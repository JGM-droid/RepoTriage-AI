"""Durable asynchronous triage workflow execution (Milestone 2.1).

See docs/adr/0007-durable-async-workflow-technology.md. PostgreSQL is the
canonical workflow-state store; Celery/Redis are execution plumbing only.
"""
