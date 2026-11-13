"""Synthetic API integration checks against the owned PostgreSQL/Auth runtime."""
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from kajh_api.main import app

pytestmark = pytest.mark.integration
ORIGIN = "http://127.0.0.1:3210"


@pytest.fixture
def backend_client():
    if os.environ.get("KAJH_TEST_RUNTIME") != "1":
        pytest.skip("Owned local runtime not requested")
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post("/api/v1/auth/login", json={"email": "owner@kajh.invalid", "password": os.environ["OWNER_PASSWORD"]}, headers={"Origin": ORIGIN})
        assert response.status_code == 200, response.text
        client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]})
        yield client


def key():
    return {"Idempotency-Key": str(uuid4())}


def test_private_health_and_dashboard(backend_client):
    assert backend_client.get("/health").json()["external_dispatch"] is False
    assert backend_client.get("/ready").json()["pgvector"] == "0.8.7"
    me = backend_client.get("/api/v1/auth/me").json()
    assert me["user"]["role"] == "Owner"
    assert me["synthetic"] is True
    dashboard = backend_client.get("/api/v1/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["capabilities"]["external_dispatch"] is False
    assert dashboard.json()["capabilities"]["ai_ranking"] is False


def test_versioned_vendor_and_idempotent_update(backend_client):
    previous = backend_client.get("/api/v1/vendor-profile").json()
    profile = {**previous["profile"], "restrictions": ["Synthetic integration-test restrictions"]}
    body = {"version": previous["version"], "profile": profile}
    headers = key()
    result = backend_client.put("/api/v1/vendor-profile", json=body, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["version"] == previous["version"] + 1
    assert result.json()["synthetic"] is True
    repeated = backend_client.put("/api/v1/vendor-profile", json=body, headers=headers)
    assert repeated.status_code == 200
    assert repeated.json() == result.json()
    stale = backend_client.put("/api/v1/vendor-profile", json=body, headers=key())
    assert stale.status_code == 412
    changed_payload = backend_client.put("/api/v1/vendor-profile", json={**body, "profile": {**profile, "legal_name": "Another synthetic name"}}, headers=headers)
    assert changed_payload.status_code == 409


def test_search_profile_version_history(backend_client):
    body = {"name": "Synthetic test profile " + uuid4().hex[:8], "filters": {"skills": ["Power Pages"], "countries": ["US"], "b2b_required": True}}
    response = backend_client.post("/api/v1/search-profiles", json=body, headers=key())
    assert response.status_code == 201, response.text
    row = response.json()
    update = backend_client.put(f"/api/v1/search-profiles/{row['id']}", json={**body, "version": 1, "filters": {**body["filters"], "countries": ["US", "CA"]}}, headers=key())
    assert update.status_code == 200
    assert update.json()["version"] == 2
    stale = backend_client.put(f"/api/v1/search-profiles/{row['id']}", json={**body, "version": 1}, headers=key())
    assert stale.status_code == 412


def test_real_sources_stay_disabled(backend_client):
    sources = backend_client.get("/api/v1/sources").json()["items"]
    assert {row["adapter_type"] for row in sources} >= {"synthetic", "remote_ok", "lever", "greenhouse", "ashby"}
    for source in sources:
        if source["adapter_type"] == "synthetic":
            continue
        assert source["enabled"] is False
        response = backend_client.patch(f"/api/v1/sources/{source['id']}", json={"version": source["permission_version"], "enabled": True}, headers=key())
        assert response.status_code == 403
        response = backend_client.post(f"/api/v1/sources/{source['id']}/ingest", json={}, headers=key())
        assert response.status_code == 403


def test_ingestion_request_is_idempotent(backend_client):
    sources = backend_client.get("/api/v1/sources").json()["items"]
    source = next(row for row in sources if row["adapter_type"] == "synthetic")
    headers = key()
    first = backend_client.post(f"/api/v1/sources/{source['id']}/ingest", json={}, headers=headers)
    second = backend_client.post(f"/api/v1/sources/{source['id']}/ingest", json={}, headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]


def test_opportunity_provenance_filters_and_saved(backend_client):
    result = backend_client.get("/api/v1/opportunities?limit=100")
    assert result.status_code == 200
    assert result.json()["total"] > 0, "Synthetic worker must ingest before acceptance"
    row = result.json()["items"][0]
    detail = backend_client.get(f"/api/v1/opportunities/{row['id']}")
    assert detail.status_code == 200
    assert detail.json()["sources"]
    assert detail.json()["evidence"]
    assert backend_client.put(f"/api/v1/opportunities/{row['id']}/saved", json={"saved": True}).status_code == 200
    saved = backend_client.get("/api/v1/opportunities?saved=true").json()["items"]
    assert row["id"] in {value["id"] for value in saved}
    assert backend_client.put(f"/api/v1/opportunities/{row['id']}/saved", json={"saved": False}).status_code == 200
    invalid = backend_client.get("/api/v1/opportunities?status=DefinitelyEligible")
    assert invalid.status_code == 422
    eligible = backend_client.get("/api/v1/opportunities?status=Eligible").json()["items"]
    assert all(value["eligibility"] == "Eligible" for value in eligible)


def test_review_cannot_override_w2_hard_gate(backend_client):
    rows = backend_client.get("/api/v1/opportunities?status=Ineligible&limit=100").json()["items"]
    row = next(value for value in rows if value["facts"].get("w2_only") is True)
    result = backend_client.post(f"/api/v1/opportunities/{row['id']}/review", json={"version": row["version"], "facts": {"b2b_accepted": True}, "reason": "Synthetic contrary B2B evidence does not remove W2-only restriction"}, headers=key())
    assert result.status_code == 200, result.text
    assert result.json()["eligibility"] == "Ineligible"
    detail = backend_client.get(f"/api/v1/opportunities/{row['id']}").json()
    assert any(event["action"] == "opportunity.reviewed" for event in detail["history"])


def test_no_outbound_action_route_exists(backend_client):
    assert backend_client.post("/api/v1/outbound-actions/dispatch", json={}).status_code == 404
    assert backend_client.post("/api/v1/applications", json={}).status_code == 404


def provenance_fixture():
    """Create a self-contained owned probe without editing the demo source."""
    from datetime import timedelta
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from kajh_api.models import EligibilityEvidence, Opportunity, OpportunitySource, RawObservation, Source, SourcePermission, SourceRun, utcnow
    from kajh_domain import evaluate_eligibility, normalize_opportunity
    from kajh_domain.adapters import synthetic_opportunities, synthetic_permission, synthetic_vendor_profile
    from uuid import UUID
    engine = create_engine(os.environ["DATABASE_ADMIN_URL"], hide_parameters=True)
    workspace = UUID("00000000-0000-0000-0000-000000000001")
    raw = synthetic_opportunities()[0]
    raw["title"] = "Synthetic reversible provenance probe " + uuid4().hex[:8]
    record = normalize_opportunity(raw)
    assessment = evaluate_eligibility(record, synthetic_vendor_profile())
    source_ids, run_ids, observation_ids, link_ids = [], [], [], []
    with Session(engine) as db, db.begin():
        opportunity = Opportunity(workspace_id=workspace, title=record["title"], organization=record["employer"]["name"], category=record["category"], description=record["description"], skills=record["skills"], facts={**record["facts"], "category": record["category"]}, eligibility=assessment["status"], expires_at=utcnow() + timedelta(days=1))
        db.add(opportunity)
        db.flush()
        opportunity_id = opportunity.id
        for n in range(2):
            permission = synthetic_permission()
            source = Source(workspace_id=workspace, name="Synthetic provenance probe " + uuid4().hex, adapter_type="synthetic", enabled=True, permission=permission)
            db.add(source)
            db.flush()
            source_ids.append(source.id)
            db.add(SourcePermission(workspace_id=workspace, source_id=source.id, version=1, body=permission))
            run = SourceRun(workspace_id=workspace, source_id=source.id, status="completed")
            db.add(run)
            db.flush()
            run_ids.append(run.id)
            observation = RawObservation(workspace_id=workspace, source_id=source.id, source_run_id=run.id, external_id=f"synthetic-probe-{n}", payload_hash=str(n) * 64, payload=raw, expires_at=utcnow() + timedelta(days=1))
            db.add(observation)
            db.flush()
            observation_ids.append(observation.id)
            link = OpportunitySource(workspace_id=workspace, opportunity_id=opportunity.id, source_id=source.id, observation_id=observation.id, external_id=f"synthetic-probe-{n}", original_url=f"https://example.invalid/probe/{n}")
            db.add(link)
            db.flush()
            link_ids.append(link.id)
        for rule in assessment["rules"]:
            db.add(EligibilityEvidence(workspace_id=workspace, opportunity_id=opportunity.id, rule=rule["rule_id"], outcome=rule["status"], details=rule))
    return engine, opportunity_id, source_ids, run_ids, observation_ids, link_ids


def cleanup_provenance_fixture(engine, opportunity_ids, source_ids, run_ids, observation_ids):
    from sqlalchemy import delete, select
    from sqlalchemy.orm import Session
    from kajh_api.models import EligibilityEvidence, Opportunity, OpportunitySource, RawObservation, SavedOpportunity, Source, SourcePermission, SourceRun, WorkflowDecision, WorkflowRun
    with Session(engine) as db, db.begin():
        workflows = list(db.scalars(select(WorkflowRun.id).where(WorkflowRun.opportunity_id.in_(opportunity_ids))))
        if workflows:
            db.execute(delete(WorkflowDecision).where(WorkflowDecision.run_id.in_(workflows)))
            db.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(workflows)))
        db.execute(delete(SavedOpportunity).where(SavedOpportunity.opportunity_id.in_(opportunity_ids)))
        db.execute(delete(EligibilityEvidence).where(EligibilityEvidence.opportunity_id.in_(opportunity_ids)))
        db.execute(delete(OpportunitySource).where(OpportunitySource.opportunity_id.in_(opportunity_ids)))
        db.execute(delete(Opportunity).where(Opportunity.id.in_(opportunity_ids)))
        db.execute(delete(RawObservation).where(RawObservation.id.in_(observation_ids)))
        db.execute(delete(SourceRun).where(SourceRun.id.in_(run_ids)))
        db.execute(delete(SourcePermission).where(SourcePermission.source_id.in_(source_ids)))
        db.execute(delete(Source).where(Source.id.in_(source_ids)))
    engine.dispose()


