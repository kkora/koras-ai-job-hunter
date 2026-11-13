"""Bounded dependency outages on owned services; never changes host networking."""
from __future__ import annotations

import json
import time

import httpx

from bootstrap import load_env
from local_runtime import assert_owned, compose, docker
from validate_runtime import require_validation_assertions, write_evidence


def offline_probe() -> dict:
    require_validation_assertions()
    env=load_env()
    project=env["KAJH_PROJECT"]
    for service in ("api","worker","auth","db"):
        assert_owned("container",project+"-"+service)
    started=time.perf_counter()
    # Both application containers have only an internal Docker network. This
    # attempts a TCP handshake only, with no payload or provider/API call.
    egress={}
    probe="""import socket,json
try:
    s=socket.create_connection(('1.1.1.1',443),timeout=2)
except OSError as e:
    print(json.dumps({'blocked':True,'error_type':type(e).__name__}))
else:
    s.close()
    print(json.dumps({'blocked':False}))
    raise SystemExit(1)
"""
    for service in ("api","worker"):
        attempt=docker("exec",project+"-"+service,"python","-c",probe,capture_output=True,text=True)
        egress[service]=json.loads(attempt.stdout)
        assert egress[service]["blocked"]
    origin={"Origin":"http://127.0.0.1:3210"}
    credentials={"email":"owner@kajh.invalid","password":env["KAJH_OWNER_PASSWORD"]}
    with httpx.Client(base_url="http://127.0.0.1:8210",timeout=15,trust_env=False) as client:
        response=client.post("/api/v1/auth/login",headers=origin,json=credentials)
        assert response.status_code==200
        csrf=response.json()["csrf_token"]
        try:
            compose("stop","--timeout","15","auth")
            unavailable=client.post("/api/v1/auth/login",headers=origin,json=credentials)
            assert unavailable.status_code==503 and unavailable.json()["error"]["code"]=="AUTH_UNAVAILABLE"
            # A still-valid session is checked against its local PostgreSQL
            # session/membership record, so existing private reads remain usable.
            assert client.get("/api/v1/dashboard").status_code==200
            logout=client.post("/api/v1/auth/logout",headers={**origin,"X-CSRF-Token":csrf})
            assert logout.status_code==200
            assert client.get("/api/v1/auth/me").status_code==401
        finally:
            compose("up","-d","--no-build","--wait","--wait-timeout","120","auth")
        assert client.post("/api/v1/auth/login",headers=origin,json=credentials).status_code==200
        try:
            compose("stop","--timeout","20","db")
            failed=client.get("/api/v1/dashboard")
            assert failed.status_code==503 and failed.json()["error"]["code"]=="DATABASE_UNAVAILABLE"
            assert client.get("/ready").status_code==503
            assert client.get("/health").status_code==200
            assert env["KAJH_DB_PASSWORD"] not in failed.text
            assert env["KAJH_API_PASSWORD"] not in failed.text
        finally:
            compose("up","-d","--no-build","--wait","--wait-timeout","120","db","auth","api","worker")
        for _ in range(20):
            if client.get("/ready").status_code==200:
                break
            time.sleep(.5)
        else:
            raise AssertionError("Database pool did not recover")
        assert client.get("/api/v1/dashboard").status_code==200
        assert client.get("/health").json()["external_dispatch"] is False
        csrf=client.get("/api/v1/auth/me").json()["csrf_token"]
        assert client.post("/api/v1/auth/logout",headers={**origin,"X-CSRF-Token":csrf}).status_code==200
    return write_evidence("E09-offline.json",{
        "status":"PASS","egress":egress,"auth_outage":"503_AUTH_UNAVAILABLE",
        "existing_session_during_auth_outage":"LOCAL_DATABASE_VALIDATED",
        "logout_during_auth_outage":"LOCAL_REVOCATION_PASS",
        "database_outage":"503_DATABASE_UNAVAILABLE","liveness_during_db_outage":"PASS",
        "database_pool_recovery":"PASS","outbound_business_actions":"ABSENT",
        "host_network_settings_modified":False,"seconds":time.perf_counter()-started,
    })


if __name__=="__main__":
    print(json.dumps(offline_probe(),indent=2))
