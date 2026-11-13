"""Regressions for concurrent writes, rights revocation, and content-bound review."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from threading import Event, Lock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from kajh_api import main as api
from kajh_api.models import AuditEvent, EligibilityEvidence, Job, Opportunity, RawObservation, SearchProfile, WorkflowDecision, WorkflowRun, utcnow
from kajh_domain import POLICY_VERSION
from test_api import backend_client as _shared_backend_client
from test_api import cleanup_provenance_fixture, key, provenance_fixture

backend_client = _shared_backend_client

pytestmark = pytest.mark.integration


def race_two_requests(first, second, first_ready, second_finished, release):
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(first)
        try:
            assert first_ready.wait(5), "First request did not reach the controlled race"
            two = pool.submit(second)
            assert not second_finished.wait(0.25), "Concurrent duplicate escaped serialization"
        finally:
            release.set()
        return one.result(timeout=10), two.result(timeout=10)


def test_concurrent_same_key_replays_one_profile_update(backend_client, monkeypatch):
    created = backend_client.post("/api/v1/search-profiles", json={"name": "Synthetic concurrent replay " + uuid4().hex[:8], "filters": {}}, headers=key()).json()
    scope = "search-profile:" + created["id"]
    first_ready, second_finished, release, lock = Event(), Event(), Event(), Lock()
    original = api.idem_start
    calls = 0

    def observed(db, person, operation, operation_key, body):
        nonlocal calls
        if operation != scope:
            return original(db, person, operation, operation_key, body)
        result = original(db, person, operation, operation_key, body)
        with lock:
            calls += 1
            number = calls
        if number == 1:
            first_ready.set()
            assert release.wait(5)
        else:
            second_finished.set()
        return result

    monkeypatch.setattr(api, "idem_start", observed)
    headers = key()
    body = {"name": created["name"] + " updated", "filters": {"skills": ["Azure"]}, "version": 1}
    def operation():
        return backend_client.put("/api/v1/search-profiles/" + created["id"], json=body, headers=headers)
    one, two = race_two_requests(operation, operation, first_ready, second_finished, release)
    assert one.status_code == two.status_code == 200, (one.text, two.text)
    assert one.json() == two.json()
    engine = create_engine(os.environ["DATABASE_ADMIN_URL"], hide_parameters=True)
    with Session(engine) as db:
        assert db.get(SearchProfile, created["id"]).version == 2
        events = db.scalars(select(AuditEvent).where(AuditEvent.object_id == created["id"], AuditEvent.action == "search_profile.version_created")).all()
        assert len(events) == 1
    engine.dispose()


def test_concurrent_saved_puts_are_idempotent(backend_client, monkeypatch):
    engine, opportunity_id, sources, runs, observations, _links = provenance_fixture()
    first_ready, second_finished, release, lock = Event(), Event(), Event(), Lock()
    original = api.record
    calls = 0

    def observed(db, person, request, action, obj, oid=None, details=None):
        nonlocal calls
        if action == "opportunity.saved" and oid == opportunity_id:
            with lock:
                calls += 1
                number = calls
            if number == 1:
                first_ready.set()
                assert release.wait(5)
            else:
                second_finished.set()
        return original(db, person, request, action, obj, oid, details)

    monkeypatch.setattr(api, "record", observed)
    try:
        def operation():
            return backend_client.put(f"/api/v1/opportunities/{opportunity_id}/saved", json={"saved": True})
        one, two = race_two_requests(operation, operation, first_ready, second_finished, release)
        assert one.status_code == two.status_code == 200, (one.text, two.text)
        assert one.json() == two.json()
        with Session(engine) as db:
            events = db.scalars(select(AuditEvent).where(AuditEvent.object_id == str(opportunity_id), AuditEvent.action == "opportunity.saved")).all()
            assert len(events) == 1
    finally:
        cleanup_provenance_fixture(engine, [opportunity_id], sources, runs, observations)


@pytest.mark.parametrize("change", [{"revoked": True}, {"persistent_storage": False}])
def test_owner_can_revoke_enabled_source_without_separate_disable(backend_client, change):
    engine, opportunity_id, sources, runs, observations, _links = provenance_fixture()
    try:
        before = next(row for row in backend_client.get("/api/v1/sources").json()["items"] if row["id"] == str(sources[0]))
        result = backend_client.patch(f"/api/v1/sources/{sources[0]}", json={"version": before["permission_version"], "permission": {**before["permission"], **change}}, headers=key())
        assert result.status_code == 200, result.text
        assert result.json()["enabled"] is False
        assert result.json()["permission_version"] == before["permission_version"] + 1
        assert backend_client.get(f"/api/v1/opportunities/{opportunity_id}").status_code == 404
        assert backend_client.post(f"/api/v1/sources/{sources[0]}/ingest", json={}, headers=key()).status_code == 409
    finally:
        cleanup_provenance_fixture(engine, [opportunity_id], sources, runs, observations)


def test_saved_summary_excludes_unavailable_source_content(backend_client):
    from kajh_api.models import Source
    engine, opportunity_id, sources, runs, observations, _links = provenance_fixture()
    try:
        baseline = backend_client.get("/api/v1/dashboard").json()["counts"]["saved"]
        assert backend_client.put(f"/api/v1/opportunities/{opportunity_id}/saved", json={"saved": True}).status_code == 200
        assert backend_client.get("/api/v1/dashboard").json()["counts"]["saved"] == baseline + 1
        with Session(engine) as db, db.begin():
            source = db.get(Source, sources[0])
            source.permission = {**source.permission, "revoked": True}
        assert backend_client.get("/api/v1/dashboard").json()["counts"]["saved"] == baseline
        assert backend_client.put(f"/api/v1/opportunities/{opportunity_id}/saved", json={"saved": True}).status_code == 404
        assert backend_client.put(f"/api/v1/opportunities/{opportunity_id}/saved", json={"saved": False}).status_code == 200
    finally:
        cleanup_provenance_fixture(engine, [opportunity_id], sources, runs, observations)


def test_split_rebuilds_original_rules_expiry_and_source_history(backend_client):
    engine, opportunity_id, sources, runs, observations, links = provenance_fixture()
    opportunities = [opportunity_id]
    try:
        with Session(engine) as db, db.begin():
            first, second = db.get(RawObservation, observations[0]), db.get(RawObservation, observations[1])
            first.payload = {**first.payload, "title": "Synthetic retained distinct engagement", "facts": {**first.payload["facts"], "b2b_accepted": False, "c2c_accepted": False}}
            first.expires_at, second.expires_at = utcnow() + timedelta(hours=1), utcnow() + timedelta(days=10)
            expiry = first.expires_at.isoformat()
            db.get(Opportunity, opportunity_id).expires_at = second.expires_at
            for observed, marker in ((first, "synthetic-retained-history"), (second, "synthetic-detached-history")):
                db.add(EligibilityEvidence(workspace_id=observed.workspace_id, opportunity_id=opportunity_id, rule=marker, outcome="Unknown", details={"observation_id": str(observed.id), "evidence": marker}))
        before = backend_client.get(f"/api/v1/opportunities/{opportunity_id}").json()
        response = backend_client.post(f"/api/v1/opportunities/{opportunity_id}/split-source", json={"version": before["opportunity"]["version"], "source_link_id": str(links[1]), "reason": "Synthetic correction of source reconciliation"}, headers=key())
        assert response.status_code == 201, response.text
        opportunities.append(response.json()["id"])
        detail = backend_client.get(f"/api/v1/opportunities/{opportunity_id}")
        body = detail.json()
        assert body["opportunity"]["eligibility"] == "Ineligible"
        assert body["opportunity"]["expires_at"] == expiry
        assert body["opportunity"]["title"] == "Synthetic retained distinct engagement"
        assert "synthetic-detached-history" not in detail.text
        assert "synthetic-retained-history" in detail.text
        assert any(row["details"].get("content_version") == body["opportunity"]["version"] for row in body["evidence"])
        with Session(engine) as db:
            for field, target in (("workflow_run_id", response.json()["id"]), ("original_workflow_run_id", opportunity_id)):
                run = db.get(WorkflowRun, response.json()[field])
                assert run.opportunity_id == target or str(run.opportunity_id) == str(target)
                assert run.budget["opportunity_version"] == db.get(Opportunity, target).version
    finally:
        cleanup_provenance_fixture(engine, opportunities, sources, runs, observations)


@pytest.mark.parametrize("facts", [{"_canonical_hash": "synthetic-forged-hash"}, {"synthetic": False}, {"synthetic": 1}, {"_source_evidence": [{"confirmed": 1}]}])
def test_review_cannot_change_reserved_provenance(backend_client, facts):
    engine, opportunity_id, sources, runs, observations, _links = provenance_fixture()
    try:
        with Session(engine) as db, db.begin():
            row = db.get(Opportunity, opportunity_id)
            row.facts = {**row.facts, "synthetic": True, "_source_evidence": [{"confirmed": True}]}
        response = backend_client.post(f"/api/v1/opportunities/{opportunity_id}/review", json={"version": 1, "facts": facts, "reason": "Synthetic reserved provenance negative test"}, headers=key())
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "RESERVED_FACT"
        with Session(engine) as db:
            assert db.get(Opportunity, opportunity_id).version == 1
    finally:
        cleanup_provenance_fixture(engine, [opportunity_id], sources, runs, observations)


def test_review_holds_old_assessment_and_resume_rejects_stale_binding(backend_client):
    engine, opportunity_id, sources, runs, observations, _links = provenance_fixture()
    try:
        with Session(engine) as db, db.begin():
            opportunity = db.get(Opportunity, opportunity_id)
            old = WorkflowRun(workspace_id=opportunity.workspace_id, opportunity_id=opportunity_id, thread_id="kajh-p1-stale-api-" + uuid4().hex, graph_version="p1-v1", policy_version=POLICY_VERSION, status="waiting_review", budget={"paid_calls": 0, "limit": 0, "opportunity_version": 0})
            db.add(old)
            db.flush()
            old_id = old.id
        stale = backend_client.post(f"/api/v1/workflow-runs/{old_id}/resume", json={"version": 1, "decision": "approved", "reason": "Synthetic stale assessment authorization negative"}, headers=key())
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "CONTENT_CHANGED"
        result = backend_client.post(f"/api/v1/opportunities/{opportunity_id}/review", json={"version": 1, "facts": {"b2b_accepted": False}, "reason": "Synthetic owner correction with current evidence"}, headers=key())
        assert result.status_code == 200, result.text
        with Session(engine) as db:
            previous, current = db.get(WorkflowRun, old_id), db.get(WorkflowRun, result.json()["workflow_run_id"])
            assert previous.restore_hold is True
            assert previous.status == "content_changed"
            assert current.budget["opportunity_version"] == result.json()["version"]
            assert db.scalar(select(WorkflowDecision.id).where(WorkflowDecision.run_id == old_id)) is None
            assert db.scalar(select(Job.id).where(Job.payload["run_id"].astext == str(old_id))) is None
    finally:
        cleanup_provenance_fixture(engine, [opportunity_id], sources, runs, observations)


@pytest.mark.parametrize("field,value", [("legal_entity", 7), ("rate_preferences", "invalid"), ("data", {"synthetic": False})])
def test_vendor_profile_rejects_malformed_or_wrapped_configuration(backend_client, field, value):
    previous = backend_client.get("/api/v1/vendor-profile").json()
    response = backend_client.put("/api/v1/vendor-profile", json={"version": previous["version"], "profile": {**previous["profile"], field: value}}, headers=key())
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "INVALID_PROFILE"
    assert backend_client.get("/api/v1/vendor-profile").json()["version"] == previous["version"]


def test_non_finite_json_configuration_is_rejected(backend_client):
    response = backend_client.post("/api/v1/search-profiles", content=b'{"name":"Synthetic nonfinite probe","filters":{"minimum_rate":NaN}}', headers={**key(), "Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "INVALID_CONFIGURATION"


def test_unmerge_requires_version_reason_and_replays_atomically(backend_client):
    engine, opportunity_id, sources, runs, observations, _links = provenance_fixture()
    opportunities = [opportunity_id]
    try:
        with Session(engine) as db, db.begin():
            original = db.get(Opportunity, opportunity_id)
            target = Opportunity(workspace_id=original.workspace_id, title="Synthetic reconciliation target", organization="Synthetic only")
            db.add(target)
            db.flush()
            target_id = target.id
            opportunities.append(target_id)
            original.merged_into = target_id
        assert backend_client.post(f"/api/v1/opportunities/{opportunity_id}/unmerge", json={}).status_code == 422
        headers = key()
        body = {"version": 1, "reason": "Synthetic correction of recorded reconciliation"}
        first = backend_client.post(f"/api/v1/opportunities/{opportunity_id}/unmerge", json=body, headers=headers)
        assert first.status_code == 200, first.text
        second = backend_client.post(f"/api/v1/opportunities/{opportunity_id}/unmerge", json=body, headers=headers)
        assert second.status_code == 200
        assert first.json() == second.json()
        assert first.json()["version"] == 2
        stale = backend_client.post(f"/api/v1/opportunities/{opportunity_id}/unmerge", json=body, headers=key())
        assert stale.status_code == 412
    finally:
        cleanup_provenance_fixture(engine, opportunities, sources, runs, observations)


@pytest.mark.parametrize("key_length", [32, 160])
def test_ingestion_key_is_source_scoped_and_bounded(backend_client, monkeypatch, key_length):
    from uuid import UUID
    from kajh_api.db import bind_identity
    from kajh_api.models import Source
    from kajh_domain.adapters import synthetic_permission
    identity = backend_client.get("/api/v1/auth/me").json()
    db = Session(api.engine())
    transaction = db.begin()
    try:
        bind_identity(db, identity["user"]["id"], identity["workspace"]["id"])
        sources = [Source(workspace_id=UUID(identity["workspace"]["id"]), name="Synthetic scoped job probe " + uuid4().hex, adapter_type="synthetic", enabled=True, permission=synthetic_permission()) for _ in range(2)]
        db.add_all(sources)
        db.flush()
        # Both requests use one rollback-only restricted API transaction; queued
        # probes never become visible to the independent live worker.
        monkeypatch.setitem(api.app.dependency_overrides, api.database, lambda: db)
        headers = {"Idempotency-Key": (uuid4().hex * 5)[:key_length]}
        results = [backend_client.post(f"/api/v1/sources/{source.id}/ingest", json={}, headers=headers) for source in sources]
        assert all(result.status_code == 202 for result in results), [result.text for result in results]
        ids = [result.json()["job_id"] for result in results]
        assert ids[0] != ids[1]
        again = backend_client.post(f"/api/v1/sources/{sources[0].id}/ingest", json={}, headers=headers)
        assert again.status_code == 202
        assert again.json()["job_id"] == ids[0]
        rows = db.scalars(select(Job).where(Job.id.in_(ids))).all()
        assert len(rows) == 2
        assert all(len(row.idempotency_key) <= 200 for row in rows)
    finally:
        transaction.rollback()
        db.close()
