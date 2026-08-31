import { useEffect, useState } from "react";

import { getHealth } from "./api/client";
import type { ServiceStatus as ServiceStatusContract } from "./api/contracts";
import { ServiceStatus } from "./components/ServiceStatus";
import "./styles.css";

export default function App() {
  const [status, setStatus] = useState<ServiceStatusContract | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);

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

  useEffect(() => {
    void loadHealth();
  }, []);

  return (
    <main>
      <p className="eyebrow">Release 0 foundation</p>
      <h1>RepoTriage AI</h1>
      <p className="subtitle">A Governed, Evidence-Backed GitHub Issue Intelligence Platform</p>
      <p className="foundation">Runnable skeleton in progress: frontend, API, and PostgreSQL connectivity.</p>
      <ServiceStatus status={status} isLoading={isLoading} error={hasError} onRetry={loadHealth} />
    </main>
  );
}