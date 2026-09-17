import type {
  DemoActorListResponse,
  IssueDetail,
  IssueListResponse,
  ServiceStatus,
  TriageDecision,
  TriageResult,
  TriageStartedResponse,
} from "./contracts";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// Milestone 3.1 Slice 2 (see ADR 0014): every protected request carries
// this header naming a synthetic demo actor -- NOT a credential, NOT
// production authentication. The backend derives organization and role
// solely from the database row this id names.
export const DEMO_ACTOR_HEADER = "X-Demo-Actor-ID";

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

function actorHeaders(actorId: string, extra?: Record<string, string>): Record<string, string> {
  return { [DEMO_ACTOR_HEADER]: actorId, ...extra };
}

export async function getHealth(): Promise<ServiceStatus> {
  return fetchJson<ServiceStatus>("/api/v1/health", "The API health check failed.");
}

export async function getDemoActors(): Promise<DemoActorListResponse> {
  return fetchJson<DemoActorListResponse>(
    "/api/v1/demo/actors",
    "The demo identity list could not be loaded.",
  );
}

export async function getIssues(actorId: string): Promise<IssueListResponse> {
  return fetchJson<IssueListResponse>("/api/v1/issues", "The imported issues could not be loaded.", {
    headers: actorHeaders(actorId),
  });
}

export async function getIssue(actorId: string, issueId: string): Promise<IssueDetail> {
  return fetchJson<IssueDetail>(
    `/api/v1/issues/${issueId}`,
    "The imported issue could not be loaded.",
    { headers: actorHeaders(actorId) },
  );
}

export async function getTriageResult(actorId: string, issueId: string): Promise<TriageResult> {
  return fetchJson<TriageResult>(
    `/api/v1/issues/${issueId}/triage`,
    "The deterministic triage result could not be loaded.",
    { headers: actorHeaders(actorId) },
  );
}

export async function startTriage(actorId: string, issueId: string): Promise<TriageStartedResponse> {
  return fetchJson<TriageStartedResponse>(
    `/api/v1/issues/${issueId}/triage`,
    "Deterministic triage could not be started.",
    {
      method: "POST",
      headers: actorHeaders(actorId, { "Content-Type": "application/json" }),
      body: JSON.stringify({}),
    },
  );
}

export async function submitTriageDecision(
  actorId: string,
  issueId: string,
  decision: TriageDecision,
): Promise<TriageResult> {
  return fetchJson<TriageResult>(
    `/api/v1/issues/${issueId}/triage/decision`,
    "The human decision could not be recorded.",
    {
      method: "POST",
      headers: actorHeaders(actorId, { "Content-Type": "application/json" }),
      body: JSON.stringify({ decision }),
    },
  );
}
