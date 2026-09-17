import type { DemoActor } from "../api/contracts";

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

  return (
    <section aria-labelledby="identity-selector-title" className="identity-selector">
      <p className="eyebrow">Demo identity (not real authentication)</p>
      <h2 id="identity-selector-title">Viewing as</h2>
      <label htmlFor="demo-actor-select">Organization / actor / role</label>
      <select
        id="demo-actor-select"
        value={selectedActorId ?? ""}
        onChange={(event) => onSelect(event.target.value)}
      >
        {actors.map((actor) => (
          <option key={actor.id} value={actor.id}>
            {actor.organization_name} — {actor.display_name} ({actor.role})
          </option>
        ))}
      </select>
      {selected ? (
        <p className="identity-summary">
          <strong>{selected.organization_name}</strong> ·{" "}
          <span className="identity-summary-actor">{selected.display_name}</span> ·{" "}
          <span className={`role-badge role-${selected.role}`}>{selected.role}</span>
        </p>
      ) : null}
    </section>
  );
}
