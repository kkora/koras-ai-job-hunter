"""Shared PostgreSQL business model; app owns business state, not checkpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint, CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Identified:
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)


class Scoped(Identified):
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("app.workspaces.id"), nullable=False, index=True)


class Workspace(Identified, Base):
    __tablename__ = "workspaces"
    __table_args__ = {"schema": "app"}
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (CheckConstraint("role IN ('Owner','Reviewer','Auditor')"), {"schema": "app"})
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("app.workspaces.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(String(254))
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class WorkerGrant(Base):
    __tablename__ = "worker_grants"
    __table_args__ = {"schema": "app"}
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("app.workspaces.id"), primary_key=True)
    role_name: Mapped[str] = mapped_column(String(100), primary_key=True, default="kajh_worker")


class RevokedSession(Base):
    __tablename__ = "revoked_sessions"
    __table_args__ = {"schema": "app"}
    session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VendorProfile(Scoped, Base):
    __tablename__ = "vendor_profiles"
    __table_args__ = (UniqueConstraint("workspace_id", "version"), {"schema": "app"})
    version: Mapped[int] = mapped_column(Integer, default=1)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SearchProfile(Scoped, Base):
    __tablename__ = "search_profiles"
    __table_args__ = (UniqueConstraint("workspace_id", "id"), {"schema": "app"})
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProfileVersion(Scoped, Base):
    __tablename__ = "profile_versions"
    __table_args__ = (UniqueConstraint("workspace_id", "profile_id", "version"), ForeignKeyConstraint(["workspace_id", "profile_id"], ["app.search_profiles.workspace_id", "app.search_profiles.id"]), {"schema": "app"})
    profile_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Source(Scoped, Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("workspace_id", "id"), UniqueConstraint("workspace_id", "name"), CheckConstraint("adapter_type = 'synthetic' OR enabled = false"), {"schema": "app"})
    name: Mapped[str] = mapped_column(String(200))
    adapter_type: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    killed: Mapped[bool] = mapped_column(Boolean, default=False)
    permission: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    permission_version: Mapped[int] = mapped_column(Integer, default=1)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourcePermission(Scoped, Base):
    __tablename__ = "source_permissions"
    __table_args__ = (UniqueConstraint("workspace_id", "source_id", "version"), ForeignKeyConstraint(["workspace_id", "source_id"], ["app.sources.workspace_id", "app.sources.id"]), {"schema": "app"})
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Scoped, Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("workspace_id", "id"), UniqueConstraint("workspace_id", "idempotency_key"), ForeignKeyConstraint(["workspace_id", "source_id"], ["app.sources.workspace_id", "app.sources.id"]), CheckConstraint("attempts >= 0 AND max_attempts BETWEEN 1 AND 10"), {"schema": "app"})
    source_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(40), default="ingest")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceRun(Scoped, Base):
    __tablename__ = "source_runs"
    __table_args__ = (UniqueConstraint("workspace_id", "id"), ForeignKeyConstraint(["workspace_id", "source_id"], ["app.sources.workspace_id", "app.sources.id"]), ForeignKeyConstraint(["workspace_id", "job_id"], ["app.jobs.workspace_id", "app.jobs.id"]), {"schema": "app"})
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    job_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    cursor_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RawObservation(Scoped, Base):
    __tablename__ = "raw_observations"
    __table_args__ = (UniqueConstraint("workspace_id", "id"), UniqueConstraint("workspace_id", "source_id", "external_id", "payload_hash"), ForeignKeyConstraint(["workspace_id", "source_id"], ["app.sources.workspace_id", "app.sources.id"]), ForeignKeyConstraint(["workspace_id", "source_run_id"], ["app.source_runs.workspace_id", "app.source_runs.id"]), {"schema": "app"})
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    external_id: Mapped[str] = mapped_column(String(200))
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Opportunity(Scoped, Base):
    __tablename__ = "opportunities"
    __table_args__ = (UniqueConstraint("workspace_id", "id"), CheckConstraint("eligibility IN ('Eligible','Ineligible','Unknown','Needs Review')"), {"schema": "app"})
    title: Mapped[str] = mapped_column(String(300))
    organization: Mapped[str] = mapped_column(String(200))
    organization_domain: Mapped[str | None] = mapped_column(String(250), nullable=True)
    category: Mapped[str] = mapped_column(String(40), default="consulting_project")
    description: Mapped[str] = mapped_column(Text, default="")
    country: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    workplace: Mapped[str] = mapped_column(String(30), default="Unknown")
    eligibility: Mapped[str] = mapped_column(String(30), default="Unknown", index=True)
    lifecycle: Mapped[str] = mapped_column(String(30), default="open")
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    skills: Mapped[list[str]] = mapped_column(JSONB, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    dedupe_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    merged_into: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class OpportunitySource(Scoped, Base):
    __tablename__ = "opportunity_sources"
    __table_args__ = (UniqueConstraint("workspace_id", "source_id", "external_id"), ForeignKeyConstraint(["workspace_id", "opportunity_id"], ["app.opportunities.workspace_id", "app.opportunities.id"]), ForeignKeyConstraint(["workspace_id", "source_id"], ["app.sources.workspace_id", "app.sources.id"]), ForeignKeyConstraint(["workspace_id", "observation_id"], ["app.raw_observations.workspace_id", "app.raw_observations.id"]), {"schema": "app"})
    opportunity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    observation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    external_id: Mapped[str] = mapped_column(String(200))
    original_url: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EligibilityEvidence(Scoped, Base):
    __tablename__ = "eligibility_evidence"
    __table_args__ = (ForeignKeyConstraint(["workspace_id", "opportunity_id"], ["app.opportunities.workspace_id", "app.opportunities.id"]), {"schema": "app"})
    opportunity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    rule: Mapped[str] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(30))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SavedOpportunity(Scoped, Base):
    __tablename__ = "saved_opportunities"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", "opportunity_id"), ForeignKeyConstraint(["workspace_id", "opportunity_id"], ["app.opportunities.workspace_id", "app.opportunities.id"]), {"schema": "app"})
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    opportunity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowRun(Scoped, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (UniqueConstraint("workspace_id", "id"), UniqueConstraint("thread_id"), ForeignKeyConstraint(["workspace_id", "opportunity_id"], ["app.opportunities.workspace_id", "app.opportunities.id"]), ForeignKeyConstraint(["workspace_id", "job_id"], ["app.jobs.workspace_id", "app.jobs.id"]), {"schema": "app"})
    opportunity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    job_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    thread_id: Mapped[str] = mapped_column(String(200))
    graph_version: Mapped[str] = mapped_column(String(30), default="p1-v1")
    policy_version: Mapped[str] = mapped_column(String(30), default="p1-v1")
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    budget: Mapped[dict[str, Any]] = mapped_column(JSONB, default=lambda: {"paid_calls": 0, "limit": 0})
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    restore_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    requested_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowDecision(Scoped, Base):
    __tablename__ = "workflow_decisions"
    __table_args__ = (ForeignKeyConstraint(["workspace_id", "run_id"], ["app.workflow_runs.workspace_id", "app.workflow_runs.id"]), {"schema": "app"})
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    decision: Mapped[str] = mapped_column(String(30))
    payload_hash: Mapped[str] = mapped_column(String(64))
    content_version: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Scoped, Base):
    __tablename__ = "audit_events"
    __table_args__ = {"schema": "app"}
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    object_type: Mapped[str] = mapped_column(String(100))
    object_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class IdempotencyKey(Scoped, Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("workspace_id", "actor", "scope", "key"), {"schema": "app"})
    actor: Mapped[str] = mapped_column(String(200))
    scope: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
