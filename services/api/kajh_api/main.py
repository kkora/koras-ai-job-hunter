from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry import trace
from sqlalchemy import String, func, select, text, literal_column, exists, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from kajh_domain import evaluate_eligibility

from .auth import Identity, csrf_token, database, identity, identity_payload, require_owner, require_write, resolve_identity
from .config import settings
from .db import engine
from .errors import APIError
from .models import AuditEvent, EligibilityEvidence, IdempotencyKey, Job, Opportunity, OpportunitySource, ProfileVersion, RawObservation, SavedOpportunity, SearchProfile, Source, SourcePermission, SourceRun, VendorProfile, WorkflowDecision, WorkflowRun, utcnow
from .schemas import Login, ResumeInput, ReviewInput, SavedInput, SearchInput, SearchUpdate, SourceUpdate, SplitSourceInput, VendorInput, bounded_object

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings()  # Fail closed at startup if configuration is missing or unsafe.
    yield


app = FastAPI(title="Koras AI Job Hunter", version="1.0.0-p1", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
log = logging.getLogger("kajh.api")
log.setLevel(logging.INFO)
log.propagate = False
if not any(handler.get_name() == "kajh.api.json" for handler in log.handlers):
    json_handler = logging.StreamHandler()
    json_handler.set_name("kajh.api.json")
    json_handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(json_handler)
tracer = trace.get_tracer("kajh.api")
STATUSES = {"Eligible", "Ineligible", "Unknown", "Needs Review"}


@app.middleware("http")
async def request_boundary(request: Request, call_next):
    request.state.request_id = str(uuid4())
    started = time.monotonic()
    try:
        declared_length = int(request.headers.get("content-length", "0") or 0)
    except ValueError:
        return JSONResponse({"error": {"code": "INVALID_CONTENT_LENGTH", "message": "Invalid request length.", "request_id": request.state.request_id}}, 400)
    if declared_length < 0 or declared_length > 1_048_576:
        return JSONResponse({"error": {"code": "PAYLOAD_TOO_LARGE", "message": "Request exceeds 1 MB.", "request_id": request.state.request_id}}, 413)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 1_048_576:
                return JSONResponse({"error": {"code": "PAYLOAD_TOO_LARGE", "message": "Request exceeds 1 MB.", "request_id": request.state.request_id}}, 413)
            body.extend(chunk)
        request._body = bytes(body)
    with tracer.start_as_current_span(f"{request.method} {request.url.path}"):
        response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    log.info(json.dumps({"event": "http_request", "request_id": request.state.request_id, "method": request.method, "path": request.url.path, "status": response.status_code, "duration_ms": round((time.monotonic() - started) * 1000, 2)}))
    return response


@app.exception_handler(APIError)
async def api_error(request: Request, error: APIError):
    return JSONResponse({"error": {"code": error.code, "message": error.message, "request_id": getattr(request.state, "request_id", None), "details": error.details}}, error.status)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, error: RequestValidationError):
    return JSONResponse({"error": {"code": "VALIDATION_ERROR", "message": "Some fields are invalid.", "request_id": getattr(request.state, "request_id", None), "details": [{"field": ".".join(map(str, e["loc"])), "message": e["msg"], "type": e["type"]} for e in error.errors()]}}, 422)


@app.exception_handler(ValueError)
async def invalid_value(request: Request, error: ValueError):
    return JSONResponse({"error": {"code": "INVALID_CONFIGURATION", "message": "Configuration contains invalid values.", "request_id": getattr(request.state, "request_id", None)}}, 422)


@app.exception_handler(IntegrityError)
async def conflict(request: Request, error: IntegrityError):
    return JSONResponse({"error": {"code": "CONFLICT", "message": "The update conflicts with current data.", "request_id": getattr(request.state, "request_id", None)}}, 409)


@app.exception_handler(SQLAlchemyError)
async def db_unavailable(request: Request, error: SQLAlchemyError):
    log.error(json.dumps({"event": "database_error", "error_type": type(error).__name__, "request_id": getattr(request.state, "request_id", None)}))
    return JSONResponse({"error": {"code": "DATABASE_UNAVAILABLE", "message": "The local database is temporarily unavailable.", "request_id": getattr(request.state, "request_id", None)}}, 503)


