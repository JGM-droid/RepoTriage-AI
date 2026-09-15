import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const healthResponse = { service: "repotriage-api", status: "healthy" };
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
    expect(screen.getByText("Loading imported issues...")).toBeInTheDocument();
  });

  it("shows a healthy response and imported issue list", async () => {
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(issueListResponse));

    render(<App />);

    expect(await screen.findByText("Healthy: repotriage-api")).toBeInTheDocument();
    expect(await findIssueButton("CLI crashes on Windows")).toBeInTheDocument();
    expect(screen.getByText("pallets/flask · open")).toBeInTheDocument();
  });

  it("shows an empty state when no issues are imported", async () => {
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse({ issues: [] }));

    render(<App />);

    expect(await screen.findByText("No imported issues are available yet.")).toBeInTheDocument();
  });

  it("shows service and issue API errors independently", async () => {
    vi.mocked(fetch)
      .mockRejectedValueOnce(new Error("offline"))
      .mockRejectedValueOnce(new Error("database unavailable"));

    render(<App />);

    expect(await screen.findByText("The backend service is unavailable.")).toBeInTheDocument();
    expect(await screen.findByText("Imported issues are unavailable.")).toBeInTheDocument();
  });

  it("retries after a failed request", async () => {
    vi.mocked(fetch)
      .mockRejectedValueOnce(new Error("offline"))
      .mockReturnValueOnce(jsonResponse({ issues: [] }))
      .mockReturnValueOnce(jsonResponse(healthResponse));

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));

    await waitFor(() => expect(screen.getByText("Healthy: repotriage-api")).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("opens an issue detail view", async () => {
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(issueListResponse))
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));

    expect(await screen.findByRole("heading", { name: "CLI crashes on Windows" })).toBeInTheDocument();
    expect(screen.getByText("The imported issue body is visible without analysis.")).toBeInTheDocument();
    expect(screen.getByText("https://github.com/pallets/flask/issues/101")).toBeInTheDocument();
  });

  it("shows the initial no-analysis triage state for a newly opened issue", async () => {
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(issueListResponse))
      .mockReturnValueOnce(jsonResponse(issueDetailResponse))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));

    expect(
      await screen.findByText("No deterministic triage has been run for this issue yet."),
    ).toBeInTheDocument();
  });

  it("shows a queued/running state while deterministic triage executes", async () => {
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(issueListResponse))
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
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(issueListResponse))
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
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(issueListResponse))
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
    fetchMock
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(issueListResponse))
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
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(issueListResponse))
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
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(issueListResponse))
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
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(issueListResponse))
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
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(issueListResponse))
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
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(issueListResponse))
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
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(issueListResponse))
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
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(issueListResponse))
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

  it("shows issue not found when detail returns 404", async () => {
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(issueListResponse))
      .mockReturnValueOnce(jsonResponse({ error: "issue_not_found", message: "Issue not found." }, 404))
      .mockReturnValueOnce(jsonResponse(noTriageYetResponse, 404));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));

    expect(await screen.findByText("Issue not found.")).toBeInTheDocument();
  });
});