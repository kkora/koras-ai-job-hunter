from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session

from kajh_api.models import (
    AuditEvent, EligibilityEvidence, Job, Opportunity, OpportunitySource,
    RawObservation, Source, SourceRun, VendorProfile, WorkflowRun, utcnow,
)
from kajh_domain import POLICY_VERSION, SyntheticAdapter, canonical_hash, dedupe_key, evaluate_eligibility, normalize_opportunity, validate_source_policy, vendor_hash

GRAPH_VERSION = "p1-v1"


def audit(db: Session, workspace_id: UUID, action: str, object_type: str, object_id: Any, details: dict | None = None):
    db.add(AuditEvent(workspace_id=workspace_id, actor="worker:synthetic", action=action,
                      object_type=object_type, object_id=str(object_id), details=details or {}))


PENDING_WORKFLOW_STATUSES = {"queued", "running", "waiting_review", "needs_review", "interrupted", "resume_requested", "approved", "test_pending"}


def hold_pending_workflows(db: Session, opportunity: Opportunity, reason: str):
    for run in db.scalars(select(WorkflowRun).where(WorkflowRun.opportunity_id == opportunity.id,
            WorkflowRun.restore_hold.is_(False), WorkflowRun.status.in_(PENDING_WORKFLOW_STATUSES))):
        run.restore_hold, run.status, run.updated_at = True, reason, utcnow()
        run.version += 1


def new_workflow(opportunity: Opportunity, *, job_id: UUID | None = None,
                 requested_by: UUID | None = None) -> WorkflowRun:
    return WorkflowRun(workspace_id=opportunity.workspace_id, opportunity_id=opportunity.id,
        job_id=job_id, requested_by=requested_by, thread_id=f"kajh-p1-{uuid4()}", graph_version=GRAPH_VERSION,
        policy_version=POLICY_VERSION, status="queued",
        budget={"paid_calls": 0, "limit": 0, "opportunity_version": opportunity.version})


def bound_workflow(db: Session, opportunity: Opportunity) -> WorkflowRun | None:
    for run in db.scalars(select(WorkflowRun).where(WorkflowRun.opportunity_id == opportunity.id,
            WorkflowRun.restore_hold.is_(False), WorkflowRun.graph_version == GRAPH_VERSION,
            WorkflowRun.policy_version == POLICY_VERSION)):
        bound = run.budget.get("opportunity_version")
        if type(bound) is int and bound == opportunity.version:
            return run
    return None


def validate_provenance(db: Session, opportunity: Opportunity, *, lock: bool = False):
    if lock:
        # Match the database review gate: sources, observations, opportunity,
        # then workflow. Registry locks also prevent ingestion adding a new link.
        list(db.scalars(select(Source).where(Source.workspace_id == opportunity.workspace_id)
            .order_by(Source.id).with_for_update(read=True).execution_options(populate_existing=True)))
        list(db.scalars(select(RawObservation).join(OpportunitySource,
            OpportunitySource.observation_id == RawObservation.id)
            .where(OpportunitySource.opportunity_id == opportunity.id)
            .order_by(RawObservation.id).with_for_update(read=True, of=RawObservation)
            .execution_options(populate_existing=True)))
    query = (select(Source, RawObservation)
        .join(OpportunitySource, Source.id == OpportunitySource.source_id)
        .join(RawObservation, RawObservation.id == OpportunitySource.observation_id)
        .where(OpportunitySource.opportunity_id == opportunity.id))
    provenance = list(db.execute(query.execution_options(populate_existing=True)))
    if not provenance:
        raise PermissionError("A permitted provenance source is required")
    for source, observation in provenance:
        if observation.expires_at <= utcnow():
            raise PermissionError("Current provenance evidence must be reconciled after expiry")
        try:
            validate_source_policy({"adapter_type": source.adapter_type, "enabled": source.enabled,
                "killed": source.killed, "permission": source.permission})
        except ValueError:
            raise PermissionError("Current provenance permission must be reconciled") from None