def record(db: Session, person: Identity, request: Request, action: str, obj: str, oid=None, details=None) -> None:
    db.add(AuditEvent(workspace_id=person.workspace_id, actor=str(person.user_id), action=action, object_type=obj, object_id=str(oid) if oid else None, details=details or {}, request_id=request.state.request_id))


def objdict(obj) -> dict:
    def clean(v):
        if isinstance(v, UUID):
            return str(v)
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return v
    return {c.name: clean(getattr(obj, c.name)) for c in obj.__table__.columns}


def get_or_404(db: Session, cls, item_id: UUID):
    value = db.get(cls, item_id)
    if value is None:
        raise APIError(404, "NOT_FOUND", "This record is unavailable in your workspace.")
    return value


def idem_start(db: Session, person: Identity, scope: str, key: str | None, body: dict):
    if not key or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,160}", key):
        raise APIError(422, "IDEMPOTENCY_REQUIRED", "Provide an 8–160 character Idempotency-Key.")
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    existing = db.scalar(select(IdempotencyKey).where(IdempotencyKey.actor == str(person.user_id), IdempotencyKey.scope == scope, IdempotencyKey.key == key))
    if existing:
        if existing.request_hash != digest:
            raise APIError(409, "IDEMPOTENCY_CONFLICT", "That operation key was already used with another payload.")
        return existing.response, digest
    return None, digest


def idem_finish(db: Session, person: Identity, scope: str, key: str | None, digest: str, response: dict):
    if key is None:
        raise APIError(422, "IDEMPOTENCY_REQUIRED", "An operation key is required.")
    db.add(IdempotencyKey(workspace_id=person.workspace_id, actor=str(person.user_id), scope=scope, key=key, request_hash=digest, response=response))
    return response


def opportunity_dto(db: Session, person: Identity, row: Opportunity) -> dict:
    result = objdict(row)
    result["saved"] = bool(db.scalar(select(SavedOpportunity.id).where(SavedOpportunity.opportunity_id == row.id, SavedOpportunity.user_id == person.user_id)))
    return result


def visible_opportunities():
    return (
        Opportunity.merged_into.is_(None),
        Opportunity.lifecycle.not_in(["expired", "purged"]),
        or_(Opportunity.expires_at.is_(None), Opportunity.expires_at > func.now()),
        exists(select(OpportunitySource.id).join(RawObservation, RawObservation.id == OpportunitySource.observation_id).where(OpportunitySource.opportunity_id == Opportunity.id, RawObservation.expires_at > func.now(), func.app.source_visible(OpportunitySource.source_id))),
        ~exists(select(OpportunitySource.id).outerjoin(RawObservation, RawObservation.id == OpportunitySource.observation_id).where(OpportunitySource.opportunity_id == Opportunity.id, or_(RawObservation.id.is_(None), RawObservation.expires_at <= func.now(), func.app.source_visible(OpportunitySource.source_id).is_not(True)))),
    )


@app.get("/health")
def health():
    return {"status": "ok", "phase": "P1", "external_dispatch": False, "paid_models": False}


@app.get("/ready")
def ready():
    with engine().connect() as connection:
        connection.execute(text("SELECT 1"))
        extension: str = connection.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'" )).scalar_one()
    return {"status": "ready", "pgvector": extension, "source_mode": "synthetic-only"}


@app.get("/api/openapi.json", include_in_schema=False)
def private_contract(person: Identity = Depends(identity)):
    return app.openapi()


@app.post("/api/v1/auth/login")
async def login(body: Login, request: Request, response: Response):
    if request.headers.get("origin") not in settings().origins:
        raise APIError(403, "ORIGIN_DENIED", "Use the local dashboard to sign in.")
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False) as client:
            result = await client.post(f"{settings().auth_url}/token?grant_type=password", json=body.model_dump())
        if result.status_code != 200:
            raise APIError(401, "LOGIN_FAILED", "Email or password is incorrect.")
        token = result.json()["access_token"]
    except (httpx.HTTPError, ValueError, KeyError):
        raise APIError(503, "AUTH_UNAVAILABLE", "The local authentication service is unavailable.") from None
    person = resolve_identity(token)
    csrf = csrf_token(person.session_id)
    response.set_cookie("kajh_session", token, httponly=True, samesite="strict", secure=False, max_age=settings().session_max_age, path="/")
    response.set_cookie("kajh_csrf", csrf, httponly=False, samesite="strict", secure=False, max_age=settings().session_max_age, path="/")
    from .db import bind_identity
    with Session(engine()) as db, db.begin():
        bind_identity(db, str(person.user_id), str(person.workspace_id))
        # Auditors must be able to establish sessions without business writes.
        db.execute(text("SELECT app.log_session_event(:action, CAST(:wid AS uuid), CAST(:uid AS uuid), :rid)"), {"action": "auth.login", "wid": str(person.workspace_id), "uid": str(person.user_id), "rid": request.state.request_id})
    return identity_payload(person, csrf)


