"""Restricted-worker ingestion checks against demonstrably owned synthetic resources."""
from __future__ import annotations

import hashlib
import asyncio
import os
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from kajh_api.models import (
    AuditEvent, EligibilityEvidence, Job, Membership, Opportunity, OpportunitySource, RawObservation,
    SavedOpportunity, Source, SourcePermission, SourceRun, WorkflowDecision, WorkflowRun, utcnow,
)
from kajh_domain import POLICY_VERSION, SyntheticAdapter, synthetic_permission, synthetic_vendor_profile
from kajh_domain.adapters import AdapterPage
from kajh_domain.policy import PolicyDenied
from kajh_worker.config import session_factory
from kajh_worker.ingestion import GRAPH_VERSION, fenced_job, finish_failure, ingest_job, persist_record, purge_expired, reassess_workspace, reconcile_workflow_bindings
from kajh_worker.workflow import current, finish, validate

pytestmark = pytest.mark.integration
WORKSPACE = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def lab():
    if os.environ.get("KAJH_TEST_RUNTIME") != "1":
        pytest.skip("Owned local runtime was not requested")
    admin = create_engine(os.environ["DATABASE_ADMIN_URL"], hide_parameters=True)
    source_ids = []
    owned_opportunity_ids = set()
    namespace = uuid4().hex

    def source(*, same_namespace=True, **policy_changes):
        permission = synthetic_permission()
        permission.update(fixture_namespace=namespace if same_namespace else uuid4().hex, **policy_changes)
        with Session(admin) as db, db.begin():
            row = Source(workspace_id=WORKSPACE, name="Synthetic ingestion test " + uuid4().hex,
                adapter_type="synthetic", enabled=True, permission=permission)
            db.add(row)
            db.flush()
            source_ids.append(row.id)
            return row.id

    def leased_job(source_id, *, attempts=1, max_attempts=3, **payload):
        token = uuid4()
        with Session(admin) as db, db.begin():
            row = Job(workspace_id=WORKSPACE, source_id=source_id, kind="ingest", payload=payload,
                idempotency_key="test:" + uuid4().hex, request_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
                status="running", attempts=attempts, max_attempts=max_attempts, lease_token=token,
                lease_until=utcnow() + timedelta(minutes=2))
            db.add(row)
            db.flush()
            return row.id, token

    def ingest(job_id, token):
        with session_factory()() as db, db.begin():
            result = ingest_job(db, job_id, token)
            job = db.get(Job, job_id)
            owned_opportunity_ids.update(db.scalars(select(OpportunitySource.opportunity_id)
                .where(OpportunitySource.source_id == job.source_id)))
            # These test cases exercise ingestion separately from background workflow execution.
            for run in db.scalars(select(WorkflowRun).where(WorkflowRun.job_id == job_id)):
                if run.status == "queued":
                    run.status = "test_pending"
            return result

    yield admin, source, leased_job, ingest

    with Session(admin) as db, db.begin():
        links = list(db.scalars(select(OpportunitySource).where(OpportunitySource.source_id.in_(source_ids))))
        opportunity_ids = list({link.opportunity_id for link in links})
        # Purged records no longer have links; their unique synthetic namespace is retained
        # only in organization_domain, never in an unrelated fixture's identity.
        opportunity_ids += list(db.scalars(select(Opportunity.id).where(
            Opportunity.organization_domain == f"{namespace}.example.invalid")))
        opportunity_ids = list(set(opportunity_ids) | owned_opportunity_ids)
        runs = list(db.scalars(select(WorkflowRun).where(WorkflowRun.opportunity_id.in_(opportunity_ids))))
        run_ids, threads = [run.id for run in runs], [run.thread_id for run in runs]
        job_ids = list(db.scalars(select(Job.id).where(Job.source_id.in_(source_ids))))
        db.execute(delete(WorkflowDecision).where(WorkflowDecision.run_id.in_(run_ids)))
        db.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(run_ids)))
        db.execute(delete(EligibilityEvidence).where(EligibilityEvidence.opportunity_id.in_(opportunity_ids)))
        db.execute(delete(SavedOpportunity).where(SavedOpportunity.opportunity_id.in_(opportunity_ids)))
        db.execute(delete(OpportunitySource).where(OpportunitySource.source_id.in_(source_ids)))
        db.execute(delete(RawObservation).where(RawObservation.source_id.in_(source_ids)))
        db.execute(delete(SourceRun).where(SourceRun.source_id.in_(source_ids)))
        db.execute(delete(Job).where(Job.source_id.in_(source_ids)))
        db.execute(delete(Opportunity).where(Opportunity.id.in_(opportunity_ids)))
        db.execute(delete(SourcePermission).where(SourcePermission.source_id.in_(source_ids)))
        db.execute(delete(Source).where(Source.id.in_(source_ids)))
        object_ids = [str(value) for value in source_ids + opportunity_ids + run_ids + job_ids]
        db.execute(delete(AuditEvent).where(AuditEvent.object_id.in_(object_ids)))
        if threads:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                db.execute(text(f"DELETE FROM checkpoints.{table} WHERE thread_id = ANY(:threads)"), {"threads": threads})
    admin.dispose()


