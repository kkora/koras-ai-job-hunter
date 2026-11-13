"""Real PostgreSQL checkpoints; all approvals concern local synthetic assessment only."""
from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session

from kajh_api.models import AuditEvent, Membership, Opportunity, WorkflowDecision, WorkflowRun, utcnow
from kajh_domain import POLICY_VERSION
from kajh_worker.config import session_factory
from kajh_worker.ingestion import GRAPH_VERSION
from kajh_worker.workflow import run_workflow, setup_checkpoints

pytestmark = pytest.mark.integration


@pytest.fixture
def flow():
    if os.environ.get("KAJH_TEST_RUNTIME") != "1":
        pytest.skip("Owned local runtime was not requested")
    engine = create_engine(os.environ["DATABASE_ADMIN_URL"], hide_parameters=True)
    ids, threads = [], []
    asyncio.run(setup_checkpoints())

    def create(*, budget=None, eligibility="Unknown"):
        with Session(engine) as db, db.begin():
            opportunity = db.scalar(select(Opportunity).where(Opportunity.eligibility == eligibility,
                Opportunity.lifecycle == "open", Opportunity.merged_into.is_(None)).limit(1))
            assert opportunity is not None, "Seeded synthetic opportunity required"
            owner = db.scalar(select(Membership).where(Membership.workspace_id == opportunity.workspace_id,
                Membership.role == "Owner", Membership.active.is_(True)))
            run = WorkflowRun(workspace_id=opportunity.workspace_id, opportunity_id=opportunity.id,
                thread_id="kajh-p1-test-" + uuid4().hex, graph_version=GRAPH_VERSION,
                policy_version=POLICY_VERSION, status="test_pending", requested_by=owner.user_id,
                budget={"paid_calls": 0, "limit": 0, **(budget or {})})
            db.add(run)
            db.flush()
            ids.append(run.id)
            threads.append(run.thread_id)
            return run.id

    def approve(run_id, *, expired=False, bad_hash=False):
        with Session(engine) as db, db.begin():
            run = db.get(WorkflowRun, run_id)
            opportunity = db.get(Opportunity, run.opportunity_id)
            digest = hashlib.sha256(f"{run.thread_id}:{opportunity.version}:review".encode()).hexdigest()
            decision = WorkflowDecision(workspace_id=run.workspace_id, run_id=run.id,
                user_id=run.requested_by, decision="approved", payload_hash="0" * 64 if bad_hash else digest,
                content_version=opportunity.version,
                expires_at=utcnow() + timedelta(minutes=-1 if expired else 10))
            db.add(decision)
            db.flush()
            return decision.id

    yield engine, create, approve

    with Session(engine) as db, db.begin():
        db.execute(delete(WorkflowDecision).where(WorkflowDecision.run_id.in_(ids)))
        db.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(ids)))
        db.execute(delete(AuditEvent).where(AuditEvent.object_id.in_([str(value) for value in ids])))
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            db.execute(text(f"DELETE FROM checkpoints.{table} WHERE thread_id = ANY(:threads)"), {"threads": threads})
    engine.dispose()