@app.get("/api/v1/auth/me")
def me(request: Request, person: Identity = Depends(identity)):
    return identity_payload(person, request.cookies.get("kajh_csrf"))


@app.post("/api/v1/auth/logout")
async def logout(request: Request, response: Response, person: Identity = Depends(identity), db: Session = Depends(database)):
    from .auth import require_csrf
    require_csrf(request, person)
    db.execute(text("SELECT app.revoke_session(CAST(:uid AS uuid),CAST(:sid AS uuid))"), {"uid": str(person.user_id), "sid": str(person.session_id)})
    db.execute(text("SELECT app.log_session_event(:action, CAST(:wid AS uuid), CAST(:uid AS uuid), :rid)"), {"action": "auth.logout", "wid": str(person.workspace_id), "uid": str(person.user_id), "rid": request.state.request_id})
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as client:
            await client.post(f"{settings().auth_url}/logout?scope=local", headers={"Authorization": f"Bearer {request.cookies['kajh_session']}"})
    except httpx.HTTPError:
        pass  # Local revocation is authoritative even during an Auth outage.
    response.delete_cookie("kajh_session", path="/")
    response.delete_cookie("kajh_csrf", path="/")
    return {"signed_out": True}


@app.get("/api/v1/dashboard")
def dashboard(person: Identity = Depends(identity), db: Session = Depends(database)):
    counts = dict(db.execute(select(Opportunity.eligibility, func.count()).where(*visible_opportunities()).group_by(Opportunity.eligibility)).all())
    recent = db.scalars(select(Opportunity).where(*visible_opportunities()).order_by(Opportunity.last_seen.desc()).limit(6)).all()
    saved = db.scalar(select(func.count()).select_from(SavedOpportunity).where(SavedOpportunity.user_id == person.user_id)) or 0
    sources = db.scalars(select(Source).order_by(Source.name)).all()
    return {"counts": {"total": sum(counts.values()), "eligible": counts.get("Eligible", 0), "ineligible": counts.get("Ineligible", 0), "unknown": counts.get("Unknown", 0), "needs_review": counts.get("Needs Review", 0), "review_queue": counts.get("Unknown", 0) + counts.get("Needs Review", 0), "saved": saved, "active_sources": sum(s.enabled and not s.killed for s in sources)}, "recent": [opportunity_dto(db, person, row) for row in recent], "source_health": [objdict(s) for s in sources], "capabilities": {"ai_ranking": False, "live_verification": False, "digests": False, "external_dispatch": False}}


@app.get("/api/v1/opportunities")
def opportunities(q: str = Query(default="", max_length=200), status: str | None = None, country: str | None = Query(default=None, max_length=10), workplace: str | None = None, source_id: UUID | None = None, skill: str | None = Query(default=None, max_length=100), saved: bool = False, limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0, le=100000), person: Identity = Depends(identity), db: Session = Depends(database)):
    query = select(Opportunity).where(*visible_opportunities())
    if status:
        if status not in STATUSES:
            raise APIError(422, "INVALID_STATUS", "Choose a supported eligibility status.")
        query = query.where(Opportunity.eligibility == status)
    if q:
        vector = func.to_tsvector(literal_column("'simple'::regconfig"), Opportunity.title + " " + Opportunity.organization + " " + Opportunity.description)
        query = query.where(vector.op("@@")(func.websearch_to_tsquery(literal_column("'simple'::regconfig"), q)))
    if country:
        query = query.where(Opportunity.country == country.upper())
    if workplace:
        query = query.where(Opportunity.workplace == workplace)
    if source_id:
        query = query.where(Opportunity.id.in_(select(OpportunitySource.opportunity_id).where(OpportunitySource.source_id == source_id)))
    if skill:
        query = query.where(Opportunity.skills.contains([skill]))
    if saved:
        query = query.where(Opportunity.id.in_(select(SavedOpportunity.opportunity_id).where(SavedOpportunity.user_id == person.user_id)))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Opportunity.last_seen.desc(), Opportunity.id).limit(limit).offset(offset)).all()
    return {"items": [opportunity_dto(db, person, row) for row in rows], "total": total, "next_cursor": str(offset + limit) if offset + limit < total else None}


