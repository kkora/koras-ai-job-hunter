from kajh_api.main import app


def test_required_private_contracts_exist():
    schema = app.openapi()
    paths = schema["paths"]
    required = ["/api/v1/auth/login", "/api/v1/auth/me", "/api/v1/opportunities", "/api/v1/vendor-profile", "/api/v1/search-profiles", "/api/v1/sources", "/api/v1/workflow-runs", "/api/v1/audit-events"]
    assert all(path in paths for path in required)
    assert not any("dispatch" in path or "application" in path for path in paths)
    assert schema["components"]["schemas"]["SourceUpdate"]["additionalProperties"] is False
