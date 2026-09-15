"""Provider-neutral AI gateway (Milestone 2.2).

Supplements the deterministic triage pipeline with an optional AI-generated
narrative. It never replaces classification, severity, the proposed action,
evidence, or human approval (see ADR 0003 and ADR 0008). Network/provider
code lives entirely in this package, never in `app.triage.rules`.
"""