def test_source_deduplication_can_be_reversed_without_losing_provenance(backend_client):
    engine, opportunity_id, sources, runs, observations, links = provenance_fixture()
    opportunities = [opportunity_id]
    try:
        before = backend_client.get(f"/api/v1/opportunities/{opportunity_id}").json()
        assert len(before["sources"]) == 2
        headers = key()
        body = {"version": before["opportunity"]["version"], "source_link_id": str(links[1]), "reason": "Synthetic owner correction of conservative deduplication"}
        response = backend_client.post(f"/api/v1/opportunities/{opportunity_id}/split-source", json=body, headers=headers)
        assert response.status_code == 201, response.text
        separated_id = response.json()["id"]
        opportunities.append(separated_id)
        repeated = backend_client.post(f"/api/v1/opportunities/{opportunity_id}/split-source", json=body, headers=headers)
        assert repeated.status_code == 201
        assert repeated.json() == response.json()
        original = backend_client.get(f"/api/v1/opportunities/{opportunity_id}").json()
        separated = backend_client.get(f"/api/v1/opportunities/{separated_id}").json()
        assert len(original["sources"]) == len(separated["sources"]) == 1
        assert original["sources"][0]["source_id"] != separated["sources"][0]["source_id"]
        assert separated["opportunity"]["facts"]["split_from"] == str(opportunity_id)
    finally:
        cleanup_provenance_fixture(engine, opportunities, sources, runs, observations)


