"""Verify migration integrity against the owned database without changing history."""
from contextlib import nullcontext
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from kajh_api import bootstrap

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def owned_runtime():
    if os.environ.get("KAJH_TEST_RUNTIME") != "1":
        pytest.skip("Owned local runtime not requested")


@pytest.mark.parametrize("filename", ["001_schema.sql", "001_policies.sql"])
def test_historical_registry_cannot_accept_changed_frozen_source(monkeypatch, filename):
    engine = bootstrap.admin_engine()
    original_read = Path.read_bytes
    with engine.connect() as connection:
        # Exercise the actual historical row under rollback, including on fresh installs.
        transaction = connection.begin()
        original = connection.execute(text("SELECT checksum FROM app.schema_migrations WHERE version='001_initial'")).scalar_one()
        connection.execute(text("UPDATE app.schema_migrations SET checksum=:legacy WHERE version='001_initial'"), {"legacy": bootstrap.LEGACY_INITIAL_CHECKSUM})
        bound_engine = SimpleNamespace(begin=lambda: nullcontext(connection))
        monkeypatch.setattr(bootstrap, "admin_engine", lambda: bound_engine)

        def changed_source(path):
            content = original_read(path)
            return content + b"\n-- synthetic integrity probe\n" if path.name == filename else content

        monkeypatch.setattr(Path, "read_bytes", changed_source)
        try:
            with pytest.raises(RuntimeError, match="Frozen migration 001 source checksum changed"):
                bootstrap.migrate()
            assert connection.execute(text("SELECT checksum FROM app.schema_migrations WHERE version='001_initial'")).scalar_one() == bootstrap.LEGACY_INITIAL_CHECKSUM
        finally:
            transaction.rollback()
        assert connection.execute(text("SELECT checksum FROM app.schema_migrations WHERE version='001_initial'")).scalar_one() == original
    engine.dispose()


def test_administrative_target_rejects_missing_project_prefix(monkeypatch):
    monkeypatch.setenv("KAJH_PROJECT", "unrelated-project")
    with pytest.raises(RuntimeError, match="owned project prefix"):
        bootstrap.admin_engine()


def test_administrative_target_rejects_different_actual_cluster_owner(monkeypatch):
    monkeypatch.setenv("KAJH_PROJECT", "kajh-p1-synthetic-wrong-owner")
    with pytest.raises(RuntimeError, match="different cluster owner"):
        bootstrap.admin_engine()
