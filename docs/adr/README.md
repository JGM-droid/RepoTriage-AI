# Architecture Decision Records

Architecture Decision Records (ADRs) preserve consequential project decisions, their alternatives, and their evidence. They are part of the project authority order after the rubric and execution roadmap.

## Lifecycle

- **Proposed:** under review and not yet controlling.
- **Accepted:** approved and must match the implementation.
- **Superseded:** replaced by a later ADR that names it.
- **Deferred:** acknowledged but deliberately not selected until the relevant milestone.

Create an ADR before implementing a significant architectural decision. Record alternatives and consequences, then mark it Accepted only with explicit approval. A later ADR may supersede an Accepted decision; it must identify the prior ADR and the reason. Deferred decisions must be revisited at their stated condition, not implemented speculatively.