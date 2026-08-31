import type { ServiceStatus } from "./contracts";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export async function getHealth(): Promise<ServiceStatus> {
  const response = await fetch(`${apiBaseUrl}/api/v1/health`);
  if (!response.ok) {
    throw new Error("The API health check failed.");
  }
  return (await response.json()) as ServiceStatus;
}