def reconcile_workflow_bindings(db: Session) -> int:
    """Hold legacy/stale pending graphs; never assign them an assumed version."""
    replaced = 0
    statuses = PENDING_WORKFLOW_STATUSES - {"test_pending"}
    candidates = list(db.scalars(select(WorkflowRun).where(WorkflowRun.restore_hold.is_(False),
        WorkflowRun.status.in_(statuses)).order_by(WorkflowRun.id)))
    for candidate in candidates:
        opportunity = db.get(Opportunity, candidate.opportunity_id)
        if not opportunity:
            continue
        permitted = True
        try:
            validate_provenance(db, opportunity, lock=True)
        except PermissionError:
            permitted = False
        opportunity = db.scalar(select(Opportunity).where(Opportunity.id == opportunity.id)
            .with_for_update().execution_options(populate_existing=True))
        run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == candidate.id)
            .with_for_update(skip_locked=True).execution_options(populate_existing=True))
        if not run or run.restore_hold or run.status not in statuses or opportunity is None:
            continue
        bound = run.budget.get("opportunity_version")
        if type(bound) is int and bound == opportunity.version:
            continue
        run.restore_hold, run.status, run.updated_at = True, "content_binding_required", utcnow()
        run.version += 1
        if opportunity.lifecycle != "open" or (opportunity.expires_at and opportunity.expires_at <= utcnow()):
            continue
        if not permitted:
            continue
        if bound_workflow(db, opportunity) is None:
            db.add(new_workflow(opportunity, requested_by=run.requested_by))
            audit(db, run.workspace_id, "workflow.binding_replaced", "workflow", run.id,
                  {"opportunity_version": opportunity.version})
            db.flush()
            replaced += 1
    return replaced


def claim_job(db: Session, *, lease_seconds: int = 60) -> tuple[UUID, UUID] | None:
    now = utcnow()
    while True:
        job = db.scalar(
            select(Job).where(
                or_(Job.status.in_(["queued", "retry"]), (Job.status == "running") & (Job.lease_until < now)),
                Job.available_at <= now,
            ).order_by(Job.available_at, Job.created_at).with_for_update(skip_locked=True).limit(1)
        )
        if job is None:
            return None
        if job.attempts >= job.max_attempts:
            job.status, job.error, job.lease_until = "dead_letter", "Retry limit exhausted", None
            audit(db, job.workspace_id, "job.dead_letter", "job", job.id)
            db.flush()
            continue
        job.status = "running"
        job.attempts += 1
        job.lease_token = uuid4()
        job.lease_until = now + timedelta(seconds=lease_seconds)
        job.updated_at = now
        db.flush()
        return job.id, job.lease_token


def fenced_job(db: Session, job_id: UUID, token: UUID) -> Job:
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job or job.status != "running" or job.lease_token != token or not job.lease_until or job.lease_until <= utcnow():
        raise PermissionError("Job lease is stale; processing is fenced")
    return job


