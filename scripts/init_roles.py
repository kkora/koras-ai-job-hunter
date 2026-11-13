"""Bootstrap only a cluster carrying this project's unique cluster name."""
from __future__ import annotations

import psycopg
from psycopg import sql

from bootstrap import load_env


def init_roles(*, restore: bool = False, schemas: bool = True) -> None:
    env = load_env()
    port = 55440 if restore else int(env["KAJH_DB_PORT"])
    expected = env["KAJH_PROJECT"] + ("-restore" if restore else "")
    with psycopg.connect(host="127.0.0.1", port=port, dbname="kajh_dev", user="postgres",
                         password=env["KAJH_DB_PASSWORD"], autocommit=True) as conn:
        cluster = conn.execute("SELECT current_setting('cluster_name')").fetchone()
        if not cluster or cluster[0] != expected:
            raise RuntimeError("Refusing to modify an unrecognized database cluster")
        roles = {"kajh_owner": None, "kajh_api": env["KAJH_API_PASSWORD"],
                 "kajh_worker": env["KAJH_WORKER_PASSWORD"], "kajh_auth_admin": env["KAJH_AUTH_PASSWORD"]}
        for role, password in roles.items():
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                flags = "NOLOGIN" if password is None else "LOGIN NOINHERIT"
                query = sql.SQL("CREATE ROLE {} " + flags + " NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(sql.Identifier(role))
                if password:
                    query += sql.SQL(" PASSWORD {}").format(sql.Literal(password))
                conn.execute(query)
        conn.execute("ALTER ROLE kajh_auth_admin NOCREATEROLE")
        conn.execute("REVOKE CONNECT ON DATABASE kajh_dev FROM PUBLIC")
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        conn.execute("GRANT CONNECT ON DATABASE kajh_dev TO kajh_api,kajh_worker,kajh_auth_admin")
        conn.execute("REVOKE CREATE ON DATABASE kajh_dev FROM kajh_auth_admin")
        conn.execute("ALTER ROLE kajh_auth_admin SET search_path=auth")
        conn.execute("ALTER ROLE kajh_api SET statement_timeout='15s'")
        conn.execute("ALTER ROLE kajh_worker SET statement_timeout='30s'")
        if schemas:
            conn.execute("CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION kajh_owner")
            conn.execute("CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION kajh_auth_admin")
            conn.execute("CREATE SCHEMA IF NOT EXISTS checkpoints AUTHORIZATION kajh_worker")
            conn.execute("GRANT USAGE ON SCHEMA app TO kajh_api,kajh_worker")
            conn.execute("GRANT USAGE ON SCHEMA public TO kajh_owner,kajh_api,kajh_worker")
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        print(f"Owned {'restore' if restore else 'primary'} cluster roles initialized")


if __name__ == "__main__":
    init_roles()
