# Koras AI Job Hunter — local P1

Private, synthetic-only B2B opportunity discovery for Koras Technologies LLC. Authorized by **KAJH-P1-AUTH-001**, following the conditionally accepted **KAJH-OWN-001** architecture. The owner accepted P1 for bounded local synthetic-only use under [KAJH-OWN-002](docs/decisions/KAJH-OWN-002-P1-ACCEPTANCE.md). E-09 is QUALIFIED CLOSED for the demonstrated local runtime, with its exclusions retained; E-02 remains OPEN and all real sources disabled. P2 and production use are not authorized.

## Open the application

The local dashboard is **http://127.0.0.1:3210**. Obtain the generated synthetic accounts without putting passwords into Git:

```sh
cd /Users/kan/Projects/koras-ai-job-hunter
.venv/bin/python scripts/manage.py credentials
```

Sign in as `owner@kajh.invalid`, `reviewer@kajh.invalid`, or `auditor@kajh.invalid`. Passwords are independently generated and stored in ignored `.local/env` with mode 0600. Owner manages configuration; Reviewer reviews opportunities and local workflows; Auditor reads only. Every profile, company, opportunity, and credential is synthetic.

## Start and stop

```sh
# Normal startup: verify images, apply numbered migrations, seed idempotently,
# build local API/worker image, start owned services and the web application.
.venv/bin/python scripts/manage.py up

# Stop only this project's services; retain the database volume.
.venv/bin/python scripts/manage.py stop

# Restart an existing owned runtime from cached images and dependencies.
# No registry access, build, migration, seed, or installation is attempted.
.venv/bin/python scripts/manage.py resume

.venv/bin/python scripts/manage.py status
```

Docker Desktop must already be running. These scripts never start Docker Desktop, change global Docker settings, prune resources, or touch other applications. Do not delete `.local/instance.json` or `.local/env`: they bind ownership and credentials to the persistent local database.

## Services

| Component | Loopback address | Purpose |
|---|---|---|
| Next.js | `127.0.0.1:3210` | Private dashboard, same-origin API proxy |
| FastAPI | `127.0.0.1:8210` | Health/readiness and authenticated API |
| PostgreSQL 17 / pgvector | `127.0.0.1:55439` | Application, Auth, jobs and checkpoints |
| Supabase GoTrue | `127.0.0.1:59999` | Local password authentication |
| Mailpit | `127.0.0.1:58029` | Local synthetic mail sink only |
| Worker | No published port | Durable synthetic ingestion, retention, local graph |

The credentialless TCP gateway publishes only these loopback ports. API and worker containers have only an internal Docker network. The database is persistent; Mailpit is disposable. Restore validation temporarily uses loopback 55440 and removes its own resources afterward.

## What works

- Authenticated overview, filtered search, opportunity detail, source provenance and evidence history, saved opportunities, review queue, source health, workflow/job monitoring, settings and profiles.
- Versioned vendor/search profiles; explicit synthetic defaults; deterministic four-state B2B eligibility.
- Durable synthetic ingestion with source-policy checks, cursors, retries, dead letters, provenance, conservative requisition-based deduplication, and owner-authorized provenance splitting.
- PostgreSQL RLS with restricted API/worker roles, verified JWT/session identity, workspace membership, CSRF protection, audit events and idempotency keys.
- Typed, versioned LangGraph with PostgreSQL checkpoints, mock processing, interrupt/resume, a separate SQL approval gate, replay protection and zero paid calls.
- Retention/expiry and source revocation hide restricted content immediately and purge retained content through the worker.

**Real adapters are disabled.** Remote OK, Lever, Greenhouse and Ashby are interface candidates; Remotive is deferred. E-02 permissions remain OPEN. An accessible API is not permission for commercial collection, AI processing, embeddings, storage, backups or evidence retention. There is no application submission, recruiter messaging, unrestricted URL fetch, autonomous browsing or external dispatch endpoint.

## Install prerequisites on a fresh checkout

Requires a locally available Python 3.12, `uv`, Node 24/npm, Docker Desktop with Linux ARM64 support, and the Docker Compose plugin. Installation downloads dependencies and images; offline operation requires those to be cached first. On the validated Mac these are already installed.

```sh
UV_CACHE_DIR="$PWD/.cache/uv" UV_PYTHON_DOWNLOADS=never uv sync --frozen --python 3.12
npm --prefix apps/web ci --cache "$PWD/apps/web/.cache/npm"
.venv/bin/python scripts/manage.py prepare
.venv/bin/python scripts/manage.py up
```

Python and npm dependencies are locked. Runtime images are pinned by ARM64 digest in `infrastructure/local/images.lock.json`. No cloud account, model key, production secret or external job feed is required.

## Verification

Tests use actual local PostgreSQL and GoTrue plus synthetic fixtures. Runtime tests create bounded, owned synthetic records. Outage/restore scripts briefly interrupt this application's services.

```sh
.venv/bin/python scripts/manage.py test -q
.venv/bin/ruff check scripts services packages tests infrastructure/local/gateway.py
.venv/bin/mypy scripts services/api/kajh_api services/worker/kajh_worker packages/domain-python/kajh_domain infrastructure/local/gateway.py
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint
npm --prefix apps/web run build

# Browser tests; Chromium must already be installed into .local/playwright.
cd apps/web
PLAYWRIGHT_BROWSERS_PATH=../../.local/playwright npx playwright test
cd ../..

.venv/bin/python scripts/validate_runtime.py vectors
.venv/bin/python scripts/validate_runtime.py backup-restore
.venv/bin/python scripts/validate_offline.py
.venv/bin/python scripts/validate_runtime.py inventory
```

See [acceptance evidence](docs/requirements/P1-acceptance.md), [operations](docs/operations/local-runtime.md), and [security boundaries](docs/security/P1-security.md). Machine-generated evidence and screenshots remain private in ignored `.local/evidence/`; reviewed results are recorded in repository documentation.