def test_idempotent_cursor_replay_conservative_dedup_and_provenance(lab):
    admin, source, leased_job, ingest = lab
    first_source = source()
    first_job, token = leased_job(first_source)
    result = ingest(first_job, token)
    assert result["observed"] == 14
    assert result["created"] == 13
    assert result["deduplicated"] == 1
    with Session(admin) as db:
        assert db.get(Source, first_source).cursor == "14"
        rows = db.scalars(select(RawObservation).where(RawObservation.source_id == first_source)).all()
        assert len(rows) == 14
        links = db.scalars(select(OpportunitySource).where(OpportunitySource.source_id == first_source)).all()
        assert len(links) == 14
        ids = {link.opportunity_id for link in links}
        opportunities = db.scalars(select(Opportunity).where(Opportunity.id.in_(ids))).all()
        assert len(opportunities) == 13
        assert {row.eligibility for row in opportunities} == {"Eligible", "Ineligible", "Unknown", "Needs Review"}
        versions = {row.id: row.version for row in opportunities}
        same_title = [row for row in opportunities if row.title == "Power Platform Solution Architect — synthetic SOW"]
        assert len(same_title) == 2
    second_job, token = leased_job(first_source)
    assert ingest(second_job, token)["observed"] == 0
    with Session(admin) as db, db.begin():
        db.get(Source, first_source).cursor = None
    replay_job, token = leased_job(first_source)
    replay = ingest(replay_job, token)
    assert replay["created"] == 0
    assert replay["observed"] == 14
    with Session(admin) as db:
        assert len(db.scalars(select(RawObservation).where(RawObservation.source_id == first_source)).all()) == 14
        assert {row.id: row.version for row in db.scalars(select(Opportunity).where(Opportunity.id.in_(ids))).all()} == versions
    other_source = source()
    other_job, token = leased_job(other_source)
    assert ingest(other_job, token)["created"] == 0
    with Session(admin) as db:
        other_ids = {link.opportunity_id for link in db.scalars(select(OpportunitySource).where(OpportunitySource.source_id == other_source))}
        assert other_ids == ids


@pytest.mark.parametrize("change", [{"revoked": True}, {"commercial_use": False}, {"persistent_storage": False}])
def test_policy_revocation_prevents_collection_and_cursor_change(lab, change):
    admin, source, leased_job, ingest = lab
    source_id = source(**change)
    job_id, token = leased_job(source_id)
    with pytest.raises(PolicyDenied):
        ingest(job_id, token)
    with Session(admin) as db:
        assert db.get(Source, source_id).cursor is None
        assert db.scalars(select(RawObservation).where(RawObservation.source_id == source_id)).all() == []


def test_lease_fencing_rejects_an_old_worker_without_business_writes(lab):
    admin, source, leased_job, ingest = lab
    source_id = source()
    job_id, token = leased_job(source_id)
    with Session(admin) as db, db.begin():
        db.get(Job, job_id).lease_token = uuid4()
    with pytest.raises(PermissionError, match="fenced"):
        ingest(job_id, token)
    with Session(admin) as db:
        assert db.get(Source, source_id).cursor is None
        assert not db.scalars(select(RawObservation).where(RawObservation.source_id == source_id)).all()