@app.get("/api/v1/opportunities/{item_id}")
def opportunity(item_id: UUID, request: Request, person: Identity = Depends(identity), db: Session = Depends(database)):
    row = db.scalar(select(Opportunity).where(Opportunity.id == item_id, *visible_opportunities()))
    if not row:
        raise APIError(404, "NOT_FOUND", "Opportunity evidence is unavailable or its retention permission expired.")
    sources = db.execute(select(OpportunitySource, Source.name).join(Source, Source.id == OpportunitySource.source_id).join(RawObservation, RawObservation.id == OpportunitySource.observation_id).where(OpportunitySource.opportunity_id == item_id, RawObservation.expires_at > func.now(), func.app.source_visible(Source.id))).all()
    # Historical excerpts inherit the rights and expiry of their own observation,
    # even after the current provenance link has refreshed to a newer observation.
    retained_evidence = exists(select(RawObservation.id).where(RawObservation.id.cast(String) == EligibilityEvidence.details["observation_id"].astext, RawObservation.workspace_id == EligibilityEvidence.workspace_id, RawObservation.expires_at > func.now(), func.app.source_visible(RawObservation.source_id)))
    current_derived = EligibilityEvidence.details["observation_id"].astext.is_(None) & (EligibilityEvidence.details["content_version"].astext == str(row.version))
    evidence = db.scalars(select(EligibilityEvidence).where(EligibilityEvidence.opportunity_id == item_id, or_(retained_evidence, current_derived)).order_by(EligibilityEvidence.created_at)).all()
    history = db.scalars(select(AuditEvent).where(AuditEvent.object_id == str(item_id)).order_by(AuditEvent.created_at.desc()).limit(50)).all()
    return {"opportunity": opportunity_dto(db, person, row), "sources": [{**objdict(link), "name": name} for link, name in sources], "evidence": [objdict(e) for e in evidence], "history": [objdict(e) for e in history]}


@app.post("/api/v1/opportunities/{item_id}/review")
def review(item_id: UUID, body: ReviewInput, request: Request, person: Identity = Depends(require_write), db: Session = Depends(database), idempotency_key: str | None = Header(default=None)):
    payload = body.model_dump()
    existing, digest = idem_start(db, person, f"review:{item_id}", idempotency_key, payload)
    if existing:
        return existing
    row = db.scalar(select(Opportunity).where(Opportunity.id == item_id, *visible_opportunities()).with_for_update())
    if not row:
        raise APIError(404, "NOT_FOUND", "Opportunity not found.")
    if row.version != body.version:
        raise APIError(412, "STALE_VERSION", "The opportunity changed. Refresh before reviewing.")
    bounded_object(body.facts)
    facts = {**row.facts, **body.facts}
    vendor = db.scalar(select(VendorProfile).order_by(VendorProfile.version.desc()).limit(1))
    try:
        result = evaluate_eligibility({"facts": facts, "category": row.category}, vendor.profile if vendor else None)
    except (ValueError, TypeError):
        raise APIError(422, "INVALID_FACTS", "Review facts must use the declared types and units.") from None
    row.facts = facts
    row.eligibility = result["status"]
    row.version += 1
    for rule in result["rules"]:
        db.add(EligibilityEvidence(workspace_id=person.workspace_id, opportunity_id=row.id, rule=rule["rule_id"], outcome=rule["status"], details={**rule, "review_reason": body.reason, "reviewer": str(person.user_id), "content_version": row.version}))
    record(db, person, request, "opportunity.reviewed", "opportunity", row.id, {"reason": body.reason, "status": row.eligibility, "version": row.version})
    response = {"id": str(row.id), "version": row.version, "eligibility": row.eligibility}
    return idem_finish(db, person, f"review:{item_id}", idempotency_key, digest, response)


