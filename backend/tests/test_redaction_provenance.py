"""Redaction-policy provenance and historical replay tests (Milestone 2.6
correction; see ADR 0012).

Proves the exact property Milestone 2.4's prompt registry (ADR 0010)
already gave prompts, now extended to redaction: a historical analysis's
exact `rendered_prompt_hash` must remain reproducible even after the
active redaction policy changes, because the *specific* policy version
that actually ran is recorded and stays resolvable, not just pattern
names.

No test here inserts a real application secret -- every "secret" is a
synthetic, obviously fake test-only value.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

import app.ai_gateway.redaction as redaction_module
from app.ai_gateway.contracts import AIRequest
from app.ai_gateway.prompts import render_active_prompt
from app.ai_gateway.redaction import (
    RedactionPolicy,
    get_active_redaction_policy,
    get_redaction_policy,
    redact_ai_request,
)
from app.triage.rules import Assessment, Classification, EvidenceItem, ProposedAction

_SECRET = "sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"

CLASSIFICATION = Classification(
    label="bug-crash", matched_rule="crash-keyword", matched_keywords=("crash",)
)
ASSESSMENT = Assessment(severity="high", rationale="Matched rule 'crash-keyword'.")
PROPOSED_ACTION = ProposedAction(
    action="Prioritize for immediate triage.", rationale="Derived from classification."
)


def _original_request() -> AIRequest:
    """The *original*, unredacted request -- the shape reconstructable from
    persisted stage outputs (classify/assess/propose/retrieve_evidence all
    predate the AI-gateway redaction step, so this is what history actually
    retains -- see app.workflow.tasks._deserialize_stage_output)."""
    return AIRequest(
        task="triage_narrative",
        classification=CLASSIFICATION,
        evidence=(
            EvidenceItem(
                field="body",
                excerpt=f"Here is my key: {_SECRET}",
                source_url="https://example.test/1",
            ),
        ),
        assessment=ASSESSMENT,
        proposed_action=PROPOSED_ACTION,
    )


def _serialize_like_the_workflow(
    original_request: AIRequest, prompt_provenance: dict, redaction_provenance
) -> str:
    """Mimics `app.workflow.tasks._serialize_stage_output`'s use of
    `dataclasses.asdict` + `json.dumps` for stage output, and the
    provenance fields `_record_ai_routing_event` writes into `AuditEvent.
    metadata_` -- proving the recorded shape round-trips through JSON like
    real persisted records do, and that only the *original* evidence
    (never a redacted copy, never the secret-bearing provider payload) is
    what is actually durable."""
    payload = {
        "original_evidence": [asdict(item) for item in original_request.evidence],
        "classification": asdict(original_request.classification),
        "assessment": asdict(original_request.assessment),
        "proposed_action": asdict(original_request.proposed_action),
        "prompt_provenance": prompt_provenance,
        "redaction_provenance": asdict(redaction_provenance),
    }
    return json.dumps(payload)


def _deserialize(raw: str) -> dict:
    return json.loads(raw)


# --- diagnostic: the gap this correction closes -----------------------------


def test_redaction_events_alone_do_not_identify_which_policy_ran() -> None:
    """Documents the exact gap: pattern *names* alone (e.g.
    "openai_api_key") say nothing about which version of the pattern
    definitions produced them -- only the policy id/version/hash does."""
    _, provenance = redact_ai_request(_original_request())
    assert provenance.events == ("openai_api_key",)
    # The event name is stable across any future patch to the pattern's
    # regex -- it is not itself a version identifier.
    assert provenance.policy_id and provenance.policy_version and provenance.policy_hash


# --- replay: reconstruct a historical redacted request and its hash --------


def test_replaying_a_historical_analysis_reproduces_the_exact_rendered_hash() -> None:
    original_request = _original_request()

    # 1. Run through production redaction and rendering, exactly as
    #    app.ai_gateway.router.route_ai_inference does.
    redacted_request, redaction_provenance = redact_ai_request(original_request)
    rendered = render_active_prompt("triage_narrative", redacted_request)

    # 2. Persist/serialize the bounded data and provenance in the same
    #    shape the real workflow uses (original evidence + provenance
    #    only -- never the redacted text or the secret itself).
    prompt_provenance = {
        "prompt_id": rendered.prompt_id,
        "prompt_version": rendered.prompt_version,
        "prompt_status": rendered.prompt_status,
        "prompt_template_hash": rendered.prompt_template_hash,
        "rendered_prompt_hash": rendered.rendered_prompt_hash,
    }
    serialized = _serialize_like_the_workflow(
        original_request, prompt_provenance, redaction_provenance
    )
    # The *original* evidence is durably persisted unredacted elsewhere in
    # the real system too (the evidence-retrieval stage predates the
    # AI-gateway redaction step -- see app.workflow.tasks._deserialize_
    # stage_output for "retrieve_fixture_evidence"/"retrieve_related_
    # evidence") -- this milestone does not change that read-only-import
    # policy. What must never contain the secret is the provenance record
    # itself: policy/prompt provenance are hashes and pattern *names* only.
    record_for_provenance_check = _deserialize(serialized)
    assert _SECRET not in json.dumps(record_for_provenance_check["prompt_provenance"])
    assert _SECRET not in json.dumps(record_for_provenance_check["redaction_provenance"])

    # 3. Reconstruct the historical redacted request using the recorded
    #    policy version -- not "whatever policy happens to be active now".
    record = _deserialize(serialized)
    historical_policy = get_redaction_policy(
        record["redaction_provenance"]["policy_id"],
        record["redaction_provenance"]["policy_version"],
    )
    reconstructed_original = AIRequest(
        task="triage_narrative",
        classification=CLASSIFICATION,
        evidence=tuple(EvidenceItem(**item) for item in record["original_evidence"]),
        assessment=ASSESSMENT,
        proposed_action=PROPOSED_ACTION,
    )
    replayed_request, replayed_provenance = redact_ai_request(
        reconstructed_original, policy=historical_policy
    )
    replayed_rendered = render_active_prompt("triage_narrative", replayed_request)

    # 4. The exact rendered_prompt_hash reproduces.
    assert (
        replayed_rendered.rendered_prompt_hash
        == record["prompt_provenance"]["rendered_prompt_hash"]
    )
    assert replayed_provenance.events == tuple(record["redaction_provenance"]["events"])
    assert replayed_request.evidence == redacted_request.evidence


# --- historical-policy stability: a hypothetical newer version is inert ----


def test_a_hypothetical_new_policy_version_does_not_change_replay_of_1_0_0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The critical property: introducing (and even activating) a newer
    `provider_input_redaction` version must never change how a historical
    analysis recorded against `1.0.0` replays -- replay must pin the exact
    recorded version, not follow whatever is active now."""
    original_request = _original_request()

    # Record the "historical" result against the real, currently-active 1.0.0.
    redacted_request, provenance = redact_ai_request(original_request)
    rendered = render_active_prompt("triage_narrative", redacted_request)
    original_hash = rendered.rendered_prompt_hash
    assert provenance.policy_version == "1.0.0"

    # Register a hypothetical newer version with deliberately DIFFERENT
    # patterns (drops the openai_api_key pattern entirely, so if replay
    # ever silently used "whatever is active", the secret would leak
    # straight through unredacted -- a maximally observable difference).
    hypothetical_patterns = (
        (
            "github_personal_access_token",
            r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{22,}\b",
        ),
    )
    from app.ai_gateway.redaction import _policy_hash

    hypothetical_policy = RedactionPolicy(
        policy_id="provider_input_redaction",
        version="9.9.9",
        status="released",
        pattern_definitions=hypothetical_patterns,
        policy_hash=_policy_hash(hypothetical_patterns),
    )
    redaction_module._register(hypothetical_policy)
    monkeypatch.setitem(
        redaction_module.ACTIVE_REDACTION_POLICIES,
        "provider_input_redaction",
        hypothetical_policy,
    )
    try:
        # Sanity/negative control: the *active* policy really did change,
        # and really would behave differently (proving this isn't a vacuous
        # test) -- the secret is no longer redacted if the (now-active)
        # hypothetical policy is used instead of the pinned historical one.
        active_now = get_active_redaction_policy()
        assert active_now.version == "9.9.9"
        unpinned_replay, unpinned_provenance = redact_ai_request(original_request)
        assert unpinned_provenance.events == ()
        assert _SECRET in unpinned_replay.evidence[0].excerpt

        # The actual replay: explicitly resolve the exact historical
        # version by id+version, ignoring whatever is active now.
        historical_policy = get_redaction_policy("provider_input_redaction", "1.0.0")
        replayed_request, replayed_provenance = redact_ai_request(
            original_request, policy=historical_policy
        )
        replayed_rendered = render_active_prompt("triage_narrative", replayed_request)

        assert replayed_rendered.rendered_prompt_hash == original_hash
        assert replayed_provenance.policy_version == "1.0.0"
        assert replayed_provenance.events == ("openai_api_key",)
    finally:
        del redaction_module._REGISTRY[("provider_input_redaction", "9.9.9")]


# --- the no-redaction path stays explainable (Milestone 2.6, requirement 10) -


def test_no_redaction_path_still_records_a_resolvable_policy_identity() -> None:
    clean_request = AIRequest(
        task="triage_narrative",
        classification=CLASSIFICATION,
        evidence=(
            EvidenceItem(
                field="body", excerpt="Nothing sensitive here.", source_url="https://example.test/1"
            ),
        ),
        assessment=ASSESSMENT,
        proposed_action=PROPOSED_ACTION,
    )

    unchanged_request, provenance = redact_ai_request(clean_request)

    assert provenance.events == ()
    assert unchanged_request is clean_request  # no-op fast path, same object
    # Absence of events is attributable to a specific, resolvable policy --
    # not ambiguous with "no policy ran at all".
    resolved = get_redaction_policy(provenance.policy_id, provenance.policy_version)
    assert resolved.status == "released"
    assert resolved.policy_hash == provenance.policy_hash
