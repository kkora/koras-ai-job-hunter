from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import settings
from .db import bind_identity, engine
from .errors import APIError


@dataclass(frozen=True)
class Identity:
    user_id: UUID
    session_id: UUID
    workspace_id: UUID
    workspace_name: str
    email: str
    role: str


def decode_token(token: str) -> dict:
    try:
        claims = jwt.decode(token, settings().auth_jwt_secret, algorithms=["HS256"], audience="authenticated", issuer=settings().auth_issuer, options={"require": ["exp", "iat", "iss", "aud", "sub", "session_id"]})
        UUID(claims["sub"])
        UUID(claims["session_id"])
        if claims.get("role") != "authenticated":
            raise ValueError("Unsupported local auth role")
        return claims
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise APIError(401, "INVALID_SESSION", "Your session is invalid or expired.") from None


def resolve_identity(token: str, requested_workspace: str | None = None) -> Identity:
    claims = decode_token(token)
    with engine().connect() as connection:
        rows = connection.execute(text("SELECT * FROM app.resolve_identity(CAST(:uid AS uuid), CAST(:sid AS uuid))"), {"uid": claims["sub"], "sid": claims["session_id"]}).mappings().all()
    if not rows:
        raise APIError(401, "SESSION_REVOKED", "Your session is no longer active.")
    chosen = rows[0]
    if requested_workspace:
        try:
            wanted = UUID(requested_workspace)
        except ValueError:
            raise APIError(422, "INVALID_WORKSPACE", "Invalid workspace identifier.") from None
        matching = [row for row in rows if row["workspace_id"] == wanted]
        if not matching:
            raise APIError(403, "WORKSPACE_DENIED", "You do not have access to this workspace.")
        chosen = matching[0]
    return Identity(UUID(claims["sub"]), UUID(claims["session_id"]), chosen["workspace_id"], chosen["workspace_name"], chosen["email"], chosen["role"])


def identity(request: Request) -> Identity:
    token = request.cookies.get("kajh_session")
    if not token:
        raise APIError(401, "AUTH_REQUIRED", "Sign in to access this private workspace.")
    person = resolve_identity(token, request.headers.get("x-workspace-id"))
    request.state.identity = person
    return person


def csrf_token(session_id: UUID) -> str:
    nonce = secrets.token_hex(24)
    signature = hmac.new(settings().auth_jwt_secret.encode(), f"{session_id}:{nonce}".encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{signature}"


def require_csrf(request: Request, person: Identity) -> None:
    if request.headers.get("origin") not in settings().origins:
        raise APIError(403, "ORIGIN_DENIED", "This request did not originate from the local dashboard.")
    cookie = request.cookies.get("kajh_csrf", "")
    header = request.headers.get("x-csrf-token", "")
    try:
        nonce, signature = cookie.split(".")
        expected = hmac.new(settings().auth_jwt_secret.encode(), f"{person.session_id}:{nonce}".encode(), hashlib.sha256).hexdigest()
        valid = len(nonce) == 48 and hmac.compare_digest(cookie, header) and hmac.compare_digest(signature, expected)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise APIError(403, "CSRF_DENIED", "The security token is missing or invalid.")
def require_write(request: Request, person: Identity = Depends(identity)) -> Identity:
    require_csrf(request, person)
    if person.role == "Auditor":
        raise APIError(403, "READ_ONLY_ROLE", "Auditors have read-only access.")
    return person


def require_owner(person: Identity) -> None:
    if person.role != "Owner":
        raise APIError(403, "OWNER_REQUIRED", "Only the owner can change this setting.")


def database(person: Identity = Depends(identity)):
    with Session(engine()) as session, session.begin():
        bind_identity(session, str(person.user_id), str(person.workspace_id))
        yield session


def identity_payload(person: Identity, csrf: str | None = None) -> dict:
    return {"user": {"id": str(person.user_id), "email": person.email, "role": person.role}, "workspace": {"id": str(person.workspace_id), "name": person.workspace_name}, "csrf_token": csrf, "synthetic": True}