@app.put("/api/v1/opportunities/{item_id}/saved")
def save(item_id: UUID, body: SavedInput, request: Request, person: Identity = Depends(require_write), db: Session = Depends(database)):
    get_or_404(db, Opportunity, item_id)
    existing = db.scalar(select(SavedOpportunity).where(SavedOpportunity.opportunity_id == item_id, SavedOpportunity.user_id == person.user_id))
    if body.saved and not existing:
        db.add(SavedOpportunity(workspace_id=person.workspace_id, user_id=person.user_id, opportunity_id=item_id))
    elif not body.saved and existing:
        db.delete(existing)
    record(db, person, request, "opportunity.saved" if body.saved else "opportunity.unsaved", "opportunity", item_id)
    return {"id": str(item_id), "saved": body.saved}


@app.get("/api/v1/vendor-profile")
def vendor_profile(person: Identity = Depends(identity), db: Session = Depends(database)):
    row = db.scalar(select(VendorProfile).order_by(VendorProfile.version.desc()).limit(1))
    if not row:
        raise APIError(404, "NOT_FOUND", "Vendor profile has not been configured.")
    return objdict(row)


@app.put("/api/v1/vendor-profile")
def update_vendor(body: VendorInput, request: Request, person: Identity = Depends(require_write), db: Session = Depends(database), idempotency_key: str | None = Header(default=None)):
    require_owner(person)
    existing, digest = idem_start(db, person, "vendor-profile", idempotency_key, body.model_dump())
    if existing:
        return existing
    previous = db.scalar(select(VendorProfile).order_by(VendorProfile.version.desc()).limit(1))
    if not previous or previous.version != body.version:
        raise APIError(412, "STALE_VERSION", "Vendor profile changed. Refresh before saving.")
    profile = bounded_object(body.profile)
    profile["synthetic"] = True
    profile["verified"] = False
    legal = dict(profile.get("legal_entity") or {})
    if "legal_name" in profile:
        legal["name"] = profile["legal_name"]
    if "entity_country" in profile:
        legal["country"] = profile["entity_country"]
    legal["verified"] = False
    profile["legal_entity"] = legal
    if "skills" in profile:
        profile["technical_capabilities"] = profile["skills"]
    rates = dict(profile.get("rate_preferences") or {})
    for source, target in (("preferred_currency", "currency"), ("minimum_rate", "minimum"), ("rate_unit", "unit")):
        if source in profile:
            rates[target] = profile[source]
    profile["rate_preferences"] = rates
    row = VendorProfile(workspace_id=person.workspace_id, version=previous.version + 1, synthetic=True, profile=profile)
    db.add(row)
    db.flush()
    job = Job(workspace_id=person.workspace_id, kind="reassess", payload={"vendor_profile_version": row.version}, idempotency_key=f"vendor-reassess:{row.id}", request_hash=hashlib.sha256(f"vendor-reassess:{row.id}".encode()).hexdigest())
    db.add(job)
    db.flush()
    record(db, person, request, "vendor_profile.version_created", "vendor_profile", row.id, {"version": row.version})
    return idem_finish(db, person, "vendor-profile", idempotency_key, digest, {**objdict(row), "reassessment_job_id": str(job.id)})


@app.get("/api/v1/search-profiles")
def profiles(person: Identity = Depends(identity), db: Session = Depends(database)):
    return {"items": [objdict(row) for row in db.scalars(select(SearchProfile).order_by(SearchProfile.name)).all()]}


@app.post("/api/v1/search-profiles", status_code=201)
def create_profile(body: SearchInput, request: Request, person: Identity = Depends(require_write), db: Session = Depends(database), idempotency_key: str | None = Header(default=None)):
    require_owner(person)
    existing, digest = idem_start(db, person, "search-profile:create", idempotency_key, body.model_dump())
    if existing:
        return existing
    row = SearchProfile(workspace_id=person.workspace_id, name=body.name, filters=bounded_object(body.filters))
    db.add(row)
    db.flush()
    result = objdict(row)
    db.add(ProfileVersion(workspace_id=person.workspace_id, profile_id=row.id, version=row.version, body=result))
    record(db, person, request, "search_profile.created", "search_profile", row.id)
    return idem_finish(db, person, "search-profile:create", idempotency_key, digest, result)


