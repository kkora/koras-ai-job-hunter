# P1 implementation and acceptance evidence

Date: November 13, 2026. Repository: `/Users/kan/Projects/koras-ai-job-hunter`. Authorization: **KAJH-P1-AUTH-001**. Architecture baseline: **KAJH-OWN-001**. All data is synthetic; all Git history is local.

**IMPLEMENTED** means working source exists. **TESTED** means an automated check executed. **VERIFIED** means the described local runtime behavior was observed. **BLOCKED** denotes an unmet external prerequisite. **DEFERRED** denotes later-phase capability. Neither technical evidence nor this report substitutes for owner acceptance.

## P1 coverage

| Requirement | Implementation and observed evidence | Status |
|---|---|---|
| P1-A private foundation | Actual GoTrue password login; Owner/Reviewer/Auditor; session revocation; verified workspace identity; RLS; health/readiness; audit and structured JSON request logs | IMPLEMENTED / TESTED / VERIFIED |
| P1-B vendor profile | Synthetic legal/vendor fields, regions, terms, rates, capabilities, exclusions; immutable versions and optimistic updates; changes queue reassessment | IMPLEMENTED / TESTED / VERIFIED; actual Koras qualifications unverified |
| P1-C search profiles | Microsoft-specialty presets and versioned JSON filters for geography, workplace, B2B, rates, seniority, employment exclusions and aliases | IMPLEMENTED / TESTED / VERIFIED |
| P1-D source registry | Permissions, capabilities, expiry, retention, quotas, attribution, kill/revocation; interface stubs for four candidates; real adapters denied at API, database and worker layers | IMPLEMENTED / TESTED / VERIFIED; real rights BLOCKED |
| P1-E ingestion | Durable fenced jobs; source runs/cursors; normalization, provenance, bounded retries, redacted DLQ, idempotent observations; 14 observations → 13 canonical records | IMPLEMENTED / TESTED / VERIFIED |
| P1-F B2B eligibility | Eligible / Ineligible / Unknown / Needs Review; deterministic business, geography, residency, time-zone, delivery, procurement, date, currency/rate and fraud rules; missing facts remain Unknown | IMPLEMENTED / TESTED / VERIFIED |
| P1-G canonical records | Versioned terms/skills/facts/evidence/lifecycle, explicit requisition-based deduplication; distinct requisitions retained; Owner-scoped provenance split and idempotent replay | IMPLEMENTED / TESTED / VERIFIED |
| P1-H dashboard | Overview, search/filter/detail, evidence/history, review queue, saved items, source health, profiles, workflow/source-run/job metadata; responsive desktop/mobile | IMPLEMENTED / TESTED / VERIFIED |
| LangGraph foundation | Typed/versioned state, real PostgreSQL checkpoints, mock provider, deterministic gates, interrupt/resume, independent atomic SQL approval, process crash/replay recovery, budgets and bounded errors | IMPLEMENTED / TESTED / VERIFIED; zero paid calls |
| P2 intelligence | External AI interpretation, live verification, embeddings generation, AI ranking, digests and proposals | DEFERRED / disabled |
| Outbound business actions | Recruiter messaging, applications and external dispatch routes/credentials | ABSENT / disabled |

## Database and migration evidence

PostgreSQL **17.12**, pgvector **0.8.7**, Linux **ARM64**, Python **3.12.10**, GoTrue **2.197.0**. Runtime images are digest-pinned in `infrastructure/local/images.lock.json`. Application migrations **001–004** are applied. All **20 scoped application tables** enforce RLS; the administrative migration registry is separate. API, worker and Auth runtime roles are nonsuperuser, NOBYPASSRLS, NOCREATEDB and NOCREATEROLE.

Fresh frozen migrations replayed in an isolated, ownership-verified scratch database, then replayed as a no-op. The scratch database was removed. The historical deployed 001 checksum remains unchanged; a pinned frozen initial-schema checksum supports reproducible new databases. Virtual schema/policy drift, missing project prefix and wrong-cluster identity were rejected in four actual-database regression cases. No applied SQL was reformatted merely to remove generated whitespace.

## Quality gates

Final frozen-source regression: **126 Python tests passed in 16.32 seconds**, with no failures or skips. Breakdown: 66 domain, 15 ingestion, 13 workflow, 11 API integration, 9 API security, 4 database boundaries, 4 migration integrity, 3 runtime-environment/ownership and 1 contract test. No skipped integration test is counted as PASS.

| Command / evidence | Result |
|---|---|
| `python scripts/manage.py test -q --junitxml=.local/evidence/pytest-results.xml` | PASS, 126 tests / 16.32 seconds |
| `ruff check scripts services packages tests infrastructure/local/gateway.py` | PASS |
| `mypy scripts services/api/kajh_api services/worker/kajh_worker packages/domain-python/kajh_domain infrastructure/local/gateway.py` | PASS, 26 source files |
| `npm --prefix apps/web run typecheck` | PASS |
| `npm --prefix apps/web run lint` | PASS |
| `npm --prefix apps/web run build` | PASS, optimized Next.js build |
| `PLAYWRIGHT_BROWSERS_PATH=../../.local/playwright npx playwright test` from `apps/web` | PASS, 13 Chromium browser cases / 11.5 seconds against final API/worker build |
| Desktop/mobile axe WCAG scans | PASS, zero reported violations each; manual keyboard-symbol checks are not an automated accessibility certification |
| Desktop/mobile screenshots | Visually inspected; readable responsive layout without observed overflow |
| `npm audit --omit=dev --json` | PASS, zero reported production vulnerabilities at execution time; not a comprehensive security audit |

The suites exercise database constraints and role escalation negatives, forged/expired/revoked identity, CSRF, cross-workspace isolation, source permission/revocation, four-state eligibility, rate/currency normalization, conservative dedup/split, replay/cursors, retention including orphan historical excerpts, worker leases/DLQ, strict source provenance before processing, graph authorization/budgets/retries/timeouts, process restart and approval consumption exactly once, contracts, browser roles/navigation/forms, inert hostile source text, nonce CSP and error recovery.

## E-09 bounded local evidence

The historical P0 result is preserved. KAJH-P1-AUTH-001 separately authorized these stateful tests; their actual evidence is new P1 evidence.

| Area | Observed result and private evidence | Status |
|---|---|---|
| Docker / ARM64 | Actual Engine 29.9.2 / Desktop 4.98.0, digest-pinned ARM64 images; owned containers, networks and volume | VERIFIED |
| PostgreSQL / pgvector | Actual extension, constraints, restricted-role reads, RLS isolation, dimension rejection, exact cosine order and HNSW index plan | VERIFIED |
| Vector benchmark | 10,000 deterministic 1,536-dimension synthetic vectors, 50 queries, shared cross-workspace filter, `ef_search=1000`; mean recall@10 **0.986**, p95 **0.00897 s**, build **10.74 s**; initial lower search breadth missed recall threshold and was tuned | VERIFIED for this fixture only |
| Auth | Real password login/session lookup/revocation; hostile claims and role/workspace negatives; Auth runs without role/database creation privileges | VERIFIED |
| Checkpoints | Real PostgreSQL persistence, interrupt and resume in new processes, actual injected crash after atomic consume, recovery with one consumption audit | VERIFIED |
| Backup / restore | Custom archive restored into separate owned volume; **21 table inventories/hashes** and **616 checkpoint rows** matched in the recorded run; restored sessions/decisions cleared, workflows held, consumed approval blocked, fresh Owner-scoped fixture approval required, new-process resume consumed once; actual expiry purge | VERIFIED; repeated with final frozen source |
| Restore safety | Primary probe cleanup compared original table hashes; temporary archive deleted; disposable restore containers, volume and networks removed; original services restarted even on failed attempts | VERIFIED |
| Offline / failure | Actual API+worker TCP egress attempts blocked; Auth-down login503 and local logout; DB-down protected/readiness503, liveness200; pool recovery; no host networking changes | VERIFIED |
| Resource limits | Owned service CPU/memory/log limits, private-only worker, loopback-only ingress, measured idle Docker stats | VERIFIED; no sustained-load/production capacity claim |
| Cached restart | `manage.py stop` then `manage.py resume`, no pulls/builds/migrations/seed | VERIFIED, same image IDs; web/API ready; pull policy never |

