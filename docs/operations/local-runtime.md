# Local runtime operations

Use the root README lifecycle commands. All resources carry the unique instance prefix from ignored `.local/instance.json` and label `io.koras.kajh.project`. The lifecycle/validation tools check both before destructive operations. They target a fixed local Docker socket and isolated project Docker-client configuration, without changing global context or registry credentials.

## Data and migrations

Application, Auth and checkpoint schemas live in the single owned `kajh_dev` PostgreSQL database, using separate principals. Numbered application migrations are checksum-verified and recorded in `app.schema_migrations`. The initial deployed checksum is retained when a frozen initial schema replaces ORM-generated bootstrap DDL; later changes use separate migrations. GoTrue manages its own Auth migrations. LangGraph sets up its PostgreSQL checkpoint schema under the worker role.

Seeding is idempotent and creates synthetic users, two isolated workspaces, a clearly labeled vendor profile, search presets and a synthetic source. Four candidate real sources remain disabled. Ingestion creates durable jobs and source runs, observes cursor progress, preserves source provenance, and produces 13 canonical opportunities from the 14-item synthetic fixture. Tests may add synthetic profiles, audit events, review history and workflow runs; this is development data, not a pristine demonstration database.

Do not edit an applied migration or delete `.local/env` while retaining its database volume. Do not point these scripts at another database. The administrative helpers verify PostgreSQL `cluster_name` against the instance prefix. No blanket Docker prune or unscoped volume removal is used.

## Recovery and offline use

The cached `resume` command starts existing owned containers without pulls, builds, migrations or seeding. Local search, saved data, settings and synthetic workflow review require the local database. No network means no live opportunities or external inference, which are absent in P1 regardless.

If GoTrue stops, new login fails with `AUTH_UNAVAILABLE` (503). Existing signed sessions are validated against local database session/membership records, and local logout remains authoritative. If PostgreSQL stops, protected reads and readiness fail with `DATABASE_UNAVAILABLE` (503), while liveness remains available. The worker backs off on dependency failures without logging credentials or dispatching anything. Reconnection uses a checked SQLAlchemy pool.

`validate_runtime.py backup-restore` quiesces only this project's API, worker and Auth, creates a mode-0600 custom-format dump, restores into a separate owned PostgreSQL volume, and compares table inventories and checkpoint persistence. Before graph execution can be released, restored decisions and Auth sessions are invalidated and workflows put on hold. The test validates role/RLS preservation, retention and approval/recovery behavior and then removes only its restore resources and temporary dump. Original services restart in `finally` cleanup. A test exercise is not a retained backup strategy.

The P1 backup scope is the PostgreSQL database. There are no uploaded objects or storage service in P1. Future object bytes, external search indexes, cryptographic keys and cross-system backup consistency require a separate design and validation. Backups/checkpoints of real source content also require explicit source permissions under E-02.

## Troubleshooting

- Web log: `.local/logs/web.log`; generated artifacts: `.local/evidence/`.
- Status: `.venv/bin/python scripts/manage.py status`.
- Credentials: `.venv/bin/python scripts/manage.py credentials` (synthetic only).
- Health: `http://127.0.0.1:8210/health`; dependency readiness: `/ready`.
- Source disabled, killed, revoked or expired: keep it blocked until a valid, authorized policy exists. Do not bypass policy checks to clear an error.
- Failed job: inspect job/source-run metadata in the private workflow dashboard. Retries are bounded; dead letters retain redacted error metadata.
- Workflow awaiting review: an Owner/Reviewer must approve the current content/version before the worker resumes. Stale or consumed approval is invalid.

Shutdown preserves data. No deletion/reset command is provided to avoid accidental loss. Do not expose these loopback development services to a LAN or the internet.
