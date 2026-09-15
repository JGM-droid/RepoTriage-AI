"""Repository-grounded retrieval (Milestone 2.3).

Retrieves bounded, same-repository, resolved/authorized evidence (closed
issues and documentation excerpts) using pgvector similarity search, and
supplies it only to the `ai_inference` stage's prompt. Never influences
`classify`/`assess`/`propose` or the human-approval boundary (see ADR 0003,
ADR 0008, ADR 0009).
"""