def test_retry_limit_dead_letter_and_error_redaction(lab):
    admin, source, leased_job, _ = lab
    source_id = source()
    job_id, token = leased_job(source_id, attempts=3)
    with session_factory()() as db, db.begin():
        finish_failure(db, job_id, token, RuntimeError("DO-NOT-STORE-secret-value"))
    with Session(admin) as db:
        job = db.get(Job, job_id)
        assert job.status == "dead_letter"
        assert "DO-NOT-STORE" not in job.error
        assert job.lease_until is None
    with session_factory()() as db, db.begin():
        with pytest.raises(PermissionError):
            fenced_job(db, job_id, token)


def test_retention_purges_derived_evidence_and_holds_checkpoints(lab):
    admin, source, leased_job, ingest = lab
    source_id = source()
    job_id, token = leased_job(source_id)
    ingest(job_id, token)
    with Session(admin) as db, db.begin():
        ids = {link.opportunity_id for link in db.scalars(select(OpportunitySource).where(OpportunitySource.source_id == source_id))}
        for observation in db.scalars(select(RawObservation).where(RawObservation.source_id == source_id)):
            observation.expires_at = utcnow() - timedelta(seconds=1)
    with session_factory()() as db, db.begin():
        result = purge_expired(db)
    assert result["observations"] == 14
    assert result["opportunities"] == 13
    with Session(admin) as db:
        assert not db.scalars(select(RawObservation).where(RawObservation.source_id == source_id)).all()
        for opportunity in db.scalars(select(Opportunity).where(Opportunity.id.in_(ids))):
            assert opportunity.lifecycle == "purged"
            assert opportunity.description == ""
            assert opportunity.eligibility == "Unknown"
            assert opportunity.title == "Expired synthetic opportunity"
            assert opportunity.organization_domain is None
            assert opportunity.country is None
            assert opportunity.dedupe_key is None
        assert not db.scalars(select(EligibilityEvidence).where(EligibilityEvidence.opportunity_id.in_(ids))).all()
        assert all(run.restore_hold for run in db.scalars(select(WorkflowRun).where(WorkflowRun.opportunity_id.in_(ids))))


def test_multi_source_retention_rebuilds_from_only_retained_evidence(lab):
    admin, source, leased_job, ingest = lab
    first, second = source(), source()
    job, token = leased_job(first)
    ingest(job, token)
    job, token = leased_job(second)
    ingest(job, token)
    with Session(admin) as db, db.begin():
        ids = {link.opportunity_id for link in db.scalars(select(OpportunitySource).where(OpportunitySource.source_id == first))}
        for observation in db.scalars(select(RawObservation).where(RawObservation.source_id == first)):
            observation.expires_at = utcnow() - timedelta(seconds=1)
    with session_factory()() as db, db.begin():
        result = purge_expired(db)
        # Hold newly queued test workflows before background workers can observe them.
        for run in db.scalars(select(WorkflowRun).where(WorkflowRun.opportunity_id.in_(ids), WorkflowRun.status == "queued")):
            run.status = "test_pending"
    assert result["observations"] == 14
    assert result["opportunities"] == 0
    with Session(admin) as db:
        assert all(row.lifecycle in {"open", "closed"} for row in db.scalars(select(Opportunity).where(Opportunity.id.in_(ids))))
        remaining = db.scalars(select(EligibilityEvidence).where(EligibilityEvidence.opportunity_id.in_(ids))).all()
        assert remaining
        assert all(row.details["source_id"] == str(second) for row in remaining)