@app.put("/api/v1/search-profiles/{profile_id}")
def update_profile(profile_id: UUID, body: SearchUpdate, request: Request, person: Identity = Depends(require_write), db: Session = Depends(database), idempotency_key: str | None = Header(default=None)):
    require_owner(person)
    scope = f"search-profile:{profile_id}"
    existing, digest = idem_start(db, person, scope, idempotency_key, body.model_dump())
    if existing:
        return existing
    row = db.scalar(select(SearchProfile).where(SearchProfile.id == profile_id).with_for_update())
    if not row:
        raise APIError(404, "NOT_FOUND", "Profile not found.")
    if row.version != body.version:
        raise APIError(412, "STALE_VERSION", "Search profile changed. Refresh before saving.")
    row.version += 1
    row.name = body.name
    row.filters = bounded_object(body.filters)
    row.updated_at = utcnow()
    result = objdict(row)
    db.add(ProfileVersion(workspace_id=person.workspace_id, profile_id=row.id, version=row.version, body=result))
    record(db, person, request, "search_profile.version_created", "search_profile", row.id, {"version": row.version})
    return idem_finish(db, person, scope, idempotency_key, digest, result)


@app.get("/api/v1/sources")
def sources(person: Identity = Depends(identity), db: Session = Depends(database)):
    return {"items": [objdict(row) for row in db.scalars(select(Source).order_by(Source.name)).all()]}


@app.patch("/api/v1/sources/{source_id}")
def update_source(source_id: UUID, body: SourceUpdate, request: Request, person: Identity = Depends(require_write), db: Session = Depends(database), idempotency_key: str | None = Header(default=None)):
    require_owner(person)
    scope = f"source:{source_id}"
    existing, digest = idem_start(db, person, scope, idempotency_key, body.model_dump())
    if existing:
        return existing
    row = db.scalar(select(Source).where(Source.id == source_id).with_for_update())
    if not row:
        raise APIError(404, "NOT_FOUND", "Source not found.")
    if body.version != row.permission_version:
        raise APIError(412, "STALE_VERSION", "Source policy changed. Refresh before saving.")
    if body.enabled and row.adapter_type != "synthetic":
        raise APIError(403, "REAL_SOURCE_DISABLED", "Real source activation is not authorized in P1.")
    if body.permission is not None:
        row.permission = bounded_object(body.permission)
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.killed is not None:
        row.killed = body.killed
        if row.killed:
            row.enabled = False
    if row.enabled:
        from kajh_domain import validate_source_policy
        try:
            validate_source_policy({"adapter_type": row.adapter_type, "enabled": row.enabled, "killed": row.killed}, row.permission)
        except ValueError:
            raise APIError(409, "SOURCE_POLICY_BLOCKED", "A current approved policy is required before enabling the synthetic source.") from None
    row.permission_version += 1
    db.add(SourcePermission(workspace_id=person.workspace_id, source_id=row.id, version=row.permission_version, body={**row.permission, "enabled": row.enabled, "killed": row.killed}))
    record(db, person, request, "source.policy_changed", "source", row.id, {"version": row.permission_version, "enabled": row.enabled, "killed": row.killed})
    return idem_finish(db, person, scope, idempotency_key, digest, objdict(row))


@app.post("/api/v1/sources/{source_id}/ingest", status_code=202)
def ingest(source_id: UUID, request: Request, person: Identity = Depends(require_write), db: Session = Depends(database), idempotency_key: str | None = Header(default=None)):
    from kajh_domain import validate_source_policy
    scope = f"ingest:{source_id}"
    existing, digest = idem_start(db, person, scope, idempotency_key, {})
    if existing:
        return existing
    source = get_or_404(db, Source, source_id)
    if source.adapter_type != "synthetic":
        raise APIError(403, "REAL_SOURCE_DISABLED", "Only synthetic ingestion is authorized.")
    try:
        validate_source_policy({"adapter_type": source.adapter_type, "enabled": source.enabled, "killed": source.killed}, source.permission)
    except Exception:
        raise APIError(409, "SOURCE_POLICY_BLOCKED", "The source is disabled, expired, revoked, or lacks permission.") from None
    job = Job(workspace_id=person.workspace_id, source_id=source.id, kind="ingest", payload={"requested_by": str(person.user_id)}, idempotency_key=f"api:{person.user_id}:{idempotency_key}", request_hash=digest)
    db.add(job)
    db.flush()
    record(db, person, request, "ingestion.requested", "job", job.id)
    return idem_finish(db, person, scope, idempotency_key, digest, {"job_id": str(job.id), "status": job.status})