def test_revoked_source_content_disappears_before_background_purge(backend_client):
    from sqlalchemy.orm import Session
    from kajh_api.models import Source
    engine, opportunity_id, sources, runs, observations, _links = provenance_fixture()
    try:
        before = backend_client.get(f"/api/v1/opportunities/{opportunity_id}")
        assert before.status_code == 200
        with Session(engine) as db, db.begin():
            # A permitted second link cannot authorize facts from a revoked link.
            row = db.get(Source, sources[0])
            row.permission = {**row.permission, "revoked": True}
        assert backend_client.get(f"/api/v1/opportunities/{opportunity_id}").status_code == 404
        review = backend_client.post(f"/api/v1/opportunities/{opportunity_id}/review", json={"version": before.json()["opportunity"]["version"], "facts": {"b2b_accepted": True}, "reason": "Synthetic review cannot revive unavailable source evidence"}, headers=key())
        assert review.status_code == 404
        page = backend_client.get("/api/v1/opportunities?limit=100").json()
        assert str(opportunity_id) not in {item["id"] for item in page["items"]}
    finally:
        cleanup_provenance_fixture(engine, [opportunity_id], sources, runs, observations)


def test_expired_historical_evidence_is_hidden_after_link_refresh(backend_client):
    from datetime import timedelta
    from sqlalchemy.orm import Session
    from kajh_api.models import EligibilityEvidence, Opportunity, RawObservation, utcnow
    engine, opportunity_id, sources, runs, observations, _links = provenance_fixture()
    try:
        with Session(engine) as db, db.begin():
            opportunity = db.get(Opportunity, opportunity_id)
            expired = RawObservation(workspace_id=opportunity.workspace_id, source_id=sources[0], source_run_id=runs[0], external_id="synthetic-expired-history", payload_hash="f" * 64, payload={"synthetic": True}, expires_at=utcnow() - timedelta(seconds=1))
            db.add(expired)
            db.flush()
            observations.append(expired.id)
            db.add(EligibilityEvidence(workspace_id=opportunity.workspace_id, opportunity_id=opportunity_id, rule="historical-expired-probe", outcome="Unknown", details={"observation_id": str(expired.id), "evidence": "synthetic-expired-excerpt-must-not-display"}))
            db.add(EligibilityEvidence(workspace_id=opportunity.workspace_id, opportunity_id=opportunity_id, rule="current-retained-probe", outcome="Eligible", details={"observation_id": str(observations[0]), "evidence": "synthetic-retained-excerpt"}))
        response = backend_client.get(f"/api/v1/opportunities/{opportunity_id}")
        assert response.status_code == 200, response.text
        assert "synthetic-expired-excerpt-must-not-display" not in response.text
        assert "synthetic-retained-excerpt" in response.text
    finally:
        cleanup_provenance_fixture(engine, [opportunity_id], sources, runs, observations)