def test_expired_unlinked_historical_observation_cannot_retain_rule_excerpts(lab):
    admin, source, leased_job, ingest = lab
    source_id = source()
    job_id, token = leased_job(source_id)
    ingest(job_id, token)
    with Session(admin) as db:
        link = db.scalar(select(OpportunitySource).where(OpportunitySource.source_id == source_id)
            .order_by(OpportunitySource.external_id))
        opportunity_id, old_observation_id = link.opportunity_id, link.observation_id
    with session_factory()() as db, db.begin():
        old = db.get(RawObservation, old_observation_id)
        updated_payload = {**old.payload, "description": "Updated synthetic evidence only."}
        persist_record(db, db.get(Source, source_id), db.get(SourceRun, old.source_run_id),
            updated_payload, synthetic_vendor_profile(), 30)
        old.expires_at = utcnow() - timedelta(seconds=1)
    with Session(admin) as db:
        assert db.scalar(select(OpportunitySource).where(OpportunitySource.opportunity_id == opportunity_id,
            OpportunitySource.observation_id == old_observation_id)) is None
        assert db.scalars(select(EligibilityEvidence).where(EligibilityEvidence.opportunity_id == opportunity_id,
            EligibilityEvidence.details["observation_id"].astext == str(old_observation_id))).all()
    with session_factory()() as db, db.begin():
        result = purge_expired(db)
        for run in db.scalars(select(WorkflowRun).where(WorkflowRun.opportunity_id == opportunity_id,
                WorkflowRun.status == "queued")):
            run.status = "test_pending"
    assert result["observations"] == 1
    with Session(admin) as db:
        assert db.get(RawObservation, old_observation_id) is None
        evidence = db.scalars(select(EligibilityEvidence).where(EligibilityEvidence.opportunity_id == opportunity_id)).all()
        assert evidence
        assert all(row.details["observation_id"] != str(old_observation_id) for row in evidence)
        assert db.get(Opportunity, opportunity_id).description == "Updated synthetic evidence only."


@pytest.mark.parametrize("rights_change", [{"revoked": True}, {"persistent_storage": False}])
def test_revoked_retention_rights_remove_existing_raw_and_derived_content(lab, rights_change):
    admin, source, leased_job, ingest = lab
    source_id = source()
    job_id, token = leased_job(source_id)
    ingest(job_id, token)
    with Session(admin) as db, db.begin():
        row = db.get(Source, source_id)
        row.permission = {**row.permission, **rights_change}
        ids = set(db.scalars(select(OpportunitySource.opportunity_id)
            .where(OpportunitySource.source_id == source_id)))
    with session_factory()() as db, db.begin():
        assert purge_expired(db)["observations"] == 14
    with Session(admin) as db:
        assert not db.scalars(select(RawObservation).where(RawObservation.source_id == source_id)).all()
        assert not db.scalars(select(EligibilityEvidence).where(EligibilityEvidence.opportunity_id.in_(ids))).all()
        assert all(row.lifecycle == "purged" for row in db.scalars(select(Opportunity).where(Opportunity.id.in_(ids))))


def test_database_decision_gate_rechecks_revocation_at_atomic_consumption(lab):
    admin, source, leased_job, ingest = lab
    source_id = source()
    job_id, token = leased_job(source_id)
    ingest(job_id, token)
    with Session(admin) as db, db.begin():
        opportunity = db.scalar(select(Opportunity).join(OpportunitySource,
            OpportunitySource.opportunity_id == Opportunity.id).where(
            OpportunitySource.source_id == source_id, Opportunity.eligibility == "Unknown"))
        owner = db.scalar(select(Membership).where(Membership.workspace_id == WORKSPACE,
            Membership.role == "Owner", Membership.active.is_(True)))
        run = WorkflowRun(workspace_id=WORKSPACE, opportunity_id=opportunity.id,
            thread_id="kajh-p1-test-" + uuid4().hex, graph_version=GRAPH_VERSION,
            policy_version=POLICY_VERSION, status="waiting_review", requested_by=owner.user_id,
            budget={"paid_calls": 0, "limit": 0, "opportunity_version": opportunity.version})
        db.add(run)
        db.flush()
        digest = hashlib.sha256(f"{run.thread_id}:{opportunity.version}:review".encode()).hexdigest()
        decision = WorkflowDecision(workspace_id=WORKSPACE, run_id=run.id, user_id=owner.user_id,
            decision="approved", payload_hash=digest, content_version=opportunity.version,
            expires_at=utcnow() + timedelta(minutes=10))
        db.add(decision)
        db.flush()
        run_id, decision_id, version = run.id, decision.id, opportunity.version
        row = db.get(Source, source_id)
        row.permission = {**row.permission, "revoked": True}
    # Recheck at the database authority boundary after a prior Python check
    # could have passed but before the local approval marker is committed.
    with session_factory()() as db, db.begin():
        with pytest.raises(DBAPIError) as denied:
            db.execute(text("SELECT app.consume_workflow_decision(:run,:decision,:hash,:version)"),
                {"run": run_id, "decision": decision_id, "hash": digest, "version": version})
        assert denied.value.orig.sqlstate == "42501"
        db.rollback()
    with Session(admin) as db:
        assert db.get(WorkflowDecision, decision_id).consumed is False
        assert db.get(WorkflowRun, run_id).status != "completed"
        assert not db.scalars(select(AuditEvent).where(AuditEvent.object_id == str(run_id),
            AuditEvent.action == "workflow.local_review_consumed")).all()


