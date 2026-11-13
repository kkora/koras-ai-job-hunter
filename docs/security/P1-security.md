# P1 security boundaries

Authorization: KAJH-P1-AUTH-001. This implementation is local development with synthetic data only. Real source entitlement, cloud deployment, paid inference and outbound business actions require separate decisions.

## Identity and authorization

GoTrue issues one-hour signed JWTs. The API verifies algorithm, signature, issuer, audience, expiry, subject, session ID and authenticated role. Database functions resolve the current Auth session and active workspace membership. A JWT workspace claim or client-supplied header never creates membership. Logout revokes locally even if GoTrue is unavailable. Browser session cookies are HttpOnly and SameSite Strict; CSRF cookies are session-bound HMAC values checked against the header and an allowlisted Origin on writes.

HTTP cookies deliberately omit Secure for this loopback HTTP development environment. No production TLS claim is made. GoTrue email signup/autoconfirm is local and synthetic; creating an Auth account does not grant application workspace membership. No OAuth, SMS, external SMTP or service-role browser key is configured.

`kajh_api` and `kajh_worker` are separate nonsuperuser, NOBYPASSRLS roles. API identity is transaction-local, not connection-global. API mutations enforce role and optimistic version checks. Application tables use RLS; database security-definer gates have fixed search paths, narrowly granted execution, and trusted owner definitions. Saved opportunities are user-scoped. Worker workspace access comes from server-side grants; it cannot insert approval decisions or become the API identity. Administrative migrations and backup tools hold local administration credentials outside the application image.

## Content, sources and workflows

The UI renders source text as text, never as executable HTML. Nonce CSP, frame denial and nosniff headers protect the dashboard. Input sizes and JSON fields are bounded. API errors return stable codes and request IDs without SQL parameters or credentials. Structured application logs record event metadata without request bodies, passwords or tokens. OpenTelemetry spans are compatible with a future exporter; no external telemetry exporter is configured.

All real acquisition adapters fail closed, even if an administrative source record is manually marked approved. Synthetic collection requires active explicit commercial, collection, display, storage, evidence, quota, attribution, retention and expiry permissions. AI and embeddings have separate rights gates. P1 has no external AI inference or embedding generation. Source kill/revocation immediately prevents visible results from being served and prevents new processing; retention deletes expired observations and rebuilds or purges canonical evidence.

There is no arbitrary URL fetch interface. API and worker Docker containers attach only to the private internal network. They have no Docker socket, messaging secrets or model-provider keys. The separate ingress TCP gateway has fixed destinations and no application credentials. Local mock workflow nodes cannot invoke a browser or external dispatcher. Prompt-like source text is data for deterministic rules, never an instruction with tools.

LangGraph approval is independent of graph state: a SQL gate atomically checks actor membership/role, expiry, run version, opportunity content hash and consumed status. Per-thread locks and checkpoint persistence handle replay/restart. Restored workflows are held and old decisions/sessions cleared before any potential release. Source permission and evidence expiry are checked again when resuming.

## Local operational limits

Secrets are random local credentials in ignored mode-0600 `.local/env`; only a deliberate `manage.py credentials` command prints synthetic account passwords. `.local`, Git history, caches, tests, frontend dependencies and documents are excluded from the runtime Docker build context. Images run as an unprivileged application user with read-only root filesystems, dropped capabilities and bounded CPU/memory/logs where supported. PostgreSQL maintains its required writable persistent volume.

Host administrators and anyone with Docker daemon access remain trusted local operators. Local plaintext loopback transport, development web server, local backup files and the Docker VM are not a production security boundary. No encrypted off-device backup, HA, disaster recovery SLA, object storage restoration, penetration test or production compliance certification is asserted.

Dependency audit results, runtime role flags, negative authorization tests and E-09 recovery evidence are recorded in `docs/requirements/P1-acceptance.md`. Do not reinterpret a finite test suite as proof against all attacks.
