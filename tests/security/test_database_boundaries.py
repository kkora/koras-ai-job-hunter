"""Exercise the restricted API PostgreSQL login, not just application filters."""
import os
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy.engine import make_url


pytestmark = pytest.mark.integration


def dsn(variable):
    return make_url(os.environ[variable]).render_as_string(hide_password=False).replace("postgresql+psycopg:", "postgresql:")


@pytest.fixture
def actual_principals():
    if os.environ.get("KAJH_TEST_RUNTIME") != "1":
        pytest.skip("Owned local runtime not requested")
    with psycopg.connect(dsn("DATABASE_ADMIN_URL"), autocommit=True) as admin:
        values = admin.execute("SELECT role,user_id,workspace_id FROM app.memberships WHERE active AND workspace_id='00000000-0000-0000-0000-000000000001'").fetchall()
    return {role: (str(user), str(workspace)) for role, user, workspace in values}


def bind(connection, principal):
    user, workspace = principal
    connection.execute("SELECT set_config('app.user_id',%s,true),set_config('app.workspace_id',%s,true)", (user, workspace))


def test_real_api_role_has_no_auth_or_admin_privileges(actual_principals):
    for statement in ("SELECT * FROM auth.users", "SELECT * FROM auth.sessions", "SET ROLE kajh_owner", "SET ROLE postgres", "UPDATE app.audit_events SET action='tampered'", "DELETE FROM app.source_permissions"):
        with psycopg.connect(dsn("DATABASE_URL")) as connection:
            bind(connection, actual_principals["Owner"])
            with pytest.raises(psycopg.Error) as caught:
                connection.execute(statement)
            assert caught.value.sqlstate == "42501"
            connection.rollback()


def test_workspace_guc_alone_cannot_authorize_another_workspace(actual_principals):
    probe = uuid4()
    other_workspace = "00000000-0000-0000-0000-000000000002"
    with psycopg.connect(dsn("DATABASE_ADMIN_URL"), autocommit=True) as admin:
        admin.execute("INSERT INTO app.vendor_profiles(id,workspace_id,version,synthetic,profile,created_at) VALUES (%s,%s,99,true,'{}',now())", (probe, other_workspace))
        try:
            with psycopg.connect(dsn("DATABASE_URL")) as connection:
                bind(connection, actual_principals["Owner"])
                connection.execute("SELECT set_config('app.workspace_id',%s,true)", (other_workspace,))
                assert connection.execute("SELECT count(*) FROM app.vendor_profiles WHERE id=%s", (probe,)).fetchone()[0] == 0
                connection.rollback()
        finally:
            admin.execute("DELETE FROM app.vendor_profiles WHERE id=%s", (probe,))


def test_private_saved_rows_are_user_scoped(actual_principals):
    probe = uuid4()
    owner_user, workspace = actual_principals["Owner"]
    with psycopg.connect(dsn("DATABASE_ADMIN_URL"), autocommit=True) as admin:
        opportunity = admin.execute("SELECT id FROM app.opportunities WHERE workspace_id=%s LIMIT 1", (workspace,)).fetchone()[0]
        existing = admin.execute("SELECT id FROM app.saved_opportunities WHERE user_id=%s AND opportunity_id=%s", (owner_user, opportunity)).fetchone()
        if existing:
            probe = existing[0]
        else:
            admin.execute("INSERT INTO app.saved_opportunities(id,workspace_id,user_id,opportunity_id,created_at) VALUES (%s,%s,%s,%s,now())", (probe, workspace, owner_user, opportunity))
        try:
            with psycopg.connect(dsn("DATABASE_URL")) as connection:
                bind(connection, actual_principals["Reviewer"])
                assert connection.execute("SELECT count(*) FROM app.saved_opportunities WHERE id=%s", (probe,)).fetchone()[0] == 0
                connection.rollback()
        finally:
            if not existing:
                admin.execute("DELETE FROM app.saved_opportunities WHERE id=%s", (probe,))


def test_database_blocks_real_source_enablement_even_with_owner_context(actual_principals):
    with psycopg.connect(dsn("DATABASE_URL")) as connection:
        bind(connection, actual_principals["Owner"])
        with pytest.raises(psycopg.Error) as caught:
            connection.execute("UPDATE app.sources SET enabled=true WHERE adapter_type='lever'")
        assert caught.value.sqlstate == "23514"
        connection.rollback()
