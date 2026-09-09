import { useEffect, useState } from "react";

import { ApiError, getHealth, getIssue, getIssues } from "./api/client";
import type {
  IssueDetail,
  IssueListItem,
  ServiceStatus as ServiceStatusContract,
} from "./api/contracts";
import { ServiceStatus } from "./components/ServiceStatus";
import "./styles.css";

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

  async function openIssue(issueId: string) {
    setSelectedIssueId(issueId);
    setSelectedIssue(null);
    setDetailError(null);
    setDetailLoading(true);
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
  }

  useEffect(() => {
    void loadHealth();
    void loadIssues();
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
    </main>
  );
}