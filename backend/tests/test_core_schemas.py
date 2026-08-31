from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import BaseModel

from app.models.core import Analysis, AuditEvent, HumanDecision, Issue, Recommendation, Repository
from app.schemas.core import (
    AnalysisRecord,
    AuditEventRecord,
    HumanDecisionRecord,
    IssueRecord,
    RecommendationRecord,
    RepositoryRecord,
)


@pytest.mark.parametrize(
    ("record_type", "instance"),
    [
        (
            RepositoryRecord,
            Repository(
                id=uuid4(),
                tenant_id=uuid4(),
                name="example",
                source_url="https://github.com/example/repository",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
        ),
        (
            IssueRecord,
            Issue(
                id=uuid4(),
                repository_id=uuid4(),
                external_number=1,
                title="Title",
                body="Body",
                state="open",
                source_url="https://github.com/example/repository/issues/1",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
        ),
        (
            AnalysisRecord,
            Analysis(
                id=uuid4(),
                issue_id=uuid4(),
                status="completed",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
        ),
        (
            RecommendationRecord,
            Recommendation(
                id=uuid4(),
                analysis_id=uuid4(),
                status="proposed",
                content="Review the issue.",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
        ),
        (
            HumanDecisionRecord,
            HumanDecision(
                id=uuid4(),
                recommendation_id=uuid4(),
                actor_id=uuid4(),
                decision="approved",
                rationale="Matches the repository policy.",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
        ),
        (
            AuditEventRecord,
            AuditEvent(
                id=uuid4(),
                repository_id=uuid4(),
                issue_id=uuid4(),
                actor_id=uuid4(),
                event_type="issue.reviewed",
                metadata_={"source": "test"},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
        ),
    ],
)
def test_record_schema_validates_orm_compatible_data(
    record_type: type[BaseModel], instance: object
) -> None:
    record = record_type.model_validate(instance)

    assert record.id == instance.id
