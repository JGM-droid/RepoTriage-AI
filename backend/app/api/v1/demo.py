"""Read-only demo-actor discovery endpoint (Milestone 3.1 Slice 2; see ADR
0014).

Lets a frontend identity selector list the deterministic demo actors it
can authenticate as, without hardcoding their ids in client source. Only
registered on the app when `Settings.demo_mode_enabled` is true (see
`app.main`) -- the live demo does not enable this by default, and no
production deployment should. Discloses only what the selector needs
(organization, display name, role) and nothing a credential-bearing
endpoint would ever need to protect, since a demo actor has no credential
at all. Disabled actors are omitted: they cannot authenticate, so listing
them would only be noise.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database import get_session
from app.models.core import Actor
from app.schemas.core import DemoActorListResponse, DemoActorSummary

router = APIRouter(prefix="/demo", tags=["demo"])

get_demo_session = get_session

DemoSession = Annotated[Session, Depends(get_demo_session)]


@router.get("/actors", response_model=DemoActorListResponse)
def list_demo_actors(session: DemoSession) -> DemoActorListResponse:
    actors = (
        session.execute(
            select(Actor)
            .options(joinedload(Actor.organization))
            .where(Actor.is_enabled.is_(True))
            .order_by(Actor.organization_id, Actor.role, Actor.display_name)
        )
        .scalars()
        .all()
    )
    return DemoActorListResponse(
        actors=[
            DemoActorSummary(
                id=actor.id,
                organization_id=actor.organization_id,
                organization_name=actor.organization.name,
                display_name=actor.display_name,
                role=actor.role,
            )
            for actor in actors
        ]
    )