def test_profile_reassessment_cannot_reuse_revoked_source_evidence(lab):
    admin, source, leased_job, ingest = lab
    source_id = source()
    job_id, token = leased_job(source_id)
    ingest(job_id, token)
    with Session(admin) as db, db.begin():
        ids = set(db.scalars(select(OpportunitySource.opportunity_id)
            .where(OpportunitySource.source_id == source_id)))
        row = db.get(Source, source_id)
        row.permission = {**row.permission, "revoked": True}
    with session_factory()() as db, db.begin():
        # Other permitted workspace fixtures can still be assessed. This test's
        # revoked records must be purged before any new derived rules are made.
        # Roll back the probe so existing workspace assessments are unchanged.
        reassess_workspace(db, WORKSPACE)
        db.flush()
        assert all(row.lifecycle == "purged" for row in db.scalars(select(Opportunity).where(Opportunity.id.in_(ids))))
        assert not db.scalars(select(EligibilityEvidence).where(EligibilityEvidence.opportunity_id.in_(ids))).all()
        assert all(row.restore_hold for row in db.scalars(select(WorkflowRun).where(WorkflowRun.opportunity_id.in_(ids))))
        db.rollback()


@pytest.mark.parametrize("invalid_link", ["source_revoked", "observation_expired"])
def test_workflow_processing_denies_any_invalid_current_provenance_link(lab, invalid_link):
    admin, source, leased_job, ingest = lab
    first, second = source(), source()
    for source_id in (first, second):
        job_id, token = leased_job(source_id)
        ingest(job_id, token)
    with Session(admin) as db, db.begin():
        opportunity = db.scalar(select(Opportunity).join(OpportunitySource,
            OpportunitySource.opportunity_id == Opportunity.id).where(
            OpportunitySource.source_id == first, Opportunity.eligibility == "Unknown"))
        run = WorkflowRun(workspace_id=WORKSPACE, opportunity_id=opportunity.id,
            thread_id="kajh-p1-test-" + uuid4().hex, graph_version=GRAPH_VERSION,
            policy_version=POLICY_VERSION, status="test_pending",
            budget={"paid_calls": 0, "limit": 0, "opportunity_version": opportunity.version})
        db.add(run)
        db.flush()
        run_id = run.id
        first_link = db.scalar(select(OpportunitySource).where(
            OpportunitySource.source_id == first, OpportunitySource.opportunity_id == opportunity.id))
        first_observation_id = first_link.observation_id
        assert db.scalar(select(OpportunitySource).where(
            OpportunitySource.source_id == second, OpportunitySource.opportunity_id == opportunity.id))
    assert current(run_id)[0].id == run_id
    with Session(admin) as db, db.begin():
        if invalid_link == "source_revoked":
            row = db.get(Source, first)
            row.permission = {**row.permission, "revoked": True}
        else:
            db.get(RawObservation, first_observation_id).expires_at = utcnow() - timedelta(seconds=1)
    # The valid second link cannot authorize processing stale canonical terms
    # derived from the revoked/expired first link before retention rebuilds it.
    with pytest.raises(PermissionError, match="reconciled"):
        current(run_id)
    with Session(admin) as db:
        assert db.get(WorkflowRun, run_id).budget.get("mock_attempts", 0) == 0


