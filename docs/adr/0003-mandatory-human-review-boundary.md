# ADR 0003: Mandatory Human-Review Boundary

**Status:** Accepted
**Date:** 2026-08-31

## Context

Issue triage may involve incomplete or adversarial text and affects external repository workflows. The rubric requires a true human approval gate.

## Decision

AI output is always proposed, not approved. No output may silently become a final action. The system will not automatically comment on, close, modify, or approve third-party repository content. Backend-enforced authorization will govern human decisions.

## Alternatives considered

Autonomous external action and client-only approval checks are rejected because they violate the product boundary and cannot satisfy the no-silent-action requirement.

## Consequences

The workflow must pause for an explicit authorized human decision, and audit records must distinguish recommendation from decision.

## Security implications

Treat issue and retrieved text as untrusted. Enforce authorization on the backend and protect against prompt injection, unauthorized action, and cross-tenant access.

## Testing/evidence

Required negative tests must prove analysis completion cannot approve an action, unauthorized users cannot decide, and external mutation paths are denied or absent.

## Revisit conditions

Revisit only through an explicitly approved ADR; any change must still satisfy G3 and the human-approval rubric requirement.