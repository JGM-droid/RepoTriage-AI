import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  getDemoActors,
  getHealth,
  getIssue,
  getIssues,
  getTriageResult,
  startTriage,
  submitTriageDecision,
} from "./api/client";
import type {
  DemoActor,
  IssueDetail,
  IssueListItem,
  ServiceStatus as ServiceStatusContract,
  TriageDecision,
  TriageResult,
} from "./api/contracts";
import { IdentitySelector } from "./components/IdentitySelector";
import { IssueTriage } from "./components/IssueTriage";
import { ServiceStatus } from "./components/ServiceStatus";
import "./styles.css";

const POLL_INTERVAL_MS = 1000;
const MAX_POLL_ATTEMPTS = 30;
const TERMINAL_STATUSES = new Set(["completed", "failed", "timed_out"]);
const ACTOR_ROLES_THAT_CAN_ACT = new Set(["reviewer", "administrator"]);

export default function App() {
  const [status, setStatus] = useState<ServiceStatusContract | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);

  // Demo identity (Milestone 3.1 Slice 2; see ADR 0014) -- not real
  // authentication. actorsUnavailable distinguishes "demo mode is
  // disabled on this deployment" (an expected, honestly-displayed state,
  // not an error) from a genuine fetch failure.
  const [actors, setActors] = useState<DemoActor[]>([]);
  const [actorsLoading, setActorsLoading] = useState(true);
  const [actorsError, setActorsError] = useState(false);
  const [actorsUnavailable, setActorsUnavailable] = useState(false);
  const [selectedActorId, setSelectedActorId] = useState<string | null>(null);

  const [issues, setIssues] = useState<IssueListItem[]>([]);
  const [issuesLoading, setIssuesLoading] = useState(false);
  const [issuesError, setIssuesError] = useState(false);
  const [issuesUnauthorized, setIssuesUnauthorized] = useState(false);
  const [selectedIssueId, setSelectedIssueId] = useState<string | null>(null);
  const [selectedIssue, setSelectedIssue] = useState<IssueDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<"not-found" | "error" | null>(null);
  const [triageResult, setTriageResult] = useState<TriageResult | null>(null);
  const [triageHasRun, setTriageHasRun] = useState(false);
  // True whenever the workflow itself is non-terminal on the backend, so
  // "Run deterministic triage" must stay disabled — this stays true even
  // after automatic polling pauses at the attempt cap; only a terminal
  // result (or a hard error) clears it.
  const [triageRunning, setTriageRunning] = useState(false);
  // True only when automatic polling stopped because it hit MAX_POLL_ATTEMPTS
  // while the backend still reported a non-terminal status. It never starts
  // another workflow; it only offers a manual, GET-only status refresh.
  const [pollingPaused, setPollingPaused] = useState(false);
  const [triageError, setTriageError] = useState(false);
  const [decisionSubmitting, setDecisionSubmitting] = useState(false);
  const [decisionError, setDecisionError] = useState(false);
  const pollIntervalRef = useRef<number | null>(null);
  const pollIssueIdRef = useRef<string | null>(null);
  const pollAttemptsRef = useRef(0);

  async function loadHealth() {
    setIsLoading(true);
    setHasError(false);
    try {
      setStatus(await getHealth());
    } catch {
      setStatus(null);
      setHasError(true);
    } finally {
      setIsLoading(false);
    }
  }

  async function loadActors() {
    setActorsLoading(true);
    setActorsError(false);
    setActorsUnavailable(false);
    try {
      const response = await getDemoActors();
      setActors(response.actors);
      if (response.actors.length > 0) {
        setSelectedActorId(response.actors[0].id);
        await loadIssues(response.actors[0].id);
      }
    } catch (error) {
      setActors([]);
      if (error instanceof ApiError && error.status === 404) {
        // Demo mode is disabled on this deployment -- an expected,
        // honestly-displayed state, not a fetch error.
        setActorsUnavailable(true);
      } else {
        setActorsError(true);
      }
    } finally {
      setActorsLoading(false);
    }
  }

  function clearTenantScopedState() {
    // Milestone 3.1 Slice 2: every piece of state that could show
    // another organization's data must be cleared before a switched
    // actor's own data is fetched -- never left visible even
    // momentarily.
    stopPolling();
    setIssues([]);
    setIssuesError(false);
    setIssuesUnauthorized(false);
    setSelectedIssueId(null);
    setSelectedIssue(null);
    setDetailError(null);
    resetTriageState();
  }

  async function loadIssues(actorId: string) {
    setIssuesLoading(true);
    setIssuesError(false);
    setIssuesUnauthorized(false);
    try {
      const response = await getIssues(actorId);
      setIssues(response.issues);
    } catch (error) {
      setIssues([]);
      if (error instanceof ApiError && error.status === 401) {
        setIssuesUnauthorized(true);
      } else {
        setIssuesError(true);
      }
    } finally {
      setIssuesLoading(false);
    }
  }

  async function selectActor(actorId: string) {
    if (actorId === selectedActorId) {
      return;
    }
    setSelectedActorId(actorId);
    clearTenantScopedState();
    await loadIssues(actorId);
  }

  function resetTriageState() {
    stopPolling();
    setTriageResult(null);
    setTriageHasRun(false);
    setTriageRunning(false);
    setPollingPaused(false);
    setTriageError(false);
    setDecisionSubmitting(false);
    setDecisionError(false);
  }

  function stopPolling() {
    pollIssueIdRef.current = null;
    if (pollIntervalRef.current !== null) {
      window.clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  }

  async function pollTriageResult(actorId: string, issueId: string) {
    if (pollIssueIdRef.current !== issueId) {
      return;
    }
    pollAttemptsRef.current += 1;
    try {
      const result = await getTriageResult(actorId, issueId);
      if (pollIssueIdRef.current !== issueId) {
        return;
      }
      setTriageResult(result);
      setTriageHasRun(true);
      if (TERMINAL_STATUSES.has(result.status)) {
        setTriageRunning(false);
        setPollingPaused(false);
        stopPolling();
      } else if (pollAttemptsRef.current >= MAX_POLL_ATTEMPTS) {
        // Stop automatic polling, but the workflow is still non-terminal on
        // the backend: leave "Run deterministic triage" disabled and offer
        // a manual, GET-only refresh instead of silently giving up.
        setPollingPaused(true);
        stopPolling();
      }
    } catch {
      if (pollIssueIdRef.current !== issueId) {
        return;
      }
      setTriageError(true);
      setTriageRunning(false);
      setPollingPaused(false);
      stopPolling();
    }
  }

  function startPolling(actorId: string, issueId: string) {
    stopPolling();
    setPollingPaused(false);
    pollIssueIdRef.current = issueId;
    pollAttemptsRef.current = 0;
    void pollTriageResult(actorId, issueId);
    pollIntervalRef.current = window.setInterval(
      () => void pollTriageResult(actorId, issueId),
      POLL_INTERVAL_MS,
    );
  }

  function refreshTriageStatus() {
    // Manual, GET-only resumption of polling after the attempt cap paused
    // it. This must never start a new workflow — it only restarts the same
    // read-only status loop that `startPolling` already performs.
    if (!selectedIssueId || !selectedActorId) {
      return;
    }
    startPolling(selectedActorId, selectedIssueId);
  }

  async function openIssue(issueId: string) {
    if (!selectedActorId) {
      return;
    }
    const actorId = selectedActorId;
    setSelectedIssueId(issueId);
    setSelectedIssue(null);
    setDetailError(null);
    setDetailLoading(true);
    resetTriageState();
    try {
      setSelectedIssue(await getIssue(actorId, issueId));
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        setDetailError("not-found");
      } else {
        setDetailError("error");
      }
    } finally {
      setDetailLoading(false);
    }

    try {
      const existing = await getTriageResult(actorId, issueId);
      setTriageResult(existing);
      setTriageHasRun(true);
      if (!TERMINAL_STATUSES.has(existing.status)) {
        setTriageRunning(true);
        startPolling(actorId, issueId);
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        setTriageHasRun(false);
      } else {
        setTriageError(true);
      }
    }
  }

  async function runTriage() {
    if (!selectedIssueId || !selectedActorId) {
      return;
    }
    const actorId = selectedActorId;
    setTriageRunning(true);
    setTriageError(false);
    try {
      await startTriage(actorId, selectedIssueId);
      setTriageHasRun(true);
      startPolling(actorId, selectedIssueId);
    } catch {
      setTriageError(true);
      setTriageRunning(false);
    }
  }

  async function decideTriage(decision: TriageDecision) {
    if (!selectedIssueId || !selectedActorId) {
      return;
    }
    setDecisionSubmitting(true);
    setDecisionError(false);
    try {
      setTriageResult(await submitTriageDecision(selectedActorId, selectedIssueId, decision));
    } catch {
      setDecisionError(true);
    } finally {
      setDecisionSubmitting(false);
    }
  }

  useEffect(() => {
    void loadHealth();
    void loadActors();
    return () => stopPolling();
  }, []);

  const selectedActor = actors.find((actor) => actor.id === selectedActorId) ?? null;
  const canAct = selectedActor !== null && ACTOR_ROLES_THAT_CAN_ACT.has(selectedActor.role);

  return (
    <main>
      <p className="eyebrow">Release 3 — multi-tenancy and roles</p>
      <h1>RepoTriage AI</h1>
      <p className="subtitle">A Governed, Evidence-Backed GitHub Issue Intelligence Platform</p>
      <p className="foundation">Browse imported GitHub issues from the bounded offline fixture.</p>
      <ServiceStatus status={status} isLoading={isLoading} error={hasError} onRetry={loadHealth} />

      <IdentitySelector
        actors={actors}
        selectedActorId={selectedActorId}
        isLoading={actorsLoading}
        error={actorsError}
        onSelect={(actorId) => void selectActor(actorId)}
      />

      {actorsUnavailable ? (
        <p role="status" className="demo-mode-disabled">
          Demo identity mode is disabled on this deployment, so no protected data can be shown here.
        </p>
      ) : null}

      {selectedActorId ? (
        <section aria-labelledby="issues-title" className="issue-browser">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Imported issues</p>
              <h2 id="issues-title">Issue browser</h2>
            </div>
            <button type="button" onClick={() => void loadIssues(selectedActorId)}>Refresh</button>
          </div>

          {issuesLoading ? <p role="status">Loading imported issues...</p> : null}

          {issuesUnauthorized ? (
            <p role="alert">Your demo identity could not be verified. Try selecting an identity again.</p>
          ) : null}

          {issuesError ? (
            <p role="alert">Imported issues are unavailable.</p>
          ) : null}

          {!issuesLoading && !issuesError && !issuesUnauthorized && issues.length === 0 ? (
            <p>No imported issues are available yet for this organization.</p>
          ) : null}

          {!issuesLoading && !issuesError && !issuesUnauthorized && issues.length > 0 ? (
            <div className="issue-layout">
              <ol className="issue-list" aria-label="Imported issue list">
                {issues.map((issue) => (
                  <li key={issue.id}>
                    <button
                      type="button"
                      className={issue.id === selectedIssueId ? "issue-card selected" : "issue-card"}
                      onClick={() => void openIssue(issue.id)}
                    >
                      <span className="issue-number">#{issue.external_number}</span>
                      <span className="issue-title">{issue.title}</span>
                      <span className="issue-meta">{issue.repository.name} · {issue.state}</span>
                    </button>
                  </li>
                ))}
              </ol>

              <article className="issue-detail" aria-labelledby="issue-detail-title">
                <h3 id="issue-detail-title">Issue detail</h3>
                {!selectedIssueId ? <p>Select an imported issue to inspect its stored metadata.</p> : null}
                {detailLoading ? <p role="status">Loading issue detail...</p> : null}
                {detailError === "not-found" ? <p role="alert">Issue not found.</p> : null}
                {detailError === "error" ? <p role="alert">Issue detail is unavailable.</p> : null}
                {selectedIssue ? (
                  <div>
                    <p className="issue-number">#{selectedIssue.external_number}</p>
                    <h4>{selectedIssue.title}</h4>
                    <dl>
                      <div>
                        <dt>State</dt>
                        <dd>{selectedIssue.state}</dd>
                      </div>
                      <div>
                        <dt>Repository</dt>
                        <dd>{selectedIssue.repository.name}</dd>
                      </div>
                      <div>
                        <dt>Repository source</dt>
                        <dd><a href={selectedIssue.repository.source_url}>{selectedIssue.repository.source_url}</a></dd>
                      </div>
                      <div>
                        <dt>Issue source</dt>
                        <dd><a href={selectedIssue.source_url}>{selectedIssue.source_url}</a></dd>
                      </div>
                      <div>
                        <dt>Imported</dt>
                        <dd>{new Date(selectedIssue.created_at).toLocaleString()}</dd>
                      </div>
                      <div>
                        <dt>Updated</dt>
                        <dd>{new Date(selectedIssue.updated_at).toLocaleString()}</dd>
                      </div>
                    </dl>
                    <p className="issue-body">{selectedIssue.body}</p>
                  </div>
                ) : null}
              </article>
            </div>
          ) : null}
        </section>
      ) : null}

      {selectedIssue ? (
        <IssueTriage
          result={triageResult}
          isRunning={triageRunning}
          pollingPaused={pollingPaused}
          error={triageError}
          hasRun={triageHasRun}
          onRunTriage={() => void runTriage()}
          onRefreshStatus={refreshTriageStatus}
          onDecide={(decision) => void decideTriage(decision)}
          decisionSubmitting={decisionSubmitting}
          decisionError={decisionError}
          canAct={canAct}
        />
      ) : null}
    </main>
  );
}