def test_refreshed_canonical_holds_old_workflow_and_queues_one_bound_replacement(lab, monkeypatch):
    admin, source, leased_job, ingest = lab
    source_id = source()
    job_id, token = leased_job(source_id)
    ingest(job_id, token)
    with Session(admin) as db:
        link = db.scalar(select(OpportunitySource).join(Opportunity,
            Opportunity.id == OpportunitySource.opportunity_id).where(
            OpportunitySource.source_id == source_id, Opportunity.eligibility == "Unknown"))
        opportunity_id, external_id = link.opportunity_id, link.external_id
        old = db.scalar(select(WorkflowRun).where(WorkflowRun.opportunity_id == opportunity_id))
        old_id, old_version = old.id, db.get(Opportunity, opportunity_id).version
    original = SyntheticAdapter.collect

    def refreshed(adapter, cursor=None, *, page_size=4):
        page = original(adapter, cursor, page_size=page_size)
        return AdapterPage([{**raw, "description": raw["description"] + " Revised synthetic terms."}
            if raw["external_id"] == external_id else raw for raw in page.items], page.next_cursor, page.exhausted)

    monkeypatch.setattr(SyntheticAdapter, "collect", refreshed)
    with Session(admin) as db, db.begin():
        db.get(Source, source_id).cursor = None
    job_id, token = leased_job(source_id)
    result = ingest(job_id, token)
    assert len(result["workflow_run_ids"]) == 1
    with Session(admin) as db:
        opportunity = db.get(Opportunity, opportunity_id)
        assert opportunity.version == old_version + 1
        assert db.get(WorkflowRun, old_id).restore_hold is True
        assert db.get(WorkflowRun, old_id).status == "content_changed"
        replacement = db.get(WorkflowRun, UUID(result["workflow_run_ids"][0]))
        assert replacement.budget["opportunity_version"] == opportunity.version
    with pytest.raises(PermissionError):
        current(old_id)
    with Session(admin) as db, db.begin():
        db.get(Source, source_id).cursor = None
    replay, token = leased_job(source_id)
    assert ingest(replay, token)["workflow_run_ids"] == []


def test_strict_requisition_identity_reconciles_legacy_hash_without_duplicate_canonical(lab):
    admin, source, leased_job, ingest = lab
    first, second = source(), source()
    job_id, token = leased_job(first)
    ingest(job_id, token)
    with Session(admin) as db, db.begin():
        ids = set(db.scalars(select(OpportunitySource.opportunity_id).where(OpportunitySource.source_id == first)))
        for opportunity in db.scalars(select(Opportunity).where(Opportunity.id.in_(ids))):
            old_identity = f"{opportunity.organization_domain}|{opportunity.facts['requisition_id'].casefold()}|{opportunity.category}"
            opportunity.dedupe_key = hashlib.sha256(old_identity.encode()).hexdigest()
    job_id, token = leased_job(second)
    assert ingest(job_id, token)["created"] == 0
    with Session(admin) as db:
        assert set(db.scalars(select(OpportunitySource.opportunity_id).where(OpportunitySource.source_id == second))) == ids


@pytest.mark.parametrize("revoked", [False, True])
def test_legacy_binding_replacement_requires_current_permitted_evidence(lab, revoked):
    admin, source, leased_job, ingest = lab
    source_id = source()
    job_id, token = leased_job(source_id)
    ingest(job_id, token)
    with Session(admin) as db, db.begin():
        opportunity_id = db.scalar(select(OpportunitySource.opportunity_id).join(Opportunity,
            Opportunity.id == OpportunitySource.opportunity_id).where(
            OpportunitySource.source_id == source_id, Opportunity.lifecycle == "open"))
        if revoked:
            source_row = db.get(Source, source_id)
            source_row.permission = {**source_row.permission, "revoked": True}
    # Keep this transition and any other legacy workspace repairs uncommitted;
    # a background service must not race this deliberately malformed fixture.
    with session_factory()() as db, db.begin():
        for old in db.scalars(select(WorkflowRun).where(WorkflowRun.opportunity_id == opportunity_id)):
            old.restore_hold = True
        legacy = WorkflowRun(workspace_id=WORKSPACE, opportunity_id=opportunity_id,
            thread_id="kajh-p1-test-" + uuid4().hex, graph_version=GRAPH_VERSION,
            policy_version=POLICY_VERSION, status="waiting_review", budget={"paid_calls": 0, "limit": 0})
        history = WorkflowRun(workspace_id=WORKSPACE, opportunity_id=opportunity_id,
            thread_id="kajh-p1-test-" + uuid4().hex, graph_version=GRAPH_VERSION,
            policy_version=POLICY_VERSION, status="completed", budget={"paid_calls": 0, "limit": 0})
        db.add_all([legacy, history])
        db.flush()
        reconcile_workflow_bindings(db)
        db.flush()
        assert legacy.restore_hold is True
        assert legacy.status == "content_binding_required"
        assert history.status == "completed" and not history.restore_hold
        active = list(db.scalars(select(WorkflowRun).where(WorkflowRun.opportunity_id == opportunity_id,
            WorkflowRun.restore_hold.is_(False), WorkflowRun.status == "queued")))
        assert len(active) == (0 if revoked else 1)
        if active:
            assert active[0].budget["opportunity_version"] == db.get(Opportunity, opportunity_id).version
        db.rollback()