def persist_record(db: Session, source: Source, run: SourceRun, raw: dict[str, Any], vendor: dict, retention_days: int) -> tuple[Opportunity, bool]:
    now = utcnow()
    record = normalize_opportunity(raw, str(source.id), observed_at=now)
    raw_hash = hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()
    observation = db.scalar(select(RawObservation).where(
        RawObservation.workspace_id == source.workspace_id, RawObservation.source_id == source.id,
        RawObservation.external_id == record["external_id"], RawObservation.payload_hash == raw_hash,
    ))
    if observation is None:
        observation = RawObservation(workspace_id=source.workspace_id, source_id=source.id,
            source_run_id=run.id, external_id=record["external_id"], payload_hash=raw_hash,
            payload=raw, observed_at=now, expires_at=now + timedelta(days=retention_days))
        db.add(observation)
        db.flush()
    else:
        observation.expires_at = now + timedelta(days=retention_days)
    link = db.scalar(select(OpportunitySource).where(
        OpportunitySource.workspace_id == source.workspace_id,
        OpportunitySource.source_id == source.id, OpportunitySource.external_id == record["external_id"],
    ))
    identity = dedupe_key(record)
    if identity:
        # Cross-source ingestion transactions serialize by explicit employer/requisition identity.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"), {"identity": identity})
    opportunity = db.get(Opportunity, link.opportunity_id) if link else None
    if opportunity is None and identity:
        opportunity = db.scalar(select(Opportunity).where(
            Opportunity.workspace_id == source.workspace_id,
            Opportunity.dedupe_key.is_not(None),
            Opportunity.organization_domain == record["employer"].get("domain"),
            Opportunity.facts["requisition_id"].astext == record.get("requisition_id"),
            Opportunity.category == record["category"],
            Opportunity.merged_into.is_(None), Opportunity.lifecycle != "purged",
        ).order_by(Opportunity.first_seen).with_for_update().limit(1))
    if (opportunity is not None and opportunity.dedupe_key is not None and identity and
            opportunity.organization_domain == record["employer"].get("domain") and
            opportunity.facts.get("requisition_id") == record.get("requisition_id")):
        # Preserve strict identity for old hashes without re-merging an Owner
        # split (which deliberately clears the key), or folding case-distinct IDs.
        opportunity.dedupe_key = identity
    new = opportunity is None
    current_hash = canonical_hash(record)
    changed = opportunity is None or opportunity.facts.get("_canonical_hash") != current_hash or opportunity.facts.get("_vendor_profile_hash") != vendor_hash(vendor)
    facts = {**record["facts"], "category": record["category"],
             "requisition_id": record.get("requisition_id"), "_canonical_hash": current_hash,
             "synthetic": True, "_source_evidence": record.get("evidence", []), "_vendor_profile_hash": vendor_hash(vendor)}
    assessment = evaluate_eligibility({**record, "facts": facts}, vendor)
    if opportunity is None:
        opportunity = Opportunity(workspace_id=source.workspace_id, title=record["title"],
            organization=record["employer"]["name"], organization_domain=record["employer"].get("domain"),
            category=record["category"], description=record["description"], facts=facts,
            skills=record["skills"], eligibility=assessment["status"], workplace=facts.get("workplace_type", "Unknown"),
            country=(facts.get("delivery_countries") or [None])[0], dedupe_key=identity,
            first_seen=now, last_seen=now, expires_at=observation.expires_at)
        db.add(opportunity)
        db.flush()
    elif changed:
        # Source evidence remains in observations even when the canonical version changes.
        opportunity.title, opportunity.description = record["title"], record["description"]
        opportunity.organization = record["employer"]["name"]
        opportunity.organization_domain = record["employer"].get("domain")
        opportunity.country = (facts.get("delivery_countries") or [None])[0]
        opportunity.facts, opportunity.skills = facts, record["skills"]
        opportunity.category, opportunity.eligibility = record["category"], assessment["status"]
        opportunity.workplace = facts.get("workplace_type", "Unknown")
        opportunity.version += 1
        hold_pending_workflows(db, opportunity, "content_changed")
    assert opportunity is not None
    opportunity.last_seen = now
    opportunity.expires_at = max(opportunity.expires_at or now, observation.expires_at)
    opportunity.lifecycle = "closed" if any(r["rule_id"] == "closing_date" and r["status"] == "Ineligible" for r in assessment["rules"]) else "open"
    if link is None:
        link = OpportunitySource(workspace_id=source.workspace_id, opportunity_id=opportunity.id,
            source_id=source.id, observation_id=observation.id, external_id=record["external_id"],
            original_url=record["source_ref"] or f"https://example.invalid/fixtures/{record['external_id']}", observed_at=now)
        db.add(link)
    else:
        link.observation_id, link.observed_at = observation.id, now
    if changed:
        for rule in assessment["rules"]:
            db.add(EligibilityEvidence(workspace_id=source.workspace_id, opportunity_id=opportunity.id,
                rule=rule["rule_id"], outcome=rule["status"],
                details={**rule, "content_version": opportunity.version, "policy_version": POLICY_VERSION,
                         "source_id": str(source.id), "observation_id": str(observation.id)}))
        audit(db, source.workspace_id, "opportunity.normalized", "opportunity", opportunity.id,
              {"version": opportunity.version, "eligibility": opportunity.eligibility, "synthetic": True})
    db.flush()
    return opportunity, new


