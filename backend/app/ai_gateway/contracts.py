"""Provider-neutral request/response contracts for the AI gateway.

`AIRequest` carries only bounded, already-sanitized, already-deterministic
values computed by earlier workflow stages — nothing raw, nothing newly
retrieved. `AIResponse` is validated before it is ever persisted or
returned to the router's caller.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.triage.rules import Assessment, Classification, EvidenceItem, ProposedAction

PROMPT_NAME = "triage_narrative_v1"

STATUS_SUCCEEDED = "succeeded"
STATUS_FALLBACK = "fallback"
_VALID_STATUSES = (STATUS_SUCCEEDED, STATUS_FALLBACK)


@dataclass(frozen=True)
class AIRequest:
    """The bounded input sent to an AI provider adapter for one analysis."""

    task: str
    classification: Classification
    evidence: tuple[EvidenceItem, ...]
    assessment: Assessment
    proposed_action: ProposedAction

    def __post_init__(self) -> None:
        if not self.task:
            raise ValueError("AIRequest.task must not be empty.")
        if not isinstance(self.evidence, tuple):
            raise TypeError("AIRequest.evidence must be a tuple of EvidenceItem.")


@dataclass(frozen=True)
class AIResponse:
    """The validated result of one AI-gateway call: real, mock, or fallback."""

    narrative: str
    status: str
    provider: str
    model: str
    prompt_name: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    latency_ms: float
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.narrative or not self.narrative.strip():
            raise ValueError("AIResponse.narrative must not be empty.")
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"Unknown AIResponse status: {self.status!r}")
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("AIResponse token counts must not be negative.")
        if self.estimated_cost_usd < 0:
            raise ValueError("AIResponse.estimated_cost_usd must not be negative.")
        if self.latency_ms < 0:
            raise ValueError("AIResponse.latency_ms must not be negative.")


class AIGatewayConfigurationError(RuntimeError):
    """Invalid provider configuration (e.g. openai selected with no API key).

    Never caught as a provider failure and never turned into a fallback —
    it must fail loudly so a misconfigured deployment is visible rather
    than silently masked as a normal, working run.
    """


class ProviderCallFailed(RuntimeError):
    """A controlled, expected adapter failure: timeout, HTTP error, or a
    malformed/missing response. Always safe for the router to catch and
    turn into a mock-adapter fallback."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
