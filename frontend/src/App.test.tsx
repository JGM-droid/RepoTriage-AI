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
      .mockReturnValueOnce(jsonResponse(issueDetailResponse));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));

    expect(await screen.findByRole("heading", { name: "CLI crashes on Windows" })).toBeInTheDocument();
    expect(screen.getByText("The imported issue body is visible without analysis.")).toBeInTheDocument();
    expect(screen.getByText("https://github.com/pallets/flask/issues/101")).toBeInTheDocument();
  });

  it("shows issue not found when detail returns 404", async () => {
    vi.mocked(fetch)
      .mockReturnValueOnce(jsonResponse(healthResponse))
      .mockReturnValueOnce(jsonResponse(issueListResponse))
      .mockReturnValueOnce(jsonResponse({ error: "issue_not_found", message: "Issue not found." }, 404));

    render(<App />);
    fireEvent.click(await findIssueButton("CLI crashes on Windows"));

    expect(await screen.findByText("Issue not found.")).toBeInTheDocument();
  });
});