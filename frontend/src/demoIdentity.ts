import type { DemoActor, DemoActorRole } from "./api/contracts";

const ORGANIZATION_DISPLAY_LABELS: Record<string, string> = {
  "Default Demo Organization": "Pallets Demo Organization",
  "Isolation Demo Organization": "Northwind Demo Organization",
};

export const ROLE_DISPLAY_LABELS: Record<DemoActorRole, string> = {
  viewer: "Viewer (read-only)",
  reviewer: "Triage Reviewer",
  administrator: "Administrator",
};

export const ROLE_DESCRIPTIONS: Record<DemoActorRole, string> = {
  viewer:
    "You have read-only access. You can review issues and completed results, but you cannot run triage or record a decision.",
  reviewer:
    "You can run triage and approve, reject, or request revisions within this organization.",
  administrator: "You have full demo access within this organization.",
};

const ORGANIZATION_DISPLAY_ORDER = [
  "Pallets Demo Organization",
  "Northwind Demo Organization",
];

const ROLE_DISPLAY_ORDER: DemoActorRole[] = ["viewer", "reviewer", "administrator"];

export function getOrganizationDisplayLabel(storedName: string): string {
  return ORGANIZATION_DISPLAY_LABELS[storedName] ?? storedName;
}

export function getDemoActorOptionLabel(actor: DemoActor): string {
  return `${getOrganizationDisplayLabel(actor.organization_name)} — ${ROLE_DISPLAY_LABELS[actor.role]}`;
}

export function sortDemoActorsForDisplay(actors: DemoActor[]): DemoActor[] {
  return [...actors].sort((left, right) => {
    const leftOrganization = getOrganizationDisplayLabel(left.organization_name);
    const rightOrganization = getOrganizationDisplayLabel(right.organization_name);
    const organizationOrder =
      ORGANIZATION_DISPLAY_ORDER.indexOf(leftOrganization) -
      ORGANIZATION_DISPLAY_ORDER.indexOf(rightOrganization);

    if (organizationOrder !== 0) {
      return organizationOrder;
    }

    return ROLE_DISPLAY_ORDER.indexOf(left.role) - ROLE_DISPLAY_ORDER.indexOf(right.role);
  });
}
