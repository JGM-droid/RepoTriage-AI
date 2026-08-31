import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

describe("App", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a loading state", () => {
    vi.mocked(fetch).mockReturnValue(new Promise(() => {}));

    render(<App />);

    expect(screen.getByText("Checking backend service...")).toBeInTheDocument();
  });

  it("shows a healthy response", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ service: "repotriage-api", status: "healthy" }), { status: 200 }));

    render(<App />);

    expect(await screen.findByText("Healthy: repotriage-api")).toBeInTheDocument();
  });

  it("shows an error when the request fails", async () => {
    vi.mocked(fetch).mockRejectedValue(new Error("offline"));

    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent("The backend service is unavailable.");
  });

  it("retries after a failed request", async () => {
    vi.mocked(fetch)
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(new Response(JSON.stringify({ service: "repotriage-api", status: "healthy" }), { status: 200 }));

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));

    await waitFor(() => expect(screen.getByText("Healthy: repotriage-api")).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});