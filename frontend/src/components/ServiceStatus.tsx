import type { ServiceStatus as ServiceStatusContract } from "../api/contracts";

type ServiceStatusProps = {
  status: ServiceStatusContract | null;
  isLoading: boolean;
  error: boolean;
  onRetry: () => void;
};

export function ServiceStatus({ status, isLoading, error, onRetry }: ServiceStatusProps) {
  if (isLoading) {
    return <p role="status">Checking backend service...</p>;
  }

  if (error) {
    return (
      <section aria-labelledby="service-status-title">
        <h2 id="service-status-title">Backend service</h2>
        <p role="alert">The backend service is unavailable.</p>
        <button type="button" onClick={onRetry}>Retry</button>
      </section>
    );
  }

  return (
    <section aria-labelledby="service-status-title">
      <h2 id="service-status-title">Backend service</h2>
      <p role="status">Healthy: {status?.service}</p>
    </section>
  );
}