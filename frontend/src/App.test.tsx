import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const healthResponse = { service: "repotriage-api", status: "healthy" };

// Milestone 3.1 Slice 2 (see ADR 0014): every test authenticates as one
// of these synthetic demo actors. The reviewer is first so it is
// auto-selected by default, preserving every pre-Slice-2 triage/decision
// test's behavior without each one needing to pick a role explicitly.
const defaultOrgReviewerActor = {
  id: "actor-default-reviewer",
  organization_id: "org-default",
  organization_name: "Default Demo Organization",
  display_name: "Default Demo Reviewer",
  role: "reviewer",
};
const defaultOrgViewerActor = {
  id: "actor-default-viewer",
  organization_id: "org-default",
  organization_name: "Default Demo Organization",
  display_name: "Default Demo Viewer",
  role: "viewer",
};
const isolationOrgAdminActor = {
  id: "actor-isolation-admin",
  organization_id: "org-isolation",
  organization_name: "Isolation Demo Organization",
  display_name: "Isolation Demo Administrator",
  role: "administrator",
};
const demoActorsResponse = {
  actors: [defaultOrgReviewerActor, defaultOrgViewerActor, isolationOrgAdminActor],
  notice: "These are synthetic demo identities for a portfolio walkthrough, not real user accounts.",
};

