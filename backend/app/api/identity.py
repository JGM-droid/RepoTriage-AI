"""Synthetic demo identity resolution and role authorization (Milestone
3.1 Slice 2; see ADR 0014).

SECURITY BOUNDARY -- read this before touching anything in this module:
`X-Demo-Actor-ID` is NOT production authentication. It is an unsigned,
unencrypted request header naming a persisted `Actor` row. It has no
password, session token, expiry, cryptographic signature, or any binding
to the caller beyond "this exact header value was present on this exact
request" -- anyone who knows or guesses an actor id can act as that actor.
It exists solely to make backend-enforced multi-tenancy and RBAC
demonstrable in a portfolio walkthrough without building a real identity
provider. A production deployment would replace `resolve_demo_actor`
below with verification of a signed credential (e.g. an OAuth/OIDC token)
issued by a real identity provider the server can independently verify;
nothing here should be mistaken for that, and this module must never be
described as "authentication."

Despite that boundary, the *authorization* built on top of it is real:
the server always derives organization and role from the database row
the header names, never from any client-supplied value. A request cannot
claim a different organization or role than its actor record has.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from opentelemetry import trace
from sqlalchemy.orm import Session

from app.database import get_session
from app.models.core import Actor
from app.observability import span

DEMO_ACTOR_HEADER = "X-Demo-Actor-ID"


class IdentityError(Exception):
    """Raised by the identity/authorization dependencies below. Handled by
    a dedicated FastAPI exception handler (see `app.main`) that returns
    the same flat `ErrorResponse` shape as every other error in this API
    -- never FastAPI's default `{"detail": ...}` envelope."""

    def __init__(self, status_code: int, error: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message


class AuthenticatedActor:
    """The server's own record of who is making this request -- built
    exclusively from the database row `X-Demo-Actor-ID` names, never from
    any other part of the request. Nothing about `organization_id` or
    `role` can be influenced by the client."""

    __slots__ = ("id", "organization_id", "role", "display_name")

    def __init__(self, actor: Actor) -> None:
        self.id: UUID = actor.id
        self.organization_id: UUID = actor.organization_id
        self.role: str = actor.role
        self.display_name: str = actor.display_name

    def has_role(self, *roles: str) -> bool:
        return self.role in roles


def resolve_demo_actor(
    session: Annotated[Session, Depends(get_session)],
    x_demo_actor_id: Annotated[str | None, Header(alias=DEMO_ACTOR_HEADER)] = None,
) -> AuthenticatedActor:
    """The one centralized place every protected route resolves its
    caller. Every failure here is `401`: the header is missing, malformed,
    names no actor, or names a disabled one -- deliberately
    indistinguishable from each other in status code (though not in
    message) so a caller cannot use the response to enumerate valid actor
    ids by trial and error."""
    with span("authorization.resolve_identity"):
        if not x_demo_actor_id:
            raise IdentityError(
                401, "missing_actor_header", f"The {DEMO_ACTOR_HEADER} header is required."
            )

        try:
            actor_id = UUID(x_demo_actor_id)
        except ValueError as exc:
            raise IdentityError(
                401, "malformed_actor_id", f"The {DEMO_ACTOR_HEADER} header is not a valid id."
            ) from exc

        actor = session.get(Actor, actor_id)
        if actor is None or not actor.is_enabled:
            raise IdentityError(401, "unknown_actor", "This actor is unknown or disabled.")

        current_span = trace.get_current_span()
        current_span.set_attribute("organization.id", str(actor.organization_id))
        current_span.set_attribute("actor.role", actor.role)
        return AuthenticatedActor(actor)


CurrentActor = Annotated[AuthenticatedActor, Depends(resolve_demo_actor)]


def require_role(*roles: str):
    """Returns a FastAPI dependency that resolves the current actor and
    additionally requires one of `roles`. A same-tenant actor whose role
    isn't sufficient gets `403` (they are who they say they are; they
    just can't do this) -- distinct from the `401`s above (we don't know
    who they are) and from the `404`s a scoping helper returns for a
    cross-tenant resource (see `app.api.scoping`)."""

    def _dependency(actor: CurrentActor) -> AuthenticatedActor:
        with span("authorization.require_role", attributes={"actor.role": actor.role}):
            if not actor.has_role(*roles):
                raise IdentityError(
                    403,
                    "insufficient_role",
                    "This actor's role does not permit this action.",
                )
            return actor

    return _dependency
