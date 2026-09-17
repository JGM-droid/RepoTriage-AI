import type { DemoActor } from "../api/contracts";
import {
  getDemoActorOptionLabel,
  getOrganizationDisplayLabel,
  ROLE_DESCRIPTIONS,
  ROLE_DISPLAY_LABELS,
  sortDemoActorsForDisplay,
} from "../demoIdentity";

type IdentitySelectorProps = {
  actors: DemoActor[];
  selectedActorId: string | null;
  isLoading: boolean;
  error: boolean;
  onSelect: (actorId: string) => void;
};

// Synthetic demo identity selector (Milestone 3.1 Slice 2; see ADR 0014).
// Deliberately labeled as a demo mechanism, not a login: there is no
// password or session here, only a deterministic actor id the backend
// looks up and trusts for organization/role on every request.
export function IdentitySelector({
  actors,
  selectedActorId,
  isLoading,
  error,
  onSelect,
}: IdentitySelectorProps) {
  if (isLoading) {
    return <p role="status">Loading demo identities...</p>;
  }

  if (error) {
    return <p role="alert">Demo identities are unavailable.</p>;
  }

  if (actors.length === 0) {
    return null;
  }

  const selected = actors.find((actor) => actor.id === selectedActorId) ?? null;
  const displayedActors = sortDemoActorsForDisplay(actors);

  return (
    <section aria-labelledby="identity-selector-title" className="identity-selector">
      <p className="eyebrow">Simulated access</p>
      <h2 id="identity-selector-title">Demo user</h2>
      <p>
        Select a simulated user to demonstrate organization-level access and permissions. These are
        not real sign-in accounts.
      </p>
      <label htmlFor="demo-actor-select">Select a demo user</label>
      <select
        id="demo-actor-select"
        value={selectedActorId ?? ""}
        onChange={(event) => onSelect(event.target.value)}
      >
        {displayedActors.map((actor) => (
          <option key={actor.id} value={actor.id}>
            {getDemoActorOptionLabel(actor)}
          </option>
        ))}
      </select>
      {selected ? (
        <p className="identity-summary">
          <strong>{getOrganizationDisplayLabel(selected.organization_name)}</strong> ·{" "}
          <span className="identity-summary-actor">{selected.display_name}</span> ·{" "}
          <span className={`role-badge role-${selected.role}`}>
            {ROLE_DISPLAY_LABELS[selected.role]}
          </span>
        </p>
      ) : null}
      {selected ? <p className="role-description">{ROLE_DESCRIPTIONS[selected.role]}</p> : null}
    </section>
  );
}
