"""Unit tests for structural validation of raw issue records."""

import pytest

from app.importer.schemas import ImportValidationError, parse_raw_record

VALID_PAYLOAD = {
    "external_number": 1,
    "title": "Title",
    "body": "Body",
    "state": "open",
    "source_url": "https://example.test/issues/1",
}


def test_parse_raw_record_accepts_a_well_formed_payload() -> None:
    record = parse_raw_record(VALID_PAYLOAD)

    assert record.external_number == 1
    assert record.state == "open"
    assert record.is_pull_request is False


@pytest.mark.parametrize("missing_field", ["external_number", "title", "state", "source_url"])
def test_parse_raw_record_rejects_missing_required_fields(missing_field: str) -> None:
    payload = {key: value for key, value in VALID_PAYLOAD.items() if key != missing_field}

    with pytest.raises(ImportValidationError):
        parse_raw_record(payload)


def test_parse_raw_record_rejects_non_positive_issue_numbers() -> None:
    payload = {**VALID_PAYLOAD, "external_number": 0}

    with pytest.raises(ImportValidationError):
        parse_raw_record(payload)


def test_parse_raw_record_rejects_unrecognized_state() -> None:
    payload = {**VALID_PAYLOAD, "state": "merged"}

    with pytest.raises(ImportValidationError):
        parse_raw_record(payload)


def test_parse_raw_record_identifies_pull_requests() -> None:
    payload = {**VALID_PAYLOAD, "is_pull_request": True}

    record = parse_raw_record(payload)

    assert record.is_pull_request is True
