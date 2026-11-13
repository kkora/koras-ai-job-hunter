"""Real database regressions for source identity and strict permission boundaries."""
import os
from uuid import uuid4

import pytest
from sqlalchemy import select, text, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kajh_api.bootstrap import admin_engine
from kajh_api.db import bind_identity, engine as api_engine
from kajh_api.models import Job, Membership, Opportunity, OpportunitySource, RawObservation, Source, SourceRun
from kajh_domain.adapters import synthetic_permission

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def owned_runtime():
    if os.environ.get("KAJH_TEST_RUNTIME") != "1":
        pytest.skip("Owned local runtime not requested")


@pytest.fixture
def identity_lab():
    engine = admin_engine()
    db = Session(engine)
    transaction = db.begin()
    try:
        first = db.scalar(select(Source).where(Source.adapter_type == "synthetic"))
        assert first
        second = Source(workspace_id=first.workspace_id, name="Synthetic source identity probe " + uuid4().hex, adapter_type="synthetic", enabled=True, permission=synthetic_permission())
        db.add(second)
        db.flush()
        job = Job(workspace_id=first.workspace_id, source_id=first.id, kind="ingest", idempotency_key="identity-probe:" + uuid4().hex, request_hash="0" * 64)
        db.add(job)
        db.flush()
        run = SourceRun(workspace_id=first.workspace_id, source_id=first.id, job_id=job.id)
        db.add(run)
        db.flush()
        observation = RawObservation(workspace_id=first.workspace_id, source_id=first.id, source_run_id=run.id, external_id="identity-probe-" + uuid4().hex, payload_hash="0" * 64, payload={}, expires_at=func.now())
        db.add(observation)
        opportunity = Opportunity(workspace_id=first.workspace_id, title="Synthetic identity probe", organization="Synthetic only")
        db.add(opportunity)
        db.flush()
        yield db, first, second, job, run, observation, opportunity
    finally:
        transaction.rollback()
        db.close()
        engine.dispose()


@pytest.mark.parametrize("kind", ["run_job_source", "observation_run_source", "link_observation_source", "link_observation_external_id"])
def test_database_rejects_cross_source_attribution(identity_lab, kind):
    db, first, second, job, run, observation, opportunity = identity_lab
    expected = {"run_job_source": "source_runs_job_source_fk", "observation_run_source": "raw_observations_run_source_fk", "link_observation_source": "opportunity_sources_observation_identity_fk", "link_observation_external_id": "opportunity_sources_observation_identity_fk"}[kind]
    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            if kind == "run_job_source":
                row = SourceRun(workspace_id=first.workspace_id, source_id=second.id, job_id=job.id)
            elif kind == "observation_run_source":
                row = RawObservation(workspace_id=first.workspace_id, source_id=second.id, source_run_id=run.id, external_id="synthetic-wrong-source", payload_hash="f" * 64, payload={}, expires_at=func.now())
            else:
                row = OpportunitySource(workspace_id=first.workspace_id, opportunity_id=opportunity.id, source_id=second.id if kind == "link_observation_source" else first.id, observation_id=observation.id, external_id="synthetic-wrong-id" if kind == "link_observation_external_id" else observation.external_id, original_url="https://example.invalid/identity-probe")
            db.add(row)
            db.flush()
    assert caught.value.orig.sqlstate == "23503"
    assert caught.value.orig.diag.constraint_name == expected


def test_database_rejects_self_merge(identity_lab):
    db, _first, _second, _job, _run, _observation, opportunity = identity_lab
    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            opportunity.merged_into = opportunity.id
            db.flush()
    assert caught.value.orig.sqlstate == "23514"
    assert caught.value.orig.diag.constraint_name == "opportunities_no_self_merge"


def test_database_rejects_merge_across_workspaces(identity_lab):
    db, first, _second, _job, _run, _observation, opportunity = identity_lab
    other = db.scalar(text("SELECT id FROM app.workspaces WHERE id<>:workspace LIMIT 1"), {"workspace": first.workspace_id})
    assert other
    target = Opportunity(workspace_id=other, title="Synthetic isolated target", organization="Synthetic only")
    db.add(target)
    db.flush()
    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            opportunity.merged_into = target.id
            db.flush()
    assert caught.value.orig.sqlstate == "23503"
    assert caught.value.orig.diag.constraint_name == "opportunities_merged_workspace_fk"


@pytest.mark.parametrize("change", [
    {"revoked": "false"}, {"revoked": None}, {"approved": "true"},
    {"approved": False, "status": "approved"}, {"commercial_use": "true"},
    {"automated_collection": 1}, {"private_display": "true"},
    {"retention_days": "30"}, {"retention_days": 1.0}, {"quota_per_day": True},
    {"expires_at": None}, {"expires_at": "2027-01-01T00:00:00"}, {"attribution": "   "},
])
def test_sql_visibility_denies_ambiguous_policy_types(change):
    admin = admin_engine()
    with Session(admin) as db:
        owner = db.scalar(select(Membership).where(Membership.role == "Owner", Membership.active.is_(True)))
        assert owner
        user_id, workspace_id = owner.user_id, owner.workspace_id
    admin.dispose()
    with Session(api_engine()) as db:
        transaction = db.begin()
        try:
            bind_identity(db, str(user_id), str(workspace_id))
            source = Source(workspace_id=workspace_id, name="Synthetic strict policy probe " + uuid4().hex, adapter_type="synthetic", enabled=True, permission=synthetic_permission())
            db.add(source)
            db.flush()
            assert db.scalar(select(func.app.source_visible(source.id))) is True
            source.permission = {**source.permission, **change}
            db.flush()
            assert db.scalar(select(func.app.source_visible(source.id))) is False
        finally:
            transaction.rollback()