@app.get("/api/v1/source-runs")
def source_runs(person: Identity = Depends(identity), db: Session = Depends(database)):
    return {"items": [objdict(row) for row in db.scalars(select(SourceRun).order_by(SourceRun.started_at.desc()).limit(100)).all()]}


@app.get("/api/v1/jobs")
def jobs(person: Identity = Depends(identity), db: Session = Depends(database)):
    return {"items": [objdict(row) for row in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(100)).all()]}


@app.get("/api/v1/workflow-runs")
def workflows(person: Identity = Depends(identity), db: Session = Depends(database)):
    return {"items": [objdict(row) for row in db.scalars(select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(100)).all()]}


@app.post("/api/v1/workflow-runs/{run_id}/resume", status_code=202)
def resume(run_id: UUID, body: ResumeInput, request: Request, person: Identity = Depends(require_write), db: Session = Depends(database), idempotency_key: str | None = Header(default=None)):
    scope = f"workflow:{run_id}:resume"
    existing, digest = idem_start(db, person, scope, idempotency_key, body.model_dump())
    if existing:
        return existing
    run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update())
    if not run:
        raise APIError(404, "NOT_FOUND", "Workflow not found.")
    if run.version != body.version:
        raise APIError(412, "STALE_VERSION", "Workflow changed. Refresh before resuming.")
    if run.restore_hold or run.status not in {"needs_review", "interrupted", "waiting_review"}:
        raise APIError(409, "WORKFLOW_NOT_RESUMABLE", "This workflow cannot be resumed in its current state.")
    opportunity = get_or_404(db, Opportunity, run.opportunity_id)
    payload_hash = hashlib.sha256(f"{run.thread_id}:{opportunity.version}:review".encode()).hexdigest()
    decision = WorkflowDecision(workspace_id=person.workspace_id, run_id=run.id, user_id=person.user_id, decision="approved", payload_hash=payload_hash, content_version=opportunity.version, expires_at=utcnow() + timedelta(minutes=15))
    db.add(decision)
    db.flush()
    run.version += 1
    run.status = "resume_requested"
    run.updated_at = utcnow()
    job = Job(workspace_id=person.workspace_id, kind="resume", payload={"run_id": str(run.id), "decision_id": str(decision.id)}, idempotency_key=f"resume:{decision.id}", request_hash=payload_hash)
    db.add(job)
    db.flush()
    record(db, person, request, "workflow.resume_authorized", "workflow", run.id, {"reason": body.reason, "decision_id": str(decision.id), "opportunity_version": opportunity.version})
    return idem_finish(db, person, scope, idempotency_key, digest, {"job_id": str(job.id), "run_id": str(run.id), "version": run.version, "status": run.status})


@app.get("/api/v1/audit-events")
def audit_events(person: Identity = Depends(identity), db: Session = Depends(database)):
    return {"items": [objdict(row) for row in db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(100)).all()]}


@app.post("/api/v1/opportunities/{item_id}/unmerge")
def unmerge(item_id: UUID, request: Request, person: Identity = Depends(require_write), db: Session = Depends(database)):
    require_owner(person)
    row = get_or_404(db, Opportunity, item_id)
    if row.merged_into is None:
        raise APIError(409, "NOT_MERGED", "This opportunity is not merged.")
    previous = row.merged_into
    row.merged_into = None
    row.version += 1
    record(db, person, request, "opportunity.unmerged", "opportunity", row.id, {"previous_target": str(previous)})
    return {"id": str(row.id), "version": row.version, "merged_into": None}