const issueListResponse = {
  issues: [
    {
      id: "issue-1",
      external_number: 101,
      title: "CLI crashes on Windows",
      state: "open",
      source_url: "https://github.com/pallets/flask/issues/101",
      created_at: "2026-09-09T12:00:00Z",
      updated_at: "2026-09-09T12:30:00Z",
      repository: {
        id: "repo-1",
        name: "pallets/flask",
        source_url: "https://github.com/pallets/flask",
      },
    },
  ],
};
const otherOrgIssueListResponse = {
  issues: [
    {
      id: "issue-99",
      external_number: 202,
      title: "Isolation org's own issue",
      state: "open",
      source_url: "https://github.com/acme-internal/northwind-portal/issues/202",
      created_at: "2026-09-18T12:00:00Z",
      updated_at: "2026-09-18T12:30:00Z",
      repository: {
        id: "repo-99",
        name: "acme-internal/northwind-portal",
        source_url: "https://github.com/acme-internal/northwind-portal",
      },
    },
  ],
};
const issueDetailResponse = {
  ...issueListResponse.issues[0],
  body: "The imported issue body is visible without analysis.",
};
const noTriageYetResponse = {
  error: "triage_not_found",
  message: "No deterministic triage has been run for this issue yet.",
};
const startedTriageResponse = {
  analysis_id: "analysis-1",
  issue_id: "issue-1",
  status: "queued",
  poll_url: "/api/v1/issues/issue-1/triage",
};
const runningTriageResponse = {
  analysis_id: "analysis-1",
  issue_id: "issue-1",
  status: "running",
  current_stage: "classify",
  attempt_count: 1,
  classification: null,
  evidence: [],
  assessment: null,
  proposed_action: null,
  human_review: null,
  status_history: [
    { status: "queued", created_at: "2026-09-14T12:00:00Z" },
    { status: "running", created_at: "2026-09-14T12:00:01Z" },
  ],
  stage_attempts: [],
  created_at: "2026-09-14T12:00:00Z",
  updated_at: "2026-09-14T12:00:00Z",
};
const retryingTriageResponse = {
  ...runningTriageResponse,
  status: "retrying",
  attempt_count: 1,
};
const completedTriageResponse = {
  analysis_id: "analysis-1",
  issue_id: "issue-1",
  status: "completed",
  current_stage: "human_review",
  attempt_count: 1,
  classification: { label: "bug-crash", matched_rule: "crash-keyword", matched_keywords: ["crash"] },
  evidence: [{ field: "title", excerpt: "CLI crashes on Windows", source_url: "https://github.com/pallets/flask/issues/101" }],
  assessment: { severity: "high", rationale: "Matched rule 'crash-keyword'." },
  proposed_action: { action: "Prioritize for immediate triage.", rationale: "Derived from classification." },
  retrieved_evidence: {
    items: [
      {
        identifier: "issue:5756",
        source_type: "issue",
        external_number: 5756,
        title: "404 Flask cannot find /security/login API",
        excerpt: "A related closed issue about a missing security endpoint.",
        source_url: "https://github.com/pallets/flask/issues/5756",
        similarity_score: 0.87,
        relevance_explanation: "Ranked as a related resolved issue with cosine similarity 0.870.",
      },
    ],
    query_summary: "top-3 match for: 'CLI crashes on Windows crash bug-crash high'",
    candidates_considered: 12,
    status: "ok",
    failure_reason: null,
  },
  ai_inference: {
    narrative: "Classified as 'bug-crash' with 'high' severity based on the stored evidence.",
    status: "succeeded",
    provider: "mock",
    model: "deterministic-v1",
    prompt_id: "triage_narrative",
    prompt_version: "1.0.0",
    prompt_status: "released",
    prompt_template_hash: "06e394552019bf7b8c8e437c0647d64d21ec1a5ecf81dd28c68ff9cbcee1857c",
    rendered_prompt_hash: "0".repeat(64),
    input_tokens: 0,
    output_tokens: 0,
    estimated_cost_usd: 0,
    fallback_reason: null,
    citations: ["issue:5756"],
  },
  human_review: {
    recommendation_status: "proposed",
    human_review_status: "awaiting_human_review",
    decision: null,
    decided_by: null,
    decided_at: null,
    rationale: null,
  },
  status_history: [
    { status: "queued", created_at: "2026-09-14T12:00:00Z" },
    { status: "running", created_at: "2026-09-14T12:00:01Z" },
    { status: "completed", created_at: "2026-09-14T12:00:02Z" },
  ],
  stage_attempts: [
    { stage: "classify", attempt_number: 1, status: "succeeded", error: null, created_at: "2026-09-14T12:00:01Z" },
  ],
  created_at: "2026-09-14T12:00:00Z",
  updated_at: "2026-09-14T12:00:00Z",
};
const fallbackAIInferenceTriageResponse = {
  ...completedTriageResponse,
  ai_inference: {
    ...completedTriageResponse.ai_inference,
    status: "fallback",
    provider: "mock",
    fallback_reason: "openai_timeout",
  },
};
const approvedTriageResponse = {
  ...completedTriageResponse,
  human_review: {
    recommendation_status: "approved",
    human_review_status: "decided",
    decision: "approve",
    decided_by: "00000000-0000-0000-0000-000000000001",
    decided_at: "2026-09-14T12:05:00Z",
    rationale: null,
  },
};
const failedTriageResponse = {
  analysis_id: "analysis-2",
  issue_id: "issue-1",
  status: "failed",
  current_stage: "classify",
  attempt_count: 3,
  classification: null,
  evidence: [],
  assessment: null,
  proposed_action: null,
  human_review: null,
  status_history: [
    { status: "queued", created_at: "2026-09-14T12:00:00Z" },
    { status: "running", created_at: "2026-09-14T12:00:01Z" },
    { status: "failed", created_at: "2026-09-14T12:00:02Z" },
  ],
  stage_attempts: [],
  created_at: "2026-09-14T12:00:00Z",
  updated_at: "2026-09-14T12:00:00Z",
};
const timedOutTriageResponse = {
  ...failedTriageResponse,
  status: "timed_out",
};

