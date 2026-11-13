"""Actual session, CSRF, role, and workspace negative tests."""
import os
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from kajh_api.config import settings
from kajh_api.main import app

pytestmark = pytest.mark.integration
ORIGIN = "http://127.0.0.1:3210"


@pytest.fixture(autouse=True)
def owned_runtime():
    if os.environ.get("KAJH_TEST_RUNTIME") != "1":
        pytest.skip("Owned local runtime not requested")


def login(role="owner"):
    client = TestClient(app, base_url=ORIGIN)
    response = client.post("/api/v1/auth/login", json={"email": f"{role}@kajh.invalid", "password": os.environ[f"{role.upper()}_PASSWORD"]}, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]})
    return client


def test_unauthenticated_private_endpoints_deny():
    with TestClient(app, base_url=ORIGIN) as client:
        for route in ("/api/v1/auth/me", "/api/v1/dashboard", "/api/v1/opportunities", "/api/v1/vendor-profile", "/api/v1/sources", "/api/v1/workflow-runs", "/api/v1/audit-events"):
            response = client.get(route)
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_login_requires_local_origin_and_invalid_credentials_redacted():
    with TestClient(app, base_url=ORIGIN) as client:
        secret = "kajh-synthetic-incorrect-password"
        payload = {"email": "owner@kajh.invalid", "password": secret}
        assert client.post("/api/v1/auth/login", json=payload).status_code == 403
        assert client.post("/api/v1/auth/login", json=payload, headers={"Origin": "https://attacker.invalid"}).status_code == 403
        response = client.post("/api/v1/auth/login", json=payload, headers={"Origin": ORIGIN})
        assert response.status_code == 401
        assert secret not in response.text


def test_cookies_and_session_claim_tampering():
    client = login()
    token = client.cookies.get("kajh_session")
    claims = jwt.decode(token, options={"verify_signature": False})
    for field, value in (("iss", "https://attacker.invalid"), ("aud", "wrong"), ("exp", 1), ("session_id", str(uuid4())), ("sub", str(uuid4()))):
        variant = {**claims, field: value}
        signed = jwt.encode(variant, settings().auth_jwt_secret, algorithm="HS256")
        with TestClient(app, base_url=ORIGIN) as attacker:
            attacker.cookies.set("kajh_session", signed)
            response = attacker.get("/api/v1/auth/me")
            assert response.status_code == 401, (field, response.text)
    with TestClient(app, base_url=ORIGIN) as attacker:
        attacker.cookies.set("kajh_session", token[:-5] + "wrong")
        assert attacker.get("/api/v1/auth/me").status_code == 401
    client.close()


def test_metadata_and_workspace_header_do_not_grant_authority():
    client = login("reviewer")
    claims = jwt.decode(client.cookies.get("kajh_session"), options={"verify_signature": False})
    claims.update(user_metadata={"role": "Owner"}, app_metadata={"role": "Owner"}, workspace_id="00000000-0000-0000-0000-000000000002")
    forged = jwt.encode(claims, settings().auth_jwt_secret, algorithm="HS256")
    with TestClient(app, base_url=ORIGIN) as probe:
        probe.cookies.set("kajh_session", forged)
        assert probe.get("/api/v1/auth/me").json()["user"]["role"] == "Reviewer"
        response = probe.get("/api/v1/dashboard", headers={"X-Workspace-ID": "00000000-0000-0000-0000-000000000002"})
        assert response.status_code == 403
    client.close()


def test_csrf_and_owner_permissions():
    client = login()
    source = client.get("/api/v1/sources").json()["items"][0]
    body = {"version": source["permission_version"], "killed": True}
    headers = {"Idempotency-Key": str(uuid4())}
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.patch(f"/api/v1/sources/{source['id']}", json=body, headers=headers).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert client.patch(f"/api/v1/sources/{source['id']}", json=body, headers={**headers, "Origin": "https://attacker.invalid"}).status_code == 403
    reviewer = login("reviewer")
    assert reviewer.patch(f"/api/v1/sources/{source['id']}", json=body, headers=headers).status_code == 403
    assert reviewer.put("/api/v1/vendor-profile", json={"version": 1, "profile": {}}, headers=headers).status_code == 403
    reviewer.close()
    client.close()


def test_auditor_is_read_only_and_can_logout():
    client = login("auditor")
    assert client.get("/api/v1/dashboard").status_code == 200
    assert client.post("/api/v1/search-profiles", json={"name": "Forbidden", "filters": {}}, headers={"Idempotency-Key": str(uuid4())}).status_code == 403
    assert client.post("/api/v1/auth/logout", json={}).status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    client.close()


def test_logout_revokes_copied_session_cookie():
    client = login()
    token = client.cookies.get("kajh_session")
    assert client.post("/api/v1/auth/logout", json={}).status_code == 200
    with TestClient(app, base_url=ORIGIN) as copied:
        copied.cookies.set("kajh_session", token)
        assert copied.get("/api/v1/dashboard").status_code == 401
    client.close()


def test_unknown_inputs_and_secrets_rejected_without_echo():
    client = login()
    response = client.post("/api/v1/search-profiles", json={"name": "Synthetic invalid", "filters": {"api_key": "must-not-be-stored"}}, headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 422
    assert "must-not-be-stored" not in response.text
    response = client.post("/api/v1/auth/login", json={"email": "owner@kajh.invalid", "password": "synthetic", "unexpected": True})
    assert response.status_code == 422
    assert "synthetic" not in response.text
    client.close()


def test_request_logger_emits_json_without_credentials_or_queries():
    import io
    import json
    import logging
    from uuid import UUID
    from kajh_api.main import log
    assert log.level == logging.INFO
    assert not log.propagate
    handler = next(handler for handler in log.handlers if handler.get_name() == "kajh.api.json")
    output = io.StringIO()
    previous = handler.setStream(output)
    try:
        with TestClient(app, base_url=ORIGIN) as client:
            secret = "synthetic-query-secret-must-not-log"
            response = client.get("/health?access_token=" + secret, headers={"Authorization": "Bearer " + secret, "X-Request-ID": secret})
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        records = [item for item in lines if item.get("event") == "http_request"]
        assert len(records) == 1
        assert records[0]["path"] == "/health"
        assert records[0]["status"] == 200
        assert str(UUID(records[0]["request_id"])) == response.headers["X-Request-ID"]
        assert secret not in output.getvalue()
    finally:
        handler.setStream(previous)
