import type { TriageDecision, TriageResult } from "../api/contracts";

type IssueTriageProps = {
  result: TriageResult | null;
  isRunning: boolean;
  pollingPaused: boolean;
  error: boolean;
  hasRun: boolean;
  onRunTriage: () => void;
  onRefreshStatus: () => void;
  onDecide: (decision: TriageDecision) => void;
  decisionSubmitting: boolean;
  decisionError: boolean;
  // Milestone 3.1 Slice 2 (see ADR 0014): true only for reviewer/
  // administrator actors. Hiding these controls for a viewer is a
  // convenience, not the security boundary -- the backend independently
  // rejects the same action with 403 regardless of what the UI shows.
  canAct: boolean;
};

const DECISION_LABELS: Record<TriageDecision, string> = {
  approve: "Approve",
  reject: "Reject",
  request_revision: "Request revision",
};

const RUNNING_STATUS_LABELS: Record<string, string> = {
  queued: "Deterministic triage is queued...",
  running: "Running deterministic triage...",
  retrying: "Retrying deterministic triage after a transient failure...",
};

export function IssueTriage({
  result,
  isRunning,
  pollingPaused,
  error,
  hasRun,
  onRunTriage,
  onRefreshStatus,
  onDecide,
  decisionSubmitting,
  decisionError,
  canAct,
}: IssueTriageProps) {
  return (
    <section aria-labelledby="issue-triage-title" className="issue-triage">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Deterministic triage</p>
          <h3 id="issue-triage-title">Rule-based triage</h3>
        </div>
        {canAct ? (
          <button type="button" onClick={onRunTriage} disabled={isRunning}>
            {isRunning ? "Running..." : "Run deterministic triage"}
          </button>
        ) : (
          <p className="viewer-notice">Viewers can browse triage results but cannot start triage.</p>
        )}
      </div>

      {!hasRun && !isRunning && !error ? (
        <p>No deterministic triage has been run for this issue yet.</p>
      ) : null}

      {isRunning && !pollingPaused ? (
        <p role="status">
          {(result && RUNNING_STATUS_LABELS[result.status]) ?? "Starting deterministic triage..."}
        </p>
      ) : null}

      {isRunning && pollingPaused ? (
        <div className="polling-paused" role="status">
          <p>
            {(result && RUNNING_STATUS_LABELS[result.status]) ?? "Deterministic triage is still in progress."}
            {" "}Automatic status updates paused after 30 seconds; the workflow is still running in the
            background.
          </p>
          <button type="button" onClick={onRefreshStatus}>
            Refresh status
          </button>
        </div>
      ) : null}

      {error ? <p role="alert">Deterministic triage is unavailable.</p> : null}

      {result && result.status === "failed" ? (
        <p role="alert">Deterministic triage failed to complete.</p>
      ) : null}

      {result && result.status === "timed_out" ? (
        <p role="alert">Deterministic triage timed out before completing.</p>
      ) : null}

      {result && result.status === "completed" ? (
        <div className="triage-result">
          <p className="triage-disclaimer" role="status">
            This result is rule-based and has not been approved by a human.
          </p>

          <div>
            <h4>Evidence</h4>
            <ul>
              {result.evidence.map((item) => (
                <li key={item.field}>
                  <strong>{item.field}:</strong> {item.excerpt}
                </li>
              ))}
            </ul>
          </div>

          <div>
            <h4>System inference</h4>
            <p>
              Classification: {result.classification?.label} (rule: {result.classification?.matched_rule})
            </p>
            <p>Severity: {result.assessment?.severity}</p>
            <p>{result.assessment?.rationale}</p>
          </div>

          <div>
            <h4>Proposed action</h4>
            <p>{result.proposed_action?.action}</p>
            <p>{result.proposed_action?.rationale}</p>
          </div>

          {result.retrieved_evidence ? (
            <div className="retrieved-evidence">
              <h4>Related repository evidence</h4>
              {result.retrieved_evidence.items.length === 0 ? (
                <p role="status">
                  No related resolved issues or documentation were found in this repository for
                  this issue.
                </p>
              ) : (
                <ul>
                  {result.retrieved_evidence.items.map((item) => (
                    <li key={item.identifier}>
                      <strong>
                        {item.source_type === "issue" ? `Issue #${item.external_number}` : "Doc"}:{" "}
                        {item.title}
                      </strong>
                      <p>{item.excerpt}</p>
                      <p className="retrieved-evidence-relevance">
                        {item.relevance_explanation}
                      </p>
                      <a href={item.source_url}>{item.source_url}</a>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : null}

          {result.ai_inference ? (
            <div className="ai-inference">
              <h4>AI-generated inference</h4>
              <p className="ai-inference-disclaimer" role="status">
                Generated by AI to supplement the rule-based result above; it does not change the
                classification, severity, proposed action, or evidence.
              </p>
              <p>{result.ai_inference.narrative}</p>
              {result.ai_inference.citations.length > 0 ? (
                <p className="ai-inference-citations">
                  Cites: {result.ai_inference.citations.join(", ")}
                </p>
              ) : null}
              <p className="ai-inference-provenance">
                Provider: {result.ai_inference.provider} ({result.ai_inference.model})
                {result.ai_inference.status === "fallback"
                  ? ` — fell back to the deterministic mock adapter${
                      result.ai_inference.fallback_reason
                        ? ` (${result.ai_inference.fallback_reason})`
                        : ""
                    }`
                  : null}
              </p>
              <p className="ai-inference-prompt-provenance">
                Prompt: {result.ai_inference.prompt_id}@{result.ai_inference.prompt_version}
              </p>
            </div>
          ) : null}

          <div>
            <h4>Human review status</h4>
            <p>
              This is a rule-based recommendation only; it has no effect until a human
              reviewer records an explicit decision below.
            </p>

            {result.human_review?.recommendation_status === "proposed" && canAct ? (
              <div className="decision-controls" role="group" aria-label="Record human decision">
                <button
                  type="button"
                  onClick={() => onDecide("approve")}
                  disabled={decisionSubmitting}
                >
                  {decisionSubmitting ? "Submitting..." : "Approve"}
                </button>
                <button
                  type="button"
                  onClick={() => onDecide("reject")}
                  disabled={decisionSubmitting}
                >
                  {decisionSubmitting ? "Submitting..." : "Reject"}
                </button>
                <button
                  type="button"
                  onClick={() => onDecide("request_revision")}
                  disabled={decisionSubmitting}
                >
                  {decisionSubmitting ? "Submitting..." : "Request revision"}
                </button>
              </div>
            ) : null}

            {result.human_review?.recommendation_status === "proposed" && !canAct ? (
              <p className="viewer-notice">Viewers can browse but cannot record a human decision.</p>
            ) : null}

            {decisionError ? (
              <p role="alert">The human decision could not be recorded.</p>
            ) : null}

            {result.human_review?.decision ? (
              <p role="status">
                Recorded decision: {DECISION_LABELS[result.human_review.decision]}
                {result.human_review.decided_by ? ` by reviewer ${result.human_review.decided_by}` : ""}
                {result.human_review.decided_at
                  ? ` at ${new Date(result.human_review.decided_at).toLocaleString()}`
                  : ""}
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}