function jsonResponse(body: object, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

async function findIssueButton(title: string) {
  const titleElement = await screen.findByText(title);
  const button = titleElement.closest("button");
  expect(button).not.toBeNull();
  return button as HTMLButtonElement;
}

/** Mocks the standard mount sequence: health, demo actors (reviewer
 * first, auto-selected), issue list -- the prefix every pre-Slice-2 test
 * needs before its own scenario-specific mocks. */
function mockStandardMount(fetchMock: ReturnType<typeof vi.mocked<typeof fetch>>) {
  fetchMock
    .mockReturnValueOnce(jsonResponse(healthResponse))
    .mockReturnValueOnce(jsonResponse(demoActorsResponse))
    .mockReturnValueOnce(jsonResponse(issueListResponse));
}

function headerOf(call: unknown[]): Record<string, string> {
  const init = call[1] as RequestInit | undefined;
  return (init?.headers as Record<string, string>) ?? {};
}

describe("App", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows a loading state", () => {
    vi.mocked(fetch).mockReturnValue(new Promise(() => {}));

    render(<App />);

    expect(screen.getByText("Checking backend service...")).toBeInTheDocument();
    expect(screen.getByText("Loading demo identities...")).toBeInTheDocument();
  });

  it("shows a healthy response and imported issue list", async () => {
    mockStandardMount(vi.mocked(fetch));

    render(<App />);

    expect(await screen.findByText("Healthy: repotriage-api")).toBeInTheDocument();
    expect(await findIssueButton("CLI crashes on Windows")).toBeInTheDocument();
    expect(screen.getByText("pallets/flask · open")).toBeInTheDocument();
  });

  it("shows an empty state when no issues are imported", async () => {
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(demoActorsResponse))
      .mockReturnValueOnce(jsonResponse({ issues: [] }));

    render(<App />);

    expect(
      await screen.findByText("No imported issues are available yet for this organization."),
    ).toBeInTheDocument();
  });

  it("shows service and issue API errors independently", async () => {
    vi.mocked(fetch)
      .mockRejectedValueOnce(new Error("offline"))
      .mockRejectedValueOnce(new Error("demo identities unavailable"));

    render(<App />);

    expect(await screen.findByText("The backend service is unavailable.")).toBeInTheDocument();
    expect(await screen.findByText("Demo identities are unavailable.")).toBeInTheDocument();
  });

  it("shows demo-mode-disabled messaging when the demo actor endpoint is absent", async () => {
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse({ error: "not_found", message: "Not Found" }, 404));

    render(<App />);

    expect(
      await screen.findByText(
        "Demo identity mode is disabled on this deployment, so no protected data can be shown here.",
      ),
    ).toBeInTheDocument();
  });

  it("opens an issue detail view", async () => {
    mockStandardMount(vi.mocked(fetch));
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));

    expect(await screen.findByRole("heading", { name: "CLI crashes on Windows" })).toBeInTheDocument();
    expect(screen.getByText("The imported issue body is visible without analysis.")).toBeInTheDocument();
    expect(screen.getByText("https://github.com/pallets/flask/issues/101")).toBeInTheDocument();
  });

  it("shows the initial no-analysis triage state for a newly opened issue", async () => {
    mockStandardMount(vi.mocked(fetch));
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));

    expect(
      await screen.findByText("No deterministic triage has been run for this issue yet."),
    ).toBeInTheDocument();
  });

  it("shows a queued/running state while deterministic triage executes", async () => {
    mockStandardMount(vi.mocked(fetch));
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404))
      .mockReturnValueOnce(jsonResponse(startedTriageResponse, 202))
      .mockReturnValueOnce(new Promise(() => {}));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));
    await screen.findByText("No deterministic triage has been run for this issue yet.");
    fireEvent.click(screen.getByRole("button", { name: "Run deterministic triage" }));

    expect(await screen.findByText("Starting deterministic triage...")).toBeInTheDocument();
  });

  it("shows a retrying state after a transient failure, then polls to completion", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockStandardMount(vi.mocked(fetch));
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404))
      .mockReturnValueOnce(jsonResponse(startedTriageResponse, 202))
      .mockReturnValueOnce(jsonResponse(retryingTriageResponse))
      .mockReturnValueOnce(jsonResponse(completedTriageResponse));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));
    await screen.findByText("No deterministic triage has been run for this issue yet.");
    fireEvent.click(screen.getByRole("button", { name: "Run deterministic triage" }));

    expect(
      await screen.findByText("Retrying deterministic triage after a transient failure..."),
    ).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(1000);

    expect(
      await screen.findByText("This result is rule-based and has not been approved by a human."),
    ).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("stops polling once a terminal state is reached", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockStandardMount(vi.mocked(fetch));
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404))
      .mockReturnValueOnce(jsonResponse(startedTriageResponse, 202))
      .mockReturnValueOnce(jsonResponse(completedTriageResponse));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));
    await screen.findByText("No deterministic triage has been run for this issue yet.");
    fireEvent.click(screen.getByRole("button", { name: "Run deterministic triage" }));
    await screen.findByText("This result is rule-based and has not been approved by a human.");

    const callCountAfterCompletion = vi.mocked(fetch).mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);

    expect(vi.mocked(fetch).mock.calls.length).toBe(callCountAfterCompletion);
    vi.useRealTimers();
  });

  it("pauses automatic polling at the attempt cap without re-enabling Run, and Refresh status resumes polling without starting a new workflow", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = vi.mocked(fetch);
    mockStandardMount(fetchMock);
    fetchMock
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404))
      .mockReturnValueOnce(jsonResponse(startedTriageResponse, 202))
      .mockImplementation(() => jsonResponse(runningTriageResponse));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));
    await screen.findByText("No deterministic triage has been run for this issue yet.");
    fireEvent.click(screen.getByRole("button", { name: "Run deterministic triage" }));
    await screen.findByText("Running deterministic triage...");

    // 1 immediate poll + 29 more interval ticks (1s apart) reaches the
    // 30-attempt cap while the backend still reports "running".
    await vi.advanceTimersByTimeAsync(29_000);

    expect(await screen.findByText(/Automatic status updates paused/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Running..." })).toBeDisabled();

    const callsAtPause = fetchMock.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchMock.mock.calls.length).toBe(callsAtPause); // polling really stopped

    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));

    await screen.findByText("Running deterministic triage...");
    expect(screen.queryByText(/Automatic status updates paused/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Running..." })).toBeDisabled();
    expect(fetchMock.mock.calls.length).toBe(callsAtPause + 1); // exactly one GET, no new workflow

    fetchMock.mockReturnValueOnce(jsonResponse(completedTriageResponse));
    await vi.advanceTimersByTimeAsync(1000);

    expect(
      await screen.findByText("This result is rule-based and has not been approved by a human."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run deterministic triage" })).not.toBeDisabled();

    vi.useRealTimers();
  });

  it("shows the separated triage result after a completed run", async () => {
    mockStandardMount(vi.mocked(fetch));
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404))
      .mockReturnValueOnce(jsonResponse(startedTriageResponse, 202))
      .mockReturnValueOnce(jsonResponse(completedTriageResponse));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));
    await screen.findByText("No deterministic triage has been run for this issue yet.");
    fireEvent.click(screen.getByRole("button", { name: "Run deterministic triage" }));

    expect(
      await screen.findByText("This result is rule-based and has not been approved by a human."),
    ).toBeInTheDocument();
    expect(screen.getByText("Evidence")).toBeInTheDocument();
    expect(screen.getByText("System inference")).toBeInTheDocument();
    expect(screen.getByText("Proposed action")).toBeInTheDocument();
    expect(screen.getByText("Related repository evidence")).toBeInTheDocument();
    expect(screen.getByText("Issue #5756: 404 Flask cannot find /security/login API")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "https://github.com/pallets/flask/issues/5756" }),
    ).toBeInTheDocument();
    expect(screen.getByText("AI-generated inference")).toBeInTheDocument();
    expect(
      screen.getByText(completedTriageResponse.ai_inference.narrative),
    ).toBeInTheDocument();
    expect(screen.getByText("Cites: issue:5756")).toBeInTheDocument();
    expect(screen.getByText("Provider: mock (deterministic-v1)", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("Prompt: triage_narrative@1.0.0", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Request revision" })).toBeInTheDocument();
  });

  it("shows an explicit no-related-evidence message when retrieval finds nothing", async () => {
    const emptyRetrievalResponse = {
      ...completedTriageResponse,
      retrieved_evidence: {
        items: [],
        query_summary: "top-3 match for: 'an idiosyncratic issue with no overlap'",
        candidates_considered: 12,
        status: "empty",
        failure_reason: null,
      },
      ai_inference: { ...completedTriageResponse.ai_inference, citations: [] },
    };
    mockStandardMount(vi.mocked(fetch));
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404))
      .mockReturnValueOnce(jsonResponse(startedTriageResponse, 202))
      .mockReturnValueOnce(jsonResponse(emptyRetrievalResponse));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));
    await screen.findByText("No deterministic triage has been run for this issue yet.");
    fireEvent.click(screen.getByRole("button", { name: "Run deterministic triage" }));

    expect(await screen.findByText("Related repository evidence")).toBeInTheDocument();
    expect(
      screen.getByText(
        "No related resolved issues or documentation were found in this repository for this issue.",
      ),
    ).toBeInTheDocument();
  });

  it("clearly separates AI inference from the deterministic sections and shows fallback provenance", async () => {
    mockStandardMount(vi.mocked(fetch));
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404))
      .mockReturnValueOnce(jsonResponse(startedTriageResponse, 202))
      .mockReturnValueOnce(jsonResponse(fallbackAIInferenceTriageResponse));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));
    await screen.findByText("No deterministic triage has been run for this issue yet.");
    fireEvent.click(screen.getByRole("button", { name: "Run deterministic triage" }));

    await screen.findByText("This result is rule-based and has not been approved by a human.");

    // The deterministic severity/classification text and the AI narrative
    // never get merged into one blob — each section's own heading exists.
    expect(screen.getByText("System inference")).toBeInTheDocument();
    expect(screen.getByText("AI-generated inference")).toBeInTheDocument();
    expect(
      screen.getByText(/Generated by AI to supplement the rule-based result above/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/fell back to the deterministic mock adapter.*openai_timeout/),
    ).toBeInTheDocument();
  });

  it("submits a human decision and shows the recorded outcome", async () => {
    mockStandardMount(vi.mocked(fetch));
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404))
      .mockReturnValueOnce(jsonResponse(startedTriageResponse, 202))
      .mockReturnValueOnce(jsonResponse(completedTriageResponse))
      .mockReturnValueOnce(jsonResponse(approvedTriageResponse));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));
    await screen.findByText("No deterministic triage has been run for this issue yet.");
    fireEvent.click(screen.getByRole("button", { name: "Run deterministic triage" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));

    expect(await screen.findByText(/Recorded decision: Approve/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Request revision" })).not.toBeInTheDocument();
  });

  it("shows a decision submission failure without recording a decision", async () => {
    mockStandardMount(vi.mocked(fetch));
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404))
      .mockReturnValueOnce(jsonResponse(startedTriageResponse, 202))
      .mockReturnValueOnce(jsonResponse(completedTriageResponse))
      .mockRejectedValueOnce(new Error("network error"));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));
    await screen.findByText("No deterministic triage has been run for this issue yet.");
    fireEvent.click(screen.getByRole("button", { name: "Run deterministic triage" }));
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));

    expect(await screen.findByText("The human decision could not be recorded.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("shows a failed triage state", async () => {
    mockStandardMount(vi.mocked(fetch));
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404))
      .mockReturnValueOnce(jsonResponse(startedTriageResponse, 202))
      .mockReturnValueOnce(jsonResponse(failedTriageResponse));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));
    await screen.findByText("No deterministic triage has been run for this issue yet.");
    fireEvent.click(screen.getByRole("button", { name: "Run deterministic triage" }));

    expect(await screen.findByText("Deterministic triage failed to complete.")).toBeInTheDocument();
  });

  it("shows a timed-out triage state", async () => {
    mockStandardMount(vi.mocked(fetch));
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404))
      .mockReturnValueOnce(jsonResponse(startedTriageResponse, 202))
      .mockReturnValueOnce(jsonResponse(timedOutTriageResponse));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));
    await screen.findByText("No deterministic triage has been run for this issue yet.");
    fireEvent.click(screen.getByRole("button", { name: "Run deterministic triage" }));

    expect(
      await screen.findByText("Deterministic triage timed out before completing."),
    ).toBeInTheDocument();
  });

  it("shows issue not found when detail returns 404 (also the cross-tenant-guess case)", async () => {
    mockStandardMount(vi.mocked(fetch));
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse({ error: "issue_not_found", message: "Issue not found." }, 404))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));

    expect(await screen.findByText("Issue not found.")).toBeInTheDocument();
  });

  it("renders a cross-tenant 404 exactly like a genuinely unknown id, disclosing nothing about the other organization", async () => {
    // The backend's own contract (ADR 0014) makes a cross-tenant id and a
    // truly nonexistent one produce byte-identical 404 responses -- this
    // test proves the frontend doesn't introduce a disclosure the backend
    // itself avoids: no organization name, no "belongs to another
    // organization" wording, no hint the id is valid elsewhere.
    mockStandardMount(vi.mocked(fetch));
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse({ error: "issue_not_found", message: "Issue not found." }, 404))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));

    const notFoundMessage = await screen.findByText("Issue not found.");
    expect(notFoundMessage).toBeInTheDocument();
    // Scoped to the issue-detail region specifically -- the identity
    // selector legitimately always lists the isolation organization's
    // actors (that's the demo-actor-discovery feature working as
    // intended), so a page-wide search for "isolation" would be a false
    // positive. Within the detail region itself, nothing should name
    // another organization or hint the id is valid elsewhere.
    const detailRegion = screen.getByRole("article", { name: "Issue detail" });
    expect(within(detailRegion).queryByText(/isolation/i)).not.toBeInTheDocument();
    expect(within(detailRegion).queryByText(/another organization/i)).not.toBeInTheDocument();
    expect(within(detailRegion).queryByText(/belongs to/i)).not.toBeInTheDocument();
  });

  // --- Milestone 3.1 Slice 2: identity selector, headers, isolation (see ADR 0014) ---

  it("displays the selected actor's organization, display name, and role", async () => {
    mockStandardMount(vi.mocked(fetch));

    render(<App />);

    // Scoped to the summary line specifically -- the same text also
    // appears (differently formatted) inside the <select>'s <option>s.
    expect(await screen.findByText("Default Demo Organization", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("Default Demo Reviewer", { selector: ".identity-summary-actor" })).toBeInTheDocument();
    expect(screen.getByText("reviewer", { selector: ".role-badge" })).toBeInTheDocument();
  });

  it("displays the administrator actor's organization, display name, and administrator role", async () => {
    // Explicitly exercises the administrator role -- a generic
    // identity-display test using the default reviewer actor does not
    // count as proof this role renders correctly (see ADR 0014's
    // frontend acceptance requirements).
    const fetchMock = vi.mocked(fetch);
    mockStandardMount(fetchMock);

    render(<App />);
    await findIssueButton("CLI crashes on Windows");

    fetchMock.mockReturnValueOnce(jsonResponse(otherOrgIssueListResponse));
    fireEvent.change(await screen.findByLabelText("Organization / actor / role"), {
      target: { value: isolationOrgAdminActor.id },
    });

    expect(
      await screen.findByText("Isolation Demo Organization", { selector: "strong" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Isolation Demo Administrator", { selector: ".identity-summary-actor" }),
    ).toBeInTheDocument();
    expect(screen.getByText("administrator", { selector: ".role-badge" })).toBeInTheDocument();
  });

  it("attaches the X-Demo-Actor-ID header to protected requests", async () => {
    const fetchMock = vi.mocked(fetch);
    mockStandardMount(fetchMock);

    render(<App />);
    await findIssueButton("CLI crashes on Windows");

    const issuesCall = fetchMock.mock.calls.find((call) => (call[0] as string).endsWith("/api/v1/issues"));
    expect(issuesCall).toBeDefined();
    expect(headerOf(issuesCall!)["X-Demo-Actor-ID"]).toBe(defaultOrgReviewerActor.id);
  });

  it("hides reviewer-only controls and shows a viewer notice for a viewer actor", async () => {
    mockStandardMount(vi.mocked(fetch));

    render(<App />);
    await findIssueButton("CLI crashes on Windows");

    // Switch to the viewer actor in the same organization -- clears
    // state, then refetches this organization's issue list as the viewer.
    vi.mocked(fetch).mockReturnValueOnce(jsonResponse(issueListResponse));
    fireEvent.change(screen.getByLabelText("Organization / actor / role"), {
      target: { value: defaultOrgViewerActor.id },
    });
    await screen.findByText(defaultOrgViewerActor.display_name, { selector: ".identity-summary-actor" });

    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404));
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));
    await screen.findByText("No deterministic triage has been run for this issue yet.");

    expect(screen.queryByRole("button", { name: "Run deterministic triage" })).not.toBeInTheDocument();
    expect(
      screen.getByText("Viewers can browse triage results but cannot start triage."),
    ).toBeInTheDocument();
  });

  it("switching from a reviewer in Organization A to an administrator in Organization B clears all previous tenant-specific state before the new request resolves", async () => {
    const fetchMock = vi.mocked(fetch);
    mockStandardMount(fetchMock);
    fetchMock
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));
    await screen.findByRole("heading", { name: "CLI crashes on Windows" });

    // Switch to the isolation organization's administrator, whose issue
    // list is entirely different.
    fetchMock.mockReturnValueOnce(jsonResponse(otherOrgIssueListResponse));
    fireEvent.change(screen.getByLabelText("Organization / actor / role"), {
      target: { value: isolationOrgAdminActor.id },
    });

    // The previous organization's issue and its open detail view are
    // never shown, not even momentarily -- and the new organization's own
    // issue appears once its list resolves.
    await screen.findByText("Isolation org's own issue");
    expect(screen.queryByText("CLI crashes on Windows")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "CLI crashes on Windows" })).not.toBeInTheDocument();
    expect(screen.getByText("Select an imported issue to inspect its stored metadata.")).toBeInTheDocument();

    const issuesCalls = fetchMock.mock.calls.filter((call) => (call[0] as string).endsWith("/api/v1/issues"));
    const lastIssuesCall = issuesCalls[issuesCalls.length - 1];
    expect(headerOf(lastIssuesCall)["X-Demo-Actor-ID"]).toBe(isolationOrgAdminActor.id);
  });

  it("shows a safe message when the issue list request is unauthorized (401)", async () => {
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(demoActorsResponse))
      .mockReturnValueOnce(jsonResponse({ error: "unknown_actor", message: "This actor is unknown or disabled." }, 401));

    render(<App />);

    expect(
      await screen.findByText("Your demo identity could not be verified. Try selecting an identity again."),
    ).toBeInTheDocument();
  });

  it("safely handles a role-forbidden (403) triage start without crashing or misleading state", async () => {
    mockStandardMount(vi.mocked(fetch));
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404))
      .mockReturnValueOnce(
        jsonResponse({ error: "insufficient_role", message: "This actor's role does not permit this action." }, 403),
      );

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));
    await screen.findByText("No deterministic triage has been run for this issue yet.");
    fireEvent.click(screen.getByRole("button", { name: "Run deterministic triage" }));

    expect(await screen.findByText("Deterministic triage is unavailable.")).toBeInTheDocument();
  });

  it("waits for the retry click before re-fetching health and demo identities", async () => {
    vi.mocked(fetch)
      .mockRejectedValueOnce(new Error("offline"))
      .mockRejectedValueOnce(new Error("demo identities unavailable"));

    render(<App />);
    await screen.findByText("The backend service is unavailable.");

    vi.mocked(fetch).mockReturnValueOnce(jsonResponse(healthResponse));
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(screen.getByText("Healthy: repotriage-api")).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledTimes(3);
  });
});