def test_atomic_finish_cannot_overwrite_a_hold_added_after_initial_read(lab, monkeypatch):
    admin, source, leased_job, ingest = lab
    source_id = source()
    job_id, token = leased_job(source_id)
    ingest(job_id, token)
    with Session(admin) as db:
        opportunity_id = db.scalar(select(OpportunitySource.opportunity_id).join(Opportunity,
            Opportunity.id == OpportunitySource.opportunity_id).where(
            OpportunitySource.source_id == source_id, Opportunity.lifecycle == "open"))
        run = db.scalar(select(WorkflowRun).where(WorkflowRun.opportunity_id == opportunity_id))
        run_id, version = run.id, db.get(Opportunity, opportunity_id).version
    actual_current = current

    def concurrent_hold(identifier):
        snapshot = actual_current(identifier)
        with Session(admin) as db, db.begin():
            row = db.get(WorkflowRun, identifier)
            row.restore_hold, row.status = True, "content_changed"
        return snapshot

    monkeypatch.setattr("kajh_worker.workflow.current", concurrent_hold)
    with pytest.raises(PermissionError, match="held"):
        asyncio.run(finish({"run_id": str(run_id), "opportunity_version": version, "status": "classified"}))
    with Session(admin) as db:
        assert db.get(WorkflowRun, run_id).status == "content_changed"
        assert not db.scalars(select(AuditEvent).where(AuditEvent.object_id == str(run_id),
            AuditEvent.action == "workflow.completed")).all()


@pytest.mark.parametrize("forged_status", ["classified", "approved"])
def test_checkpoint_state_cannot_complete_review_required_content_without_consumed_decision(lab, forged_status):
    admin, source, leased_job, ingest = lab
    source_id = source()
    job_id, token = leased_job(source_id)
    ingest(job_id, token)
    with Session(admin) as db:
        opportunity = db.scalar(select(Opportunity).join(OpportunitySource,
            Opportunity.id == OpportunitySource.opportunity_id).where(
            OpportunitySource.source_id == source_id, Opportunity.eligibility == "Unknown"))
        run = db.scalar(select(WorkflowRun).where(WorkflowRun.opportunity_id == opportunity.id))
        run_id, version = run.id, opportunity.version
    with pytest.raises(PermissionError, match="trusted consumed review"):
        asyncio.run(finish({"run_id": str(run_id), "opportunity_version": version, "status": forged_status}))
    with Session(admin) as db:
        assert db.get(WorkflowRun, run_id).status == "test_pending"
        assert not db.scalars(select(AuditEvent).where(AuditEvent.object_id == str(run_id),
            AuditEvent.action == "workflow.completed")).all()


@pytest.mark.parametrize("invalid_version", [True, 1.0])
def test_node_validation_and_finish_reject_coercible_content_version_types(lab, invalid_version):
    admin, source, leased_job, ingest = lab
    source_id = source()
    job_id, token = leased_job(source_id)
    ingest(job_id, token)
    with Session(admin) as db:
        opportunity = db.scalar(select(Opportunity).join(OpportunitySource,
            Opportunity.id == OpportunitySource.opportunity_id).where(
            OpportunitySource.source_id == source_id, Opportunity.eligibility == "Eligible"))
        assert opportunity.version == 1
        run = db.scalar(select(WorkflowRun).where(WorkflowRun.opportunity_id == opportunity.id))
        run_id = run.id
    state = {"run_id": str(run_id), "graph_version": GRAPH_VERSION, "policy_version": POLICY_VERSION,
             "opportunity_version": invalid_version, "status": "classified", "paid_calls": 0}
    with pytest.raises(PermissionError, match="content version"):
        asyncio.run(validate(state))
    with pytest.raises(PermissionError, match="content version"):
        asyncio.run(finish(state))
    with Session(admin) as db:
        assert db.get(WorkflowRun, run_id).status == "test_pending"
