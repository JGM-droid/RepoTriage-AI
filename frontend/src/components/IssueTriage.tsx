import type { TriageDecision, TriageResult } from "../api/contracts";

type IssueTriageProps = {
  result: TriageResult | null;
  isRunning: boolean;
  error: boolean;
  hasRun: boolean;
  onRunTriage: () => void;
  onDecide: (decision: TriageDecision) => void;
  decisionSubmitting: boolean;
  decisionError: boolean;
};

const DECISION_LABELS: Record<TriageDecision, string> = {
  approve: "Approve",
  reject: "Reject",
  request_revision: "Request revision",
};

export function IssueTriage({
  result,
  isRunning,
  error,
  hasRun,
  onRunTriage,
  onDecide,
  decisionSubmitting,
  decisionError,
}: IssueTriageProps) {
  return (
    <section aria-labelledby="issue-triage-title" className="issue-triage">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Deterministic triage</p>
          <h3 id="issue-triage-title">Rule-based triage</h3>
        </div>
        <button type="button" onClick={onRunTriage} disabled={isRunning}>
          {isRunning ? "Running..." : "Run deterministic triage"}
        </button>
      </div>

      {!hasRun && !isRunning && !error ? (
        <p>No deterministic triage has been run for this issue yet.</p>
      ) : null}

      {isRunning ? <p role="status">Running deterministic triage...</p> : null}

      {error ? <p role="alert">Deterministic triage is unavailable.</p> : null}

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

          <div>
            <h4>Human review status</h4>
            <p>
              This is a rule-based recommendation only; it has no effect until a human
              reviewer records an explicit decision below.
            </p>

            {result.human_review?.recommendation_status === "proposed" ? (
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

      {result && result.status === "failed" ? (
        <p role="alert">Deterministic triage failed to complete.</p>
      ) : null}
    </section>
  );
}
