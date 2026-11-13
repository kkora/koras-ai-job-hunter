"""Administrative local migrations/fixtures; never imported by the API process."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from kajh_domain.adapters import synthetic_permission, synthetic_vendor_profile
from .models import Job, Membership, ProfileVersion, SearchProfile, Source, SourcePermission, VendorProfile, WorkerGrant, Workspace

ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = UUID("00000000-0000-0000-0000-000000000001")
ISOLATION_WORKSPACE = UUID("00000000-0000-0000-0000-000000000002")


def admin_engine():
    value = os.environ["DATABASE_ADMIN_URL"]
    url = make_url(value)
    if url.username != "postgres" or url.database != "kajh_dev" or url.host not in {"db", "restore-db", "127.0.0.1", "localhost"}:
        raise RuntimeError("Refusing nonlocal administrative migration target")
    return create_engine(value, hide_parameters=True)


def migrate():
    engine = admin_engine()
    policies = ROOT / "database/migrations/001_policies.sql"
    schema = ROOT / "database/migrations/001_schema.sql"
    checksum = hashlib.sha256(policies.read_bytes() + schema.read_bytes()).hexdigest()
    legacy_checksum = "0d2e26fe88bbcb545e709350f3b2f9b43635c2714ea836838c99d66ffbae022c"
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION kajh_owner"))
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS checkpoints AUTHORIZATION kajh_worker"))
        connection.execute(text("CREATE TABLE IF NOT EXISTS app.schema_migrations(version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"))
        existing = connection.execute(text("SELECT checksum FROM app.schema_migrations WHERE version='001_initial'" )).scalar_one_or_none()
        if existing:
            if existing not in {checksum, legacy_checksum}:
                raise RuntimeError("Migration 001 checksum changed; add a new migration or reset only owned disposable development data")
            print(json.dumps({"migration": "001_initial", "status": "already_applied"}))
        else:
            connection.execute(text("SET LOCAL ROLE kajh_owner"))
            connection.exec_driver_sql(schema.read_text(), execution_options={"no_parameters": True})
            connection.execute(text("RESET ROLE"))
            connection.exec_driver_sql(policies.read_text(), execution_options={"no_parameters": True})
            connection.execute(text("INSERT INTO app.schema_migrations(version,checksum) VALUES ('001_initial',:checksum)"), {"checksum": checksum})
            print(json.dumps({"migration": "001_initial", "status": "applied", "checksum": checksum}))
        for path in sorted((ROOT / "database/migrations").glob("[0-9][0-9][0-9]_*.sql")):
            if int(path.name[:3]) <= 1:
                continue
            version = path.stem
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            stored = connection.execute(text("SELECT checksum FROM app.schema_migrations WHERE version=:version"), {"version": version}).scalar_one_or_none()
            if stored is not None:
                if stored != digest:
                    raise RuntimeError(f"Applied migration checksum changed: {version}")
                continue
            connection.exec_driver_sql(path.read_text(), execution_options={"no_parameters": True})
            connection.execute(text("INSERT INTO app.schema_migrations(version,checksum) VALUES (:version,:checksum)"), {"version": version, "checksum": digest})
            print(json.dumps({"migration": version, "status": "applied", "checksum": digest}))


def seed():
    engine = admin_engine()
    auth_url = os.environ.get("AUTH_URL", "http://127.0.0.1:59999")
    if auth_url not in {"http://127.0.0.1:59999", "http://localhost:59999", "http://auth:9999"}:
        raise RuntimeError("Refusing nonlocal Auth URL")
    identities = []
    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
        for role in ("Owner", "Reviewer", "Auditor"):
            email = f"{role.lower()}@kajh.invalid"
            password = os.environ[f"{role.upper()}_PASSWORD"]
            if len(password) < 16:
                raise RuntimeError("Synthetic password must be at least 16 characters")
            with engine.connect() as connection:
                user_id = connection.execute(text("SELECT id FROM auth.users WHERE email=:email"), {"email": email}).scalar_one_or_none()
            if user_id is None:
                response = client.post(f"{auth_url}/signup", json={"email": email, "password": password})
                response.raise_for_status()
                data = response.json()
                user_id = UUID((data.get("user") or data)["id"])
            login = client.post(f"{auth_url}/token?grant_type=password", json={"email": email, "password": password})
            login.raise_for_status()
            identities.append((role, email, UUID(str(user_id))))

    with Session(engine) as db, db.begin():
        for wid, name in ((WORKSPACE, "Koras synthetic development"), (ISOLATION_WORKSPACE, "Synthetic isolation workspace")):
            if not db.get(Workspace, wid):
                db.add(Workspace(id=wid, name=name))
        db.flush()
        if not db.get(WorkerGrant, (WORKSPACE, "kajh_worker")):
            db.add(WorkerGrant(workspace_id=WORKSPACE, role_name="kajh_worker"))
        for role, email, user_id in identities:
            if not db.get(Membership, (WORKSPACE, user_id)):
                db.add(Membership(workspace_id=WORKSPACE, user_id=user_id, email=email, role=role, active=True))
        if not db.scalar(select(VendorProfile).where(VendorProfile.workspace_id == WORKSPACE)):
            profile = synthetic_vendor_profile()
            profile.update(legal_name=profile["legal_entity"]["name"], entity_country="US", skills=profile["technical_capabilities"], preferred_currency="USD", minimum_rate=100, rate_unit="hour")
            db.add(VendorProfile(workspace_id=WORKSPACE, version=1, synthetic=True, profile=profile))
        if not db.scalar(select(SearchProfile).where(SearchProfile.workspace_id == WORKSPACE)):
            for name, skills in (("Power Platform Architecture", ["Power Platform", "Dataverse", "Power Pages"]), ("Dynamics 365 & Copilot", ["Dynamics 365", "Copilot Studio"]), ("Azure & AI Integration", ["Azure", "AI Architecture", "Enterprise Integration"])):
                filters = {"skills": skills, "countries": ["US", "CA", "GB"], "workplaces": ["remote"], "b2b_required": True, "minimum_rate": 100, "currency": "USD", "rate_unit": "hour", "seniority": ["Architect", "Lead"], "excluded_employment_types": ["W2-only", "Employee-only"], "skill_aliases": {"powerapps": "Power Apps", "d365": "Dynamics 365"}}
                search = SearchProfile(workspace_id=WORKSPACE, name=name, filters=filters)
                db.add(search)
                db.flush()
                db.add(ProfileVersion(workspace_id=WORKSPACE, profile_id=search.id, version=1, body={"name": name, "filters": filters, "version": 1}))
        synthetic_id = UUID("10000000-0000-0000-0000-000000000001")
        candidates = [(synthetic_id, "Synthetic opportunities", "synthetic"), (UUID("10000000-0000-0000-0000-000000000002"), "Remote OK — entitlement pending", "remote_ok"), (UUID("10000000-0000-0000-0000-000000000003"), "Lever — entitlement pending", "lever"), (UUID("10000000-0000-0000-0000-000000000004"), "Greenhouse — entitlement pending", "greenhouse"), (UUID("10000000-0000-0000-0000-000000000005"), "Ashby — entitlement pending", "ashby")]
        for sid, name, adapter in candidates:
            if not db.get(Source, sid):
                permission = synthetic_permission() if adapter == "synthetic" else {"approved": False, "status": "unreviewed", "revoked": False, "commercial_use": None, "automated_collection": None, "private_display": None, "persistent_storage": None, "evidence_retention": None, "ai_processing": None, "embeddings": None, "retention_days": None, "quota_per_day": None, "attribution": "Pending source-specific permission review", "expires_at": None}
                db.add(Source(id=sid, workspace_id=WORKSPACE, name=name, adapter_type=adapter, enabled=adapter == "synthetic", permission=permission, permission_version=1))
                db.flush()
                db.add(SourcePermission(workspace_id=WORKSPACE, source_id=sid, version=1, body=permission))
        if not db.scalar(select(Job).where(Job.workspace_id == WORKSPACE, Job.idempotency_key == "seed:synthetic-ingestion-v1")):
            db.add(Job(workspace_id=WORKSPACE, source_id=synthetic_id, kind="ingest", payload={"requested_by": str(identities[0][2])}, idempotency_key="seed:synthetic-ingestion-v1", request_hash=hashlib.sha256(b"seed:synthetic-ingestion-v1").hexdigest()))
    print(json.dumps({"seed": "complete", "workspace_id": str(WORKSPACE), "users": [row[1] for row in identities], "real_sources_enabled": 0, "synthetic_job_queued": True}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "seed"])
    command = parser.parse_args().command
    {"migrate": migrate, "seed": seed}[command]()


if __name__ == "__main__":
    main()