@app.post("/api/v1/opportunities/{item_id}/split-source", status_code=201)
def split_source(item_id: UUID, body: SplitSourceInput, request: Request, person: Identity = Depends(require_write), db: Session = Depends(database), idempotency_key: str | None = Header(default=None)):
    from kajh_domain import normalize_opportunity
    require_owner(person)
    scope = f"split-source:{item_id}"
    previous, digest = idem_start(db, person, scope, idempotency_key, body.model_dump())
    if previous:
        return previous
    original = db.scalar(select(Opportunity).where(Opportunity.id == item_id).with_for_update())
    if not original:
        raise APIError(404, "NOT_FOUND", "Opportunity not found.")
    if original.version != body.version:
        raise APIError(412, "STALE_VERSION", "Opportunity changed. Refresh before separating sources.")
    try:
        link_id = UUID(body.source_link_id)
    except ValueError:
        raise APIError(422, "INVALID_SOURCE_LINK", "Invalid source reference.") from None
    links = list(db.scalars(select(OpportunitySource).where(OpportunitySource.opportunity_id == item_id).with_for_update()))
    link = next((value for value in links if value.id == link_id), None)
    if not link or len(links) < 2:
        raise APIError(409, "NOT_SPLITTABLE", "A shared canonical opportunity needs at least two source references.")
    if not db.scalar(select(func.app.source_visible(link.source_id))):
        raise APIError(409, "SOURCE_POLICY_BLOCKED", "The selected source is no longer permitted.")
    observation = get_or_404(db, RawObservation, link.observation_id)
    if observation.expires_at <= utcnow():
        raise APIError(409, "EVIDENCE_EXPIRED", "Source evidence expired.")
    normalized = normalize_opportunity(observation.payload, str(link.source_id))
    vendor = db.scalar(select(VendorProfile).order_by(VendorProfile.version.desc()).limit(1))
    facts = {**normalized["facts"], "category": normalized["category"], "synthetic": True, "split_from": str(original.id), "requisition_id": normalized.get("requisition_id")}
    assessment = evaluate_eligibility({**normalized, "facts": facts}, vendor.profile if vendor else None)
    separated = Opportunity(workspace_id=person.workspace_id, title=normalized["title"], organization=normalized["employer"]["name"], organization_domain=normalized["employer"].get("domain"), category=normalized["category"], description=normalized["description"], facts=facts, skills=normalized["skills"], eligibility=assessment["status"], workplace=facts.get("workplace_type", "Unknown"), country=(facts.get("delivery_countries") or [None])[0], dedupe_key=None, first_seen=observation.observed_at, last_seen=observation.observed_at, expires_at=observation.expires_at)
    db.add(separated)
    db.flush()
    link.opportunity_id = separated.id
    remaining_links = [value for value in links if value.id != link.id]
    remaining_observation = db.get(RawObservation, remaining_links[0].observation_id)
    if remaining_observation:
        remaining_record = normalize_opportunity(remaining_observation.payload, str(remaining_links[0].source_id))
        remaining_facts = {**remaining_record["facts"], "category": remaining_record["category"], "synthetic": True, "requisition_id": remaining_record.get("requisition_id")}
        remaining_assessment = evaluate_eligibility({**remaining_record, "facts": remaining_facts}, vendor.profile if vendor else None)
        original.title = remaining_record["title"]
        original.organization = remaining_record["employer"]["name"]
        original.organization_domain = remaining_record["employer"].get("domain")
        original.category = remaining_record["category"]
        original.description = remaining_record["description"]
        original.facts = remaining_facts
        original.skills = remaining_record["skills"]
        original.eligibility = remaining_assessment["status"]
        original.workplace = remaining_facts.get("workplace_type", "Unknown")
        original.country = (remaining_facts.get("delivery_countries") or [None])[0]
    original.version += 1
    for rule in assessment["rules"]:
        db.add(EligibilityEvidence(workspace_id=person.workspace_id, opportunity_id=separated.id, rule=rule["rule_id"], outcome=rule["status"], details={**rule, "source_id": str(link.source_id), "observation_id": str(observation.id), "content_version": 1}))
    record(db, person, request, "opportunity.source_separated", "opportunity", original.id, {"new_opportunity_id": str(separated.id), "source_link_id": str(link.id), "reason": body.reason})
    record(db, person, request, "opportunity.separated_from", "opportunity", separated.id, {"original_id": str(original.id), "reason": body.reason})
    return idem_finish(db, person, scope, idempotency_key, digest, {"id": str(separated.id), "original_id": str(original.id), "source_link_id": str(link.id), "version": 1})
