import { useEffect, useRef, useState } from "react";

import { ApiError, getHealth, getIssue, getIssues, getTriageResult, startTriage, submitTriageDecision } from "./api/client";
import type {
  IssueDetail,
  IssueListItem,
  ServiceStatus as ServiceStatusContract,
  TriageDecision,
  TriageResult,
} from "./api/contracts";
import { IssueTriage } from "./components/IssueTriage";
import { ServiceStatus } from "./components/ServiceStatus";
import "./styles.css";

const POLL_INTERVAL_MS = 1000;
const MAX_POLL_ATTEMPTS = 30;
const TERMINAL_STATUSES = new Set(["completed", "failed", "timed_out"]);

export default function App() {
  const [status, setStatus] = useState<ServiceStatusContract | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);
  const [issues, setIssues] = useState<IssueListItem[]>([]);
  const [issuesLoading, setIssuesLoading] = useState(true);
  const [issuesError, setIssuesError] = useState(false);
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

  async function loadIssues() {
    setIssuesLoading(true);
    setIssuesError(false);
    setSelectedIssueId(null);
    setSelectedIssue(null);
    setDetailError(null);
    resetTriageState();
    try {
      const response = await getIssues();
      setIssues(response.issues);
    } catch {
      setIssues([]);
      setIssuesError(true);
    } finally {
      setIssuesLoading(false);
    }
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

  async function pollTriageResult(issueId: string) {
    if (pollIssueIdRef.current !== issueId) {
      return;
    }
    pollAttemptsRef.current += 1;
    try {
      const result = await getTriageResult(issueId);
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

  function startPolling(issueId: string) {
    stopPolling();
    setPollingPaused(false);
    pollIssueIdRef.current = issueId;
    pollAttemptsRef.current = 0;
    void pollTriageResult(issueId);
    pollIntervalRef.current = window.setInterval(() => void pollTriageResult(issueId), POLL_INTERVAL_MS);
  }

  function refreshTriageStatus() {
    // Manual, GET-only resumption of polling after the attempt cap paused
    // it. This must never start a new workflow — it only restarts the same
    // read-only status loop that `startPolling` already performs.
    if (!selectedIssueId) {
      return;
    }
    startPolling(selectedIssueId);
  }

  async function openIssue(issueId: string) {
    setSelectedIssueId(issueId);
    setSelectedIssue(null);
    setDetailError(null);
    setDetailLoading(true);
    resetTriageState();
    try {
      setSelectedIssue(await getIssue(issueId));
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
      const existing = await getTriageResult(issueId);
      setTriageResult(existing);
      setTriageHasRun(true);
      if (!TERMINAL_STATUSES.has(existing.status)) {
        setTriageRunning(true);
        startPolling(issueId);
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
    if (!selectedIssueId) {
      return;
    }
    setTriageRunning(true);
    setTriageError(false);
    try {
      await startTriage(selectedIssueId);
      setTriageHasRun(true);
      startPolling(selectedIssueId);
    } catch {
      setTriageError(true);
      setTriageRunning(false);
    }
  }

  async function decideTriage(decision: TriageDecision) {
    if (!selectedIssueId) {
      return;
    }
    setDecisionSubmitting(true);
    setDecisionError(false);
    try {
      setTriageResult(await submitTriageDecision(selectedIssueId, decision));
    } catch {
      setDecisionError(true);
    } finally {
      setDecisionSubmitting(false);
    }
  }

  useEffect(() => {
    void loadHealth();
    void loadIssues();
    return () => stopPolling();
  }, []);

  return (
    <main>
      <p className="eyebrow">Release 1 foundation</p>
      <h1>RepoTriage AI</h1>
      <p className="subtitle">A Governed, Evidence-Backed GitHub Issue Intelligence Platform</p>
      <p className="foundation">Browse imported GitHub issues from the bounded offline fixture.</p>
      <ServiceStatus status={status} isLoading={isLoading} error={hasError} onRetry={loadHealth} />
      <section aria-labelledby="issues-title" className="issue-browser">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Imported issues</p>
            <h2 id="issues-title">Issue browser</h2>
          </div>
          <button type="button" onClick={loadIssues}>Refresh</button>
        </div>

        {issuesLoading ? <p role="status">Loading imported issues...</p> : null}

        {issuesError ? (
          <p role="alert">Imported issues are unavailable.</p>
        ) : null}

        {!issuesLoading && !issuesError && issues.length === 0 ? (
          <p>No imported issues are available yet.</p>
        ) : null}

        {!issuesLoading && !issuesError && issues.length > 0 ? (
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
        />
      ) : null}
    </main>
  );
}