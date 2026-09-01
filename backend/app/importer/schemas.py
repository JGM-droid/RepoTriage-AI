"""Structural validation for raw issue records.

Every raw record, whether drawn from the committed fixture or a live
capture, must pass this structural validation before it is sanitized or
written to the database. Validation failures are reported through
`ImportValidationError`, which never includes secrets or connection
details.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

ALLOWED_STATES = {"open", "closed"}


class ImporterError(Exception):
    """Base class for all importer errors."""


class ImportValidationError(ImporterError):
    """Raised when a record is structurally malformed or fails policy."""


class ImportIntegrityError(ImporterError):
    """Raised when fixture content does not match its provenance manifest."""


class RawIssueRecord(BaseModel):
    """A single issue record before sanitization is applied."""

    model_config = ConfigDict(extra="ignore")

    external_number: int = Field(gt=0)
    title: str
    body: str = ""
    state: str
    source_url: str = Field(min_length=1)
    is_pull_request: bool = False

    @field_validator("state")
    @classmethod
    def _validate_state(cls, value: str) -> str:
        if value not in ALLOWED_STATES:
            raise ValueError(f"state must be one of {sorted(ALLOWED_STATES)}")
        return value


def parse_raw_record(payload: dict[str, Any]) -> RawIssueRecord:
    """Parse and structurally validate one raw record.

    Raises `ImportValidationError` for missing required fields, wrong
    types, or disallowed values. The original validation error is chained
    for local debugging but is not exposed to external callers.
    """
    try:
        return RawIssueRecord.model_validate(payload)
    except ValidationError as exc:
        raise ImportValidationError("issue record failed structural validation") from exc
