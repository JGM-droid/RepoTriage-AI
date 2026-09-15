import type {
  IssueDetail,
  IssueListResponse,
  ServiceStatus,
  TriageDecision,
  TriageResult,
  TriageStartedResponse,
} from "./contracts";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

async function fetchJson<T>(
  path: string,
  errorMessage: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, init);
  if (!response.ok) {
    throw new ApiError(errorMessage, response.status);
  }
  return (await response.json()) as T;
}

export async function getHealth(): Promise<ServiceStatus> {
  return fetchJson<ServiceStatus>("/api/v1/health", "The API health check failed.");
}

export async function getIssues(): Promise<IssueListResponse> {
  return fetchJson<IssueListResponse>("/api/v1/issues", "The imported issues could not be loaded.");
}

export async function getIssue(issueId: string): Promise<IssueDetail> {
  return fetchJson<IssueDetail>(`/api/v1/issues/${issueId}`, "The imported issue could not be loaded.");
}

export async function getTriageResult(issueId: string): Promise<TriageResult> {
  return fetchJson<TriageResult>(
    `/api/v1/issues/${issueId}/triage`,
    "The deterministic triage result could not be loaded.",
  );
}

export async function startTriage(issueId: string): Promise<TriageStartedResponse> {
  return fetchJson<TriageStartedResponse>(
    `/api/v1/issues/${issueId}/triage`,
    "Deterministic triage could not be started.",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    },
  );
}

export async function submitTriageDecision(
  issueId: string,
  decision: TriageDecision,
): Promise<TriageResult> {
  return fetchJson<TriageResult>(
    `/api/v1/issues/${issueId}/triage/decision`,
    "The human decision could not be recorded.",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    },
  );
}