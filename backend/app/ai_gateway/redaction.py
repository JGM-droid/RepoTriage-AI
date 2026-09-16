"""Narrow, provider-bound secret redaction (Milestone 2.6; see ADR 0012).

This is not general data-loss prevention. It exists for exactly one
scenario: untrusted issue/repository text that happens to contain a
high-confidence, well-known credential format (an OpenAI-style API key, a
GitHub personal-access token, or a PEM private-key block) must not be
forwarded to an AI provider, and the redacted value must not reappear in
model-influenced, persisted output. It deliberately does not attempt to
catch every possible secret shape -- a broad "anything KEY=value-looking"
regex would also destroy legitimate code samples and Markdown in real
issue text, which this milestone explicitly does not want. The *original*
imported issue record is not touched or treated as a DLP-protected store
by this module -- see `app.importer.sanitize` for the separate,
pre-existing bounding/sanitization policy that already applies at import
time.

This module never touches application-controlled secrets (the configured
OPENAI_API_KEY, database credentials, etc.) -- those are never placed into
an `AIRequest`'s fields in the first place (see `app.ai_gateway.contracts`),
so there is nothing here to redact for them; this module only scans
per-request, per-issue text that originated outside the application.

Milestone 2.6 correction (versioned redaction policy): redaction now runs
*inside* the same prompt-generation pipeline that Milestone 2.4's prompt
registry (ADR 0010) made reproducible -- so redaction itself needed the
same treatment. `RedactionPolicy` is an immutable, semantically-versioned,
hash-pinned set of pattern definitions, exactly mirroring
`app.ai_gateway.prompts.PromptVersion`: `__post_init__` recomputes
`policy_hash` from the actual pattern definitions and raises if it does
not match the pinned literal, so a silent pattern edit fails at import
time. Superseded policy versions stay registered (and therefore
importable, so a historical `AIResponse.redaction_policy_id/_version` can
be resolved back to the exact rules that produced it) but are never
selected at runtime -- switching the active policy is a source change,
like switching the active prompt version.

Applied once, by `app.ai_gateway.router.route_ai_inference`, to the
`AIRequest` before it reaches `render_active_prompt` or either adapter --
so the rendered prompt text, the real provider's request payload, and the
mock adapter's own narrative construction (which reads `AIRequest` fields
directly, not the rendered text) all see the same already-redacted content.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from functools import lru_cache

from app.ai_gateway.contracts import AIRequest
from app.retrieval.contracts import RetrievedRecord
from app.triage.rules import EvidenceItem

REDACTION_MARKER_PREFIX = "[REDACTED:"

STATUS_DRAFT = "draft"
STATUS_RELEASED = "released"
STATUS_RETIRED = "retired"
_VALID_STATUSES = (STATUS_DRAFT, STATUS_RELEASED, STATUS_RETIRED)

_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _policy_hash(pattern_definitions: tuple[tuple[str, str], ...]) -> str:
    canonical = "\n".join(f"{name}:{pattern}" for name, pattern in pattern_definitions)
    return _sha256(canonical)


@dataclass(frozen=True)
class RedactionPolicy:
    """One immutable, hash-pinned redaction rule set -- the redaction
    analog of `app.ai_gateway.prompts.PromptVersion`.

    `pattern_definitions` is an ordered tuple of `(pattern_name, regex_source)`
    pairs -- source strings, not compiled `re.Pattern` objects, so the
    dataclass stays hashable/comparable and the pinned `policy_hash` is
    computed from something stable and human-reviewable in a diff."""

    policy_id: str
    version: str
    status: str
    pattern_definitions: tuple[tuple[str, str], ...]
    policy_hash: str

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("RedactionPolicy.policy_id must not be empty.")
        if not _SEMVER_PATTERN.match(self.version):
            raise ValueError(
                f"RedactionPolicy.version must be a semantic version X.Y.Z: {self.version!r}"
            )
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"Unknown RedactionPolicy.status: {self.status!r}")
        if not self.pattern_definitions:
            raise ValueError(f"{self.policy_id}@{self.version}: at least one pattern is required.")
        actual_hash = _policy_hash(self.pattern_definitions)
        if actual_hash != self.policy_hash:
            raise ValueError(
                f"{self.policy_id}@{self.version} policy_hash mismatch: pinned "
                f"{self.policy_hash!r} but the pattern definitions hash to {actual_hash!r}. "
                "A released redaction policy's patterns must never change without a version "
                "bump and a matching recomputed policy_hash."
            )

    def compiled_patterns(self) -> tuple[tuple[str, re.Pattern[str]], ...]:
        return _compile_patterns(self.pattern_definitions)


@lru_cache(maxsize=None)
def _compile_patterns(
    pattern_definitions: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple((name, re.compile(source)) for name, source in pattern_definitions)


@dataclass(frozen=True)
class RedactionProvenance:
    """Which redaction policy ran and what it matched -- persisted
    alongside prompt-registry provenance on `AIResponse` so a historical
    analysis's redaction can be traced even when nothing matched (see
    `redact_ai_request`, which always returns this, never only on a hit)."""

    policy_id: str
    policy_version: str
    policy_status: str
    policy_hash: str
    # Pattern names, one entry per match (a name may repeat) -- never the
    # matched value itself. `len(events)` is the match count;
    # `set(events)` is which patterns fired.
    events: tuple[str, ...] = field(default_factory=tuple)


_REGISTRY: dict[tuple[str, str], RedactionPolicy] = {}


def _register(policy: RedactionPolicy) -> RedactionPolicy:
    key = (policy.policy_id, policy.version)
    if key in _REGISTRY:
        raise ValueError(f"Duplicate redaction policy registry key: {key!r}")
    _REGISTRY[key] = policy
    return policy


def all_registered_redaction_policies() -> tuple[RedactionPolicy, ...]:
    """Every policy version ever registered -- for inspection and
    historical replay only, never for runtime selection."""
    return tuple(_REGISTRY.values())


def get_redaction_policy(policy_id: str, version: str) -> RedactionPolicy:
    """Look up an exact, possibly-superseded policy version -- the
    mechanism a historical replay uses to reconstruct the exact rules that
    produced a past `AIResponse.redaction_policy_id/_version`, regardless
    of which policy is active now."""
    try:
        return _REGISTRY[(policy_id, version)]
    except KeyError:
        raise KeyError(f"No registered redaction policy for {policy_id}@{version}") from None


# --- provider_input_redaction ------------------------------------------------
#
# Three specific, well-known credential formats only. Order matters only in
# that PEM blocks are matched as one greedy span rather than line-by-line.
_PROVIDER_INPUT_REDACTION_1_0_0_PATTERNS: tuple[tuple[str, str], ...] = (
    ("openai_api_key", r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    (
        "github_personal_access_token",
        r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{22,}\b",
    ),
    (
        "pem_private_key",
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
        r"[\s\S]+?"
        r"-----END (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
    ),
)

PROVIDER_INPUT_REDACTION_1_0_0 = _register(
    RedactionPolicy(
        policy_id="provider_input_redaction",
        version="1.0.0",
        status=STATUS_RELEASED,
        pattern_definitions=_PROVIDER_INPUT_REDACTION_1_0_0_PATTERNS,
        policy_hash="1dda0ba71d76831fe276b2ee316a0585090e801c0734282836bdf418eb8b9800",
    )
)

ACTIVE_REDACTION_POLICIES: dict[str, RedactionPolicy] = {
    "provider_input_redaction": PROVIDER_INPUT_REDACTION_1_0_0,
}


def get_active_redaction_policy(policy_id: str = "provider_input_redaction") -> RedactionPolicy:
    try:
        return ACTIVE_REDACTION_POLICIES[policy_id]
    except KeyError:
        raise KeyError(
            f"No active redaction policy registered for policy_id={policy_id!r}"
        ) from None


def redact_secrets(text: str, policy: RedactionPolicy | None = None) -> tuple[str, tuple[str, ...]]:
    """Replace any high-confidence secret-shaped substring with a visible,
    non-revealing `[REDACTED:<pattern_name>]` marker, using `policy`
    (defaults to the active `provider_input_redaction` policy).

    Returns `(redacted_text, pattern_names_matched)` -- the marker makes the
    redaction auditable (it is visible in the rendered prompt and, if it
    reaches one, the provider's response) without revealing the original
    value; `pattern_names_matched` never contains the secret itself, only
    which known pattern fired, so it is safe to persist in an audit event.
    """
    active_policy = policy or get_active_redaction_policy()
    matched: list[str] = []
    redacted = text
    for pattern_name, pattern in active_policy.compiled_patterns():

        def _sub(match: re.Match[str], pattern_name: str = pattern_name) -> str:
            matched.append(pattern_name)
            return f"{REDACTION_MARKER_PREFIX}{pattern_name}]"

        redacted = pattern.sub(_sub, redacted)
    return redacted, tuple(matched)


def _redact_evidence(
    evidence: tuple[EvidenceItem, ...], policy: RedactionPolicy
) -> tuple[tuple[EvidenceItem, ...], tuple[str, ...]]:
    redacted_items: list[EvidenceItem] = []
    events: list[str] = []
    for item in evidence:
        redacted_excerpt, matches = redact_secrets(item.excerpt, policy)
        if matches:
            events.extend(matches)
            redacted_items.append(replace(item, excerpt=redacted_excerpt))
        else:
            redacted_items.append(item)
    return tuple(redacted_items), tuple(events)


def _redact_retrieved_context(
    retrieved_context: tuple[RetrievedRecord, ...], policy: RedactionPolicy
) -> tuple[tuple[RetrievedRecord, ...], tuple[str, ...]]:
    redacted_records: list[RetrievedRecord] = []
    events: list[str] = []
    for record in retrieved_context:
        redacted_title, title_matches = redact_secrets(record.title, policy)
        redacted_excerpt, excerpt_matches = redact_secrets(record.excerpt, policy)
        matches = title_matches + excerpt_matches
        if matches:
            events.extend(matches)
            redacted_records.append(replace(record, title=redacted_title, excerpt=redacted_excerpt))
        else:
            redacted_records.append(record)
    return tuple(redacted_records), tuple(events)


def redact_ai_request(
    request: AIRequest, policy: RedactionPolicy | None = None
) -> tuple[AIRequest, RedactionProvenance]:
    """Redact the only two `AIRequest` fields that can carry raw,
    third-party-authored text: `evidence` (the current issue's own
    title/body excerpts) and `retrieved_context` (past issues' excerpts
    pulled in by retrieval). `classification`/`assessment`/`proposed_action`
    are app-generated deterministic strings, never raw issue text, and are
    left untouched.

    `policy` defaults to the active `provider_input_redaction` policy; a
    caller reconstructing a historical analysis passes the exact recorded
    `RedactionPolicy` (via `get_redaction_policy`) instead, so replay is not
    silently affected by a later policy-version change.

    Always returns a `RedactionProvenance`, even when nothing matched, so
    the *absence* of a redaction event is still attributable to a specific,
    identifiable policy version -- never ambiguous between "no policy ran"
    and "the policy ran and found nothing." Returns the original `request`
    object unchanged when nothing matched, so the common case does no extra
    allocation."""
    active_policy = policy or get_active_redaction_policy()
    redacted_evidence, evidence_events = _redact_evidence(request.evidence, active_policy)
    redacted_retrieved, retrieved_events = _redact_retrieved_context(
        request.retrieved_context, active_policy
    )
    events = evidence_events + retrieved_events
    provenance = RedactionProvenance(
        policy_id=active_policy.policy_id,
        policy_version=active_policy.version,
        policy_status=active_policy.status,
        policy_hash=active_policy.policy_hash,
        events=events,
    )
    if not events:
        return request, provenance
    return (
        replace(request, evidence=redacted_evidence, retrieved_context=redacted_retrieved),
        provenance,
    )
