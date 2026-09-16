"""Narrow, provider-bound secret redaction (Milestone 2.6; see ADR 0012).

No test here inserts a real application secret to prove it doesn't leak --
every example is a synthetic, obviously-fake test-only value. These tests
cover the pattern-matching module directly; `test_ai_gateway.py` covers its
integration into `route_ai_inference`.
"""

from __future__ import annotations

from app.ai_gateway.redaction import REDACTION_MARKER_PREFIX, redact_secrets

_OPENAI_STYLE_TEST_SECRET = "sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"
_GITHUB_CLASSIC_PAT_TEST_SECRET = "ghp_" + "b" * 36
_GITHUB_FINEGRAINED_PAT_TEST_SECRET = "github_pat_" + "c" * 30
_PEM_TEST_BLOCK = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n"
    "-----END RSA PRIVATE KEY-----"
)


def test_redacts_an_openai_style_key() -> None:
    text = f"my key is {_OPENAI_STYLE_TEST_SECRET} please help"
    redacted, events = redact_secrets(text)
    assert _OPENAI_STYLE_TEST_SECRET not in redacted
    assert events == ("openai_api_key",)
    assert f"{REDACTION_MARKER_PREFIX}openai_api_key]" in redacted


def test_redacts_a_classic_github_personal_access_token() -> None:
    text = f"token: {_GITHUB_CLASSIC_PAT_TEST_SECRET}"
    redacted, events = redact_secrets(text)
    assert _GITHUB_CLASSIC_PAT_TEST_SECRET not in redacted
    assert events == ("github_personal_access_token",)


def test_redacts_a_fine_grained_github_personal_access_token() -> None:
    text = f"token: {_GITHUB_FINEGRAINED_PAT_TEST_SECRET}"
    redacted, events = redact_secrets(text)
    assert _GITHUB_FINEGRAINED_PAT_TEST_SECRET not in redacted
    assert events == ("github_personal_access_token",)


def test_redacts_a_pem_private_key_block() -> None:
    text = f"Accidentally pasted this:\n{_PEM_TEST_BLOCK}\nplease ignore"
    redacted, events = redact_secrets(text)
    assert _PEM_TEST_BLOCK not in redacted
    assert "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu" not in redacted
    assert events == ("pem_private_key",)


def test_redacts_multiple_distinct_secrets_in_one_text() -> None:
    text = f"{_OPENAI_STYLE_TEST_SECRET} and also {_GITHUB_CLASSIC_PAT_TEST_SECRET}"
    redacted, events = redact_secrets(text)
    assert _OPENAI_STYLE_TEST_SECRET not in redacted
    assert _GITHUB_CLASSIC_PAT_TEST_SECRET not in redacted
    assert set(events) == {"openai_api_key", "github_personal_access_token"}


def test_redaction_marker_is_visible_and_auditable_without_revealing_the_secret() -> None:
    redacted, _ = redact_secrets(_OPENAI_STYLE_TEST_SECRET)
    assert redacted == f"{REDACTION_MARKER_PREFIX}openai_api_key]"
    assert "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6" not in redacted


def test_ordinary_text_with_no_secret_is_returned_unchanged() -> None:
    text = "This is a perfectly normal issue body describing a crash."
    redacted, events = redact_secrets(text)
    assert redacted == text
    assert events == ()


def test_does_not_redact_ordinary_environment_variable_style_code() -> None:
    """The narrow pattern set must not fire on generic KEY=value-looking
    code -- only the three specific high-confidence credential formats."""
    text = "export API_KEY=my-local-dev-value\nexport DATABASE_URL=postgres://localhost/db"
    redacted, events = redact_secrets(text)
    assert redacted == text
    assert events == ()


def test_does_not_redact_a_short_string_that_merely_starts_with_sk_dash() -> None:
    """Guards against an overly loose pattern: 'sk-' is a common French/
    German word fragment and abbreviation prefix; only a sufficiently long
    token-shaped suffix should match."""
    text = "the sk-8 configuration flag is unrelated to API keys"
    redacted, events = redact_secrets(text)
    assert redacted == text
    assert events == ()
