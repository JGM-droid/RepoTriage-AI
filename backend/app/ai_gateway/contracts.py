"""Provider-neutral request/response contracts for the AI gateway.

`AIRequest` carries only bounded, already-sanitized, already-deterministic
values computed by earlier workflow stages — nothing raw, nothing newly
retrieved. `AIResponse` is validated before it is ever persisted or
returned to the router's caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.retrieval.contracts import RetrievedRecord
from app.triage.rules import Assessment, Classification, EvidenceItem, ProposedAction

STATUS_SUCCEEDED = "succeeded"
STATUS_FALLBACK = "fallback"
_VALID_STATUSES = (STATUS_SUCCEEDED, STATUS_FALLBACK)


@dataclass(frozen=True)
class AIRequest:
    """The bounded input sent to an AI provider adapter for one analysis.

    `retrieved_context` carries only the already-selected, already-bounded
    top-k `RetrievedRecord`s chosen by the `retrieve_related_evidence`
    stage (see `app.retrieval`) — never the full corpus, and never a second
    copy of the issue being analyzed (the retrieval stage excludes it)."""

    task: str
    classification: Classification
    evidence: tuple[EvidenceItem, ...]
    assessment: Assessment
    proposed_action: ProposedAction
    retrieved_context: tuple[RetrievedRecord, ...] = ()

    def __post_init__(self) -> None:
        if not self.task:
            raise ValueError("AIRequest.task must not be empty.")
        if not isinstance(self.evidence, tuple):
            raise TypeError("AIRequest.evidence must be a tuple of EvidenceItem.")
        if not isinstance(self.retrieved_context, tuple):
            raise TypeError("AIRequest.retrieved_context must be a tuple of RetrievedRecord.")

    def allowed_citation_identifiers(self) -> frozenset[str]:
        return frozenset(record.identifier for record in self.retrieved_context)


@dataclass(frozen=True)
class AIResponse:
    """The validated result of one AI-gateway call: real, mock, or fallback.

    `citations` may only reference identifiers that were actually present
    in the request's `retrieved_context` — validated by
    `validate_citations` before an `AIResponse` referencing them is ever
    constructed from adapter output.

    `prompt_id`/`prompt_version`/`prompt_status`/`prompt_template_hash`/
    `rendered_prompt_hash` are the prompt-registry provenance for this
    result (see `app.ai_gateway.prompts`, ADR 0010) — set from the single
    `RenderedPrompt` both adapters render through, never constructed
    independently by either adapter."""

    narrative: str
    status: str
    provider: str
    model: str
    prompt_id: str
    prompt_version: str
    prompt_status: str
    prompt_template_hash: str
    rendered_prompt_hash: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    latency_ms: float
    fallback_reason: str | None = None
    citations: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.narrative or not self.narrative.strip():
            raise ValueError("AIResponse.narrative must not be empty.")
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"Unknown AIResponse status: {self.status!r}")
        if not self.prompt_id:
            raise ValueError("AIResponse.prompt_id must not be empty.")
        if not self.prompt_version:
            raise ValueError("AIResponse.prompt_version must not be empty.")
        if not self.prompt_template_hash:
            raise ValueError("AIResponse.prompt_template_hash must not be empty.")
        if not self.rendered_prompt_hash:
            raise ValueError("AIResponse.rendered_prompt_hash must not be empty.")
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("AIResponse token counts must not be negative.")
        if self.estimated_cost_usd < 0:
            raise ValueError("AIResponse.estimated_cost_usd must not be negative.")
        if self.latency_ms < 0:
            raise ValueError("AIResponse.latency_ms must not be negative.")


class InvalidCitationError(RuntimeError):
    """A provider response cited an identifier not present in the supplied
    `retrieved_context`. Treated as a controlled provider failure (like a
    malformed response) by the router, never persisted as-is."""


def validate_citations(citations: tuple[str, ...], request: AIRequest) -> None:
    allowed = request.allowed_citation_identifiers()
    unknown = [citation for citation in citations if citation not in allowed]
    if unknown:
        raise InvalidCitationError(f"unknown citation identifier(s): {unknown!r}")


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