def test_actual_interrupt_then_new_process_authorized_resume_is_idempotent(flow):
    engine, create, approve = flow
    run_id = create()
    assert asyncio.run(run_workflow(run_id)) == "waiting_review"
    with Session(engine) as db:
        row = db.get(WorkflowRun, run_id)
        assert row.status == "waiting_review"
        checkpoint_count = db.scalar(text("SELECT count(*) FROM checkpoints.checkpoints WHERE thread_id=:thread"), {"thread": row.thread_id})
        assert checkpoint_count > 0
    decision_id = approve(run_id)
    code = (
        "import asyncio; from kajh_worker.workflow import run_workflow; "
        f"print(asyncio.run(run_workflow('{run_id}', '{decision_id}')))"
    )
    completed = subprocess.run([sys.executable, "-B", "-c", code], env=os.environ.copy(),
        capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "completed"
    assert asyncio.run(run_workflow(run_id, decision_id)) == "completed"
    with Session(engine) as db:
        assert db.get(WorkflowDecision, decision_id).consumed is True
        events = db.scalars(select(AuditEvent).where(AuditEvent.object_id == str(run_id),
            AuditEvent.action == "workflow.local_review_consumed")).all()
        assert len(events) == 1
        assert all(event.details["external_dispatch"] is False for event in events)


@pytest.mark.parametrize("kind", ["missing", "expired", "changed_hash"])
def test_unauthorized_resume_cannot_consume_or_complete(flow, kind):
    engine, create, approve = flow
    run_id = create()
    assert asyncio.run(run_workflow(run_id)) == "waiting_review"
    decision = uuid4() if kind == "missing" else approve(run_id, expired=kind == "expired", bad_hash=kind == "changed_hash")
    assert asyncio.run(run_workflow(run_id, decision)) == "error"
    with Session(engine) as db:
        assert db.get(WorkflowRun, run_id).status == "error"
        row = db.get(WorkflowDecision, decision)
        assert row is None or row.consumed is False
        count = db.scalar(select(text("count(*)")).select_from(AuditEvent).where(
            AuditEvent.object_id == str(run_id), AuditEvent.action == "workflow.local_review_consumed"))
        assert count == 0


def test_budget_stops_before_mock_node_with_zero_paid_calls(flow):
    engine, create, _ = flow
    run_id = create(budget={"mock_step_limit": 0})
    assert asyncio.run(run_workflow(run_id)) == "budget_exhausted"
    with Session(engine) as db:
        row = db.get(WorkflowRun, run_id)
        assert row.budget.get("mock_attempts", 0) == 0
        assert row.budget["paid_calls"] == 0
        assert row.status == "budget_exhausted"


@pytest.mark.parametrize("mode,expected_attempts", [("transient", 2), ("timeout", 2), ("error", 1)])
def test_bounded_mock_retry_timeout_and_error_states(flow, mode, expected_attempts):
    engine, create, _ = flow
    run_id = create(budget={"mock_mode": mode})
    status = asyncio.run(run_workflow(run_id))
    assert status == ("waiting_review" if mode == "transient" else "error")
    with Session(engine) as db:
        row = db.get(WorkflowRun, run_id)
        assert row.budget["mock_attempts"] == expected_attempts
        assert row.budget["paid_calls"] == 0
        if mode == "timeout":
            assert row.error == "NodeTimeoutError"


@pytest.mark.parametrize("field,value", [("graph_version", "incompatible-v2"), ("policy_version", "unknown-policy"), ("restore_hold", True)])
def test_current_version_and_restore_hold_checked_before_checkpoint_load(flow, field, value):
    engine, create, approve = flow
    run_id = create()
    assert asyncio.run(run_workflow(run_id)) == "waiting_review"
    decision = approve(run_id)
    with Session(engine) as db, db.begin():
        setattr(db.get(WorkflowRun, run_id), field, value)
    with pytest.raises((ValueError, PermissionError)):
        asyncio.run(run_workflow(run_id, decision))
    with Session(engine) as db:
        assert db.get(WorkflowDecision, decision).consumed is False


def test_crash_after_local_commit_recovers_without_reconsuming_approval(flow):
    engine, create, approve = flow
    run_id = create(budget={"mock_mode": "crash_after_consume"})
    assert asyncio.run(run_workflow(run_id)) == "waiting_review"
    decision_id = approve(run_id)
    environment = {**os.environ, "KAJH_WORKER_FAULT_INJECTION": "true"}
    code = (
        "import asyncio; from kajh_worker.workflow import run_workflow; "
        f"asyncio.run(run_workflow('{run_id}', '{decision_id}'))"
    )
    crashed = subprocess.run([sys.executable, "-B", "-c", code], env=environment,
        capture_output=True, text=True, timeout=30, check=False)
    assert crashed.returncode == 86, crashed.stderr
    with Session(engine) as db:
        assert db.get(WorkflowDecision, decision_id).consumed is True
    # A live service may win recovery of the committed marker. Its advisory
    # lock is a valid concurrent outcome; wait for the same durable completion.
    deadline = time.monotonic() + 3
    status = asyncio.run(run_workflow(run_id))
    while status == "busy" and time.monotonic() < deadline:
        time.sleep(0.05)
        status = asyncio.run(run_workflow(run_id))
    assert status == "completed"
    with Session(engine) as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.object_id == str(run_id),
            AuditEvent.action == "workflow.local_review_consumed")).all()
        assert len(events) == 1
        assert db.get(WorkflowRun, run_id).status == "completed"


def test_worker_database_principal_cannot_forge_approval_or_api_identity(flow):
    _engine, create, _approve = flow
    run_id = create()
    with session_factory()() as db:
        role = db.scalar(text("SELECT current_user"))
        assert role == "kajh_worker"
    with session_factory()() as db, db.begin():
        with pytest.raises(Exception) as error:
            db.execute(text("INSERT INTO app.workflow_decisions(id,workspace_id,run_id,user_id,decision,payload_hash,content_version,expires_at,consumed,created_at) VALUES (gen_random_uuid(),:ws,:run,gen_random_uuid(),'approved','fake',1,now()+interval '1 hour',false,now())"),
                       {"ws": "00000000-0000-0000-0000-000000000001", "run": run_id})
        assert "permission denied" in str(error.value).lower()
        db.rollback()