def ingest_job(db: Session, job_id: UUID, lease_token: UUID) -> dict[str, Any]:
    job = fenced_job(db, job_id, lease_token)
    source = db.scalar(select(Source).where(Source.id == job.source_id).with_for_update())
    if not source:
        raise ValueError("Source does not exist in the authorized workspace")
    policy = validate_source_policy({"adapter_type": source.adapter_type, "enabled": source.enabled,
        "killed": source.killed, "permission": source.permission})
    if job.payload.get("simulate_failure") == "permanent":
        raise ValueError("Synthetic permanent adapter failure")
    if job.payload.get("simulate_failure") == "transient" and job.attempts < 2:
        raise ConnectionError("Synthetic transient adapter failure")
    source_run = SourceRun(workspace_id=source.workspace_id, source_id=source.id, job_id=job.id,
                           status="running", cursor_before=source.cursor, counts={})
    db.add(source_run)
    db.flush()
    vendor = db.scalar(select(VendorProfile).where(VendorProfile.workspace_id == source.workspace_id)
                       .order_by(VendorProfile.version.desc()).limit(1))
    profile = vendor.profile if vendor else {}
    # A source's fixture anchor is immutable, so replay yields identical observations.
    anchor = source.created_at.replace(hour=0, minute=0, second=0, microsecond=0)
    adapter = SyntheticAdapter(now=anchor)
    cursor = source.cursor
    counts = {"observed": 0, "created": 0, "deduplicated": 0, "pages": 0}
    opportunity_ids: set[UUID] = set()
    request_pages = 0
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    prior_pages = db.scalar(select(func.coalesce(func.sum(SourceRun.counts["pages"].as_integer()), 0))
        .where(SourceRun.source_id == source.id, SourceRun.started_at >= day_start)) or 0
    while True:
        validate_source_policy({"adapter_type": source.adapter_type, "enabled": source.enabled,
            "killed": source.killed, "permission": source.permission})
        page_offset = int(cursor or "0")
        page = adapter.collect(cursor, page_size=4)
        request_pages += 1
        if request_pages + prior_pages > policy["quota_per_day"]:
            raise PermissionError("Source quota would be exceeded")
        for raw in page.items:
            namespace = policy.get("fixture_namespace")
            if namespace is not None:
                if not isinstance(namespace, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", namespace):
                    raise ValueError("Invalid synthetic fixture namespace")
                raw = dict(raw)
                raw["employer_domain"] = f"{namespace}.example.invalid"
            opportunity, new = persist_record(db, source, source_run, raw, profile, policy["retention_days"])
            counts["observed"] += 1
            counts["created"] += int(new)
            counts["deduplicated"] += int(not new)
            opportunity_ids.add(opportunity.id)
        cursor = page.next_cursor if not page.exhausted else str(page_offset + len(page.items))
        if page.exhausted:
            break
    # Recheck rights and lease immediately before the transaction commits its cursor.
    validate_source_policy({"adapter_type": source.adapter_type, "enabled": source.enabled,
        "killed": source.killed, "permission": source.permission})
    fenced_job(db, job_id, lease_token)
    source.cursor = cursor
    counts["pages"] = request_pages
    source_run.status, source_run.finished_at = "succeeded", utcnow()
    source_run.cursor_after, source_run.counts = cursor, counts
    run_ids = []
    for opportunity_id in sorted(opportunity_ids, key=str):
        current_opportunity = db.get(Opportunity, opportunity_id)
        if current_opportunity is None:
            raise ValueError("Ingested opportunity is unavailable")
        existing = bound_workflow(db, current_opportunity)
        if not existing:
            workflow = new_workflow(current_opportunity, job_id=job.id)
            db.add(workflow)
            db.flush()
            run_ids.append(str(workflow.id))
    job.status, job.lease_until, job.updated_at = "completed", None, utcnow()
    job.result = {**counts, "source_run_id": str(source_run.id), "workflow_run_ids": run_ids}
    audit(db, source.workspace_id, "source.ingested", "source", source.id, counts)
    db.flush()
    return dict(job.result)


def finish_failure(db: Session, job_id: UUID, token: UUID, error: Exception):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job or job.status != "running" or job.lease_token != token:
        return
    job.error = f"{type(error).__name__}: Local synthetic processing failed"
    job.lease_until, job.updated_at = None, utcnow()
    job.status = "blocked" if isinstance(error, PermissionError) or type(error).__name__ == "PolicyDenied" else "dead_letter" if job.attempts >= job.max_attempts else "retry"
    job.available_at = utcnow() + timedelta(seconds=min(2 ** job.attempts, 30))
    audit(db, job.workspace_id, f"job.{job.status}", "job", job.id,
          {"attempts": job.attempts, "error_type": type(error).__name__})
    if job.source_id:
        db.add(SourceRun(workspace_id=job.workspace_id, source_id=job.source_id, job_id=job.id,
            status="failed", finished_at=utcnow(), error=job.error, counts={}))


def purge_expired(db: Session) -> dict[str, int]:
    now = utcnow()
    expired_source_ids = set()
    for source in db.scalars(select(Source)):
        try:
            validate_source_policy({"adapter_type": source.adapter_type, "enabled": source.enabled,
                "killed": source.killed, "permission": source.permission}, now=now)
        except ValueError:
            expired_source_ids.add(source.id)
    predicate = RawObservation.expires_at <= now
    if expired_source_ids:
        predicate = or_(predicate, RawObservation.source_id.in_(expired_source_ids))
    observations = list(db.scalars(select(RawObservation).where(predicate)))
    affected: set[UUID] = set()
    for observation in observations:
        # A refreshed source link can point at a newer observation while older
        # versioned rules still retain excerpts from this historical payload.
        historical = list(db.scalars(select(EligibilityEvidence).where(
            EligibilityEvidence.details["observation_id"].astext == str(observation.id))))
        affected.update(evidence.opportunity_id for evidence in historical)
        links = list(db.scalars(select(OpportunitySource).where(OpportunitySource.observation_id == observation.id)))
        for link in links:
            affected.add(link.opportunity_id)
            db.delete(link)
        db.flush()
        db.delete(observation)
    db.flush()
    purged = 0
    for opportunity_id in affected:
        opportunity = db.get(Opportunity, opportunity_id)
        if not opportunity:
            continue
        remaining = None
        for link in db.scalars(select(OpportunitySource).where(OpportunitySource.opportunity_id == opportunity_id)
                               .order_by(OpportunitySource.observed_at.desc())):
            candidate_observation = db.get(RawObservation, link.observation_id)
            candidate_source = db.get(Source, link.source_id)
            if not candidate_observation or candidate_observation.expires_at <= now or not candidate_source:
                continue
            try:
                validate_source_policy({"adapter_type": candidate_source.adapter_type, "enabled": candidate_source.enabled,
                    "killed": candidate_source.killed, "permission": candidate_source.permission})
            except ValueError:
                continue
            remaining = (candidate_source, candidate_observation)
            break
        # No assessment derived from removed evidence is kept merely because another link exists.
        db.execute(delete(EligibilityEvidence).where(EligibilityEvidence.opportunity_id == opportunity_id))
        for run in db.scalars(select(WorkflowRun).where(WorkflowRun.opportunity_id == opportunity_id)):
            run.restore_hold, run.status = True, "evidence_expired"
        if remaining is None:
            opportunity.title, opportunity.organization = "Expired synthetic opportunity", "Removed"
            opportunity.organization_domain, opportunity.country, opportunity.dedupe_key = None, None, None
            opportunity.workplace = "Unknown"
            opportunity.description, opportunity.facts, opportunity.skills = "", {"expired": True, "synthetic": True}, []
            opportunity.eligibility, opportunity.lifecycle = "Unknown", "purged"
            opportunity.version += 1
            audit(db, opportunity.workspace_id, "opportunity.retention_purged", "opportunity", opportunity.id)
            purged += 1
        else:
            source, observation = remaining
            record = normalize_opportunity(observation.payload, str(source.id), observed_at=observation.observed_at)
            vendor = db.scalar(select(VendorProfile).where(VendorProfile.workspace_id == opportunity.workspace_id)
                               .order_by(VendorProfile.version.desc()).limit(1))
            profile = vendor.profile if vendor else {}
            facts = {**record["facts"], "category": record["category"], "requisition_id": record.get("requisition_id"),
                     "_canonical_hash": canonical_hash(record), "_vendor_profile_hash": vendor_hash(profile),
                     "synthetic": True, "_source_evidence": record.get("evidence", [])}
            assessment = evaluate_eligibility({**record, "facts": facts}, profile)
            opportunity.title, opportunity.description = record["title"], record["description"]
            opportunity.organization, opportunity.organization_domain = record["employer"]["name"], record["employer"].get("domain")
            opportunity.facts, opportunity.skills, opportunity.eligibility = facts, record["skills"], assessment["status"]
            opportunity.workplace = facts.get("workplace_type", "Unknown")
            opportunity.country = (facts.get("delivery_countries") or [None])[0]
            opportunity.expires_at = observation.expires_at
            opportunity.lifecycle = "closed" if any(rule["rule_id"] == "closing_date" and
                rule["status"] == "Ineligible" for rule in assessment["rules"]) else "open"
            opportunity.version += 1
            for rule in assessment["rules"]:
                db.add(EligibilityEvidence(workspace_id=opportunity.workspace_id, opportunity_id=opportunity.id,
                    rule=rule["rule_id"], outcome=rule["status"], details={**rule,
                    "content_version": opportunity.version, "policy_version": POLICY_VERSION,
                    "source_id": str(source.id), "observation_id": str(observation.id)}))
            db.add(new_workflow(opportunity))
            audit(db, opportunity.workspace_id, "opportunity.retention_rebuilt", "opportunity", opportunity.id,
                  {"source_id": str(source.id), "version": opportunity.version})
    return {"observations": len(observations), "opportunities": purged}


def reassess_workspace(db: Session, workspace_id: UUID) -> dict[str, int]:
    # A profile change never grants renewed use of source material. Apply the
    # same current rights/retention purge before deriving another assessment.
    purge_expired(db)
    vendor = db.scalar(select(VendorProfile).where(VendorProfile.workspace_id == workspace_id)
                       .order_by(VendorProfile.version.desc()).limit(1))
    profile = vendor.profile if vendor else {}
    reassessed = 0
    for opportunity in db.scalars(select(Opportunity).where(Opportunity.workspace_id == workspace_id,
            Opportunity.lifecycle == "open", Opportunity.merged_into.is_(None)).with_for_update()):
        if opportunity.expires_at and opportunity.expires_at <= utcnow():
            continue
        if db.scalar(select(OpportunitySource.id).where(
                OpportunitySource.opportunity_id == opportunity.id).limit(1)) is None:
            continue
        facts = {**opportunity.facts, "category": opportunity.category, "_vendor_profile_hash": vendor_hash(profile)}
        assessment = evaluate_eligibility(facts, profile)
        opportunity.facts, opportunity.eligibility = facts, assessment["status"]
        opportunity.version += 1
        for rule in assessment["rules"]:
            db.add(EligibilityEvidence(workspace_id=workspace_id, opportunity_id=opportunity.id,
                rule=rule["rule_id"], outcome=rule["status"], details={**rule,
                "content_version": opportunity.version, "policy_version": POLICY_VERSION,
                "vendor_profile_version": vendor.version if vendor else None}))
        hold_pending_workflows(db, opportunity, "profile_changed")
        db.add(new_workflow(opportunity))
        reassessed += 1
    audit(db, workspace_id, "workspace.reassessed", "vendor_profile", vendor.id if vendor else "none",
          {"opportunities": reassessed, "profile_version": vendor.version if vendor else None})
    return {"reassessed": reassessed}