Private machine evidence is in ignored `.local/evidence/`: `M1-runtime.json`, `E09-vector.json`, `E09-backup-restore.json`, `E09-offline.json`, `E09-resources.json`, `E09-json-logging.json`, `E09-cached-restart.json`, `P1-final-integrity.json`, `pytest-results.xml`, `playwright-results.json`, axe JSON and desktop/mobile PNGs. Raw secrets, database archives and browser traces are not committed.

## Architecture choices and limits

- A credentialless fixed TCP ingress service was necessary because internal-only Docker bridges did not publish working host ports on this Mac. API/worker remain on the internal network; only the gateway publishes loopback ports. See ADR-001.
- Local standalone GoTrue supplies Supabase Auth compatibility; a full Supabase deployment, hosted Auth and object storage are not claimed.
- The separate SQL gate approves bounded local workflow review. There is no production external-action dispatch gateway because P1 has no dispatch capability.
- The web server uses a minimal environment with no database/JWT/account/provider secrets. Host administration and Docker access remain trusted; loopback HTTP and the development web server are not production hardening.
- OpenTelemetry-compatible spans and JSON logging are implemented; no external exporter or observability SaaS is configured.
- ESLint 9 is pinned for the Next.js peer range although the registry marks that major deprecated. It is a development tool; migration requires a compatible Next/toolchain update. The production dependency audit reported no findings.
- The backup test covers PostgreSQL only. No P1 object storage exists. There is no retained/encrypted off-device backup, RPO/RTO commitment, HA, long-duration soak, production penetration test or external-source capacity validation.

## Preserved P0 criteria and owner decisions

| Historical P0 criterion | Original result |
|---|---|
| AC-01 | PASS |
| AC-02 | BLOCKED |
| AC-03 through AC-08 | PASS each |
| AC-09 | NOT VERIFIED |
| AC-10 through AC-12 | PASS each |

Historical total: **10 PASS, 1 BLOCKED, 1 NOT VERIFIED**. P0 architecture remains conditionally accepted under KAJH-OWN-001; original full P0 gate remains blocked. New E-09 technical evidence does not rewrite that historical decision.

| Decision | Owner action / evidence required | Current state |
|---|---|---|
| Accept bounded local P1 | Review this implementation, tests and documented limits; accept the synthetic-only scope | PENDING OWNER REVIEW |
| E-09 exception disposition | Review actual bounded runtime evidence and decide whether to close/qualify E-09 | Technically verified within documented limits; owner disposition pending |
| E-02 source entitlement | For each candidate obtain and accept evidence for commercial discovery, automated collection, private display, external AI, embeddings, persistent/evidence retention, backups/checkpoints, attribution, quotas, pricing and deletion/revocation | OPEN / BLOCKING every real source |
| Koras business facts | Supply verified legal/vendor qualifications, registrations, certifications, delivery constraints and commercial terms before real operational use | Synthetic defaults only |
| Future phase or deployment | Separately authorize P2, real-source activation after third-party rights, cloud/paid services or external business actions | NOT AUTHORIZED by P1 |

Remote OK, Lever, Greenhouse and Ashby remain candidate interfaces only. Remotive remains deferred pending appropriate commercial/API permissions. No accessible endpoint, owner preference or synthetic test establishes third-party rights. No provider was contacted or agreement accepted.

## Local commits

| Milestone | Commit |
|---|---|
| M1 isolated foundation | `82d6c6a` |
| M2 security/domain | `b5634d1` |
| M3 ingestion/checkpoints | `e6f0ead` |
| M4 dashboard/workflows | `7759cc9` |
| M5 hardening/evidence | Recorded in final conversation and local Git log after validation |

Recommendation: accept local synthetic P1 after owner review with E-02 and the stated production/later-phase limits retained. Do not activate sources or infer P2/cloud/outbound authorization from P1 acceptance.
