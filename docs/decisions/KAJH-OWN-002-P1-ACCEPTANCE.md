# KAJH-OWN-002 — Final P1 owner acceptance

- Decision date: November 13, 2026
- Owner: Koras Technologies LLC
- Project: Koras AI Job Hunter
- Phase: P1 — Final Acceptance
- Owner decision: **APPROVED — ACCEPTED FOR BOUNDED LOCAL SYNTHETIC-ONLY USE**

The project owner explicitly approved KAJH-OWN-002. This is the owner's acceptance decision, not a claim that every originally proposed runtime test passed. It accepts the implemented P1 application in `/Users/kan/Projects/koras-ai-job-hunter` for bounded local synthetic-only use, qualifies closure of E-09, and retains E-02 as an open blocker to all real source activation.

## Accepted scope and evidence

The accepted scope includes the private local application, synthetic vendor/search profiles, source-policy controls, synthetic ingestion, deterministic four-state B2B eligibility, provenance and reversible deduplication, the dashboard, and the bounded LangGraph foundation with PostgreSQL checkpoints and no outbound dispatch.

This record references existing evidence without changing it, rerunning tests, or extending independent verification beyond the available records. The implementation authorization remains [KAJH-P1-AUTH-001](KAJH-P1-AUTH-001.md). The [acceptance evidence](../requirements/P1-acceptance.md) and [milestone ledger](../requirements/P1-milestones.md) retain the implementation results and limitations.

| Reported result accepted by the owner | Existing evidence |
|---|---|
| Five completed local milestones | Milestone ledger; M1 `82d6c6a`, M2 `b5634d1`, M3 `e6f0ead`, M4 `7759cc9`, M5 `9211ae4` |
| 126 Python tests passed, with no failures or skips | `.local/evidence/pytest-results.xml` |
| 13 Chromium browser tests passed; desktop/mobile axe scans reported zero violations | `.local/evidence/playwright-results.json`, `axe-desktop.json`, `axe-mobile.json` |
| PostgreSQL migrations 001–004 applied; 20 scoped tables enforce RLS | Acceptance report, recorded security/migration cases, `.local/evidence/P1-final-status.json` |
| 14 synthetic observations normalized to 13 canonical opportunities; four-state eligibility and deduplication | Recorded domain/ingestion/API test cases and milestone ledger |
| PostgreSQL-backed LangGraph checkpoint recovery | Recorded workflow test cases and `.local/evidence/E09-backup-restore.json` |
| Restore compared 21 table inventories and 616 checkpoint rows | `.local/evidence/E09-backup-restore.json`; snapshot counts include the disposable probe before cleanup |
| pgvector: 10,000 vectors, 98.6% recall@10, approximately 9 ms p95 | `.local/evidence/E09-vector.json`; bounded synthetic benchmark |
| Local-only Git repository | Existing local commit history and read-only Git checks; no remote configured |

The evidence directory is private and ignored by Git. No generated evidence, credentials, raw backup, or browser trace is included in this decision commit. These are historical execution results; this documentation instruction does not establish a new test run or new runtime validation.

## Exception register

| Exception | Current disposition | Scope and controlling decision | Remaining gate |
|---|---|---|---|
| E-09 — Local Runtime Validation | **QUALIFIED CLOSED** | Demonstrated local P1 runtime only; decision authority **KAJH-OWN-002** | Mandatory exclusions below remain outstanding; no unqualified or production-readiness closure |
| E-02 — Source Entitlements | **OPEN — ACTIVATION BLOCKED** | All real job sources remain disabled | Accepted source-specific entitlement evidence and separate owner activation authorization |

E-09 qualified closure covers the demonstrated local Docker runtime; PostgreSQL and migrations; pgvector; standalone Supabase GoTrue authentication; LangGraph checkpoints and recovery; PostgreSQL backup/restore; scoped outage and cached-restart behavior; bounded resource measurements; isolation/cleanup; and tested security boundaries. It does not imply completion of every earlier E-09 subcheck.

## E-09 mandatory exclusions and future gates

Every item below remains **NOT VERIFIED**. None is converted to PASS by owner acceptance or qualified closure.

| Outstanding verification item | Current evidence status and scope | Future gate |
|---|---|---|
| 1. Actual Mailpit email capture | **NOT VERIFIED**; local sink presence/health does not establish message capture | Separate owner authorization for a bounded synthetic email-capture validation; no external email |
| 2. Additional explicit foreign-key and isolation-negative scenarios | **NOT VERIFIED** for those additional scenarios; existing RLS/role negatives remain recorded separately | Separate owner authorization for scoped synthetic database validation |
| 3. Comprehensive outage tests against the final image | **NOT VERIFIED**; saved outage evidence predates the final image, while cached restart has later evidence | Separate owner authorization for final-image failure scenarios and an owned-service interruption window |
| 4. Sustained-load, storage-growth, and resource-contention testing | **NOT VERIFIED**; existing measurements are bounded samples, not sustained capacity evidence | Separate owner authorization with workload, resource/storage limits, contention scenarios and cleanup defined |
| 5. Full Supabase CLI-managed stack parity and object-storage restoration | **NOT VERIFIED — OUTSIDE ACCEPTED P1 SCOPE**; standalone GoTrue and PostgreSQL-only restoration were accepted | Separate architecture/scope decision and implementation/validation authorization before adding those components |
| 6. Production disaster recovery, penetration testing, and production security readiness | **NOT VERIFIED — OUTSIDE ACCEPTED P1 SCOPE**; no production or RPO/RTO assurance | Separate production/security/DR authorization, defined acceptance criteria and evidence; cloud, spending and deployment approvals remain separate |

The earlier planning-only lab package remains reference material under KAJH-P1-AUTH-001, not a requirement to materialize or execute it under this decision. No excluded validation or implementation is authorized by this documentation instruction.

## E-02 source entitlements

Remote OK, Lever, Greenhouse and Ashby remain candidate interfaces only, with activation **BLOCKED** and integrations **DISABLED**. Remotive remains **DEFERRED**.

No real source may be enabled without accepted source-specific evidence covering applicable commercial discovery, automated collection, internal display, AI processing, embeddings, persistent/evidence retention, backups/checkpoints, attribution, quotas, pricing, deletion/revocation, and other governing restrictions. API accessibility is not permission for the complete workflow. Owner approval cannot replace third-party licensing rights. This decision accepts no provider agreement and authorizes no provider contact or acquisition.

## Remaining risks and historical decisions

- Acceptance is limited to local development with synthetic data and unverified Koras business defaults. Loopback HTTP, the development web server, and trusted host/Docker administrators are not a production security boundary.
- The vector benchmark, resource samples and finite security/accessibility tests establish only their recorded scope. Restore approval used a trusted Owner-scoped synthetic fixture, not an interactive owner login. The archive was deleted after the exercise; no retained off-device recovery strategy is established.
- All six E-09 exclusions and E-02 source entitlement blockers remain visible above. Actual Koras legal qualifications, registrations, certifications and commercial terms require verified owner-provided facts before any separately authorized real operational use.
- **KAJH-OWN-001 remains the historical conditional P0 architecture decision.** Preserve the original results: AC-01 PASS; AC-02 BLOCKED; AC-03 through AC-08 PASS; AC-09 NOT VERIFIED; AC-10 through AC-12 PASS. Historical total: **10 PASS, 1 BLOCKED, 1 NOT VERIFIED**. The original full P0 gate is not retroactively closed or rewritten.

## Authorization boundaries and future decisions

KAJH-OWN-002 does **not** authorize P2 implementation, real job-source ingestion, billable external AI/model calls, paid services, cloud provisioning/deployment, GitHub repository creation or remote push, recruiter outreach, job applications, proposal or procurement submissions, external communications or commercial commitments, changes to other Koras repositories, or production use. **No outbound dispatch capability may be enabled.**

Future phases, excluded validation, source activation after entitlement acceptance, and any cloud, paid, external-action or production scope require separate explicit owner authorization. Acceptance of P1 is not authorization to perform those activities. The only execution authorized by this instruction is the minimum local documentation updates and one reviewed documentation-only commit; no application, migration, dependency, configuration, test or generated-evidence changes are permitted.

## Final owner decision

**KAJH-OWN-002 is APPROVED. P1 is ACCEPTED — LOCAL SYNTHETIC ONLY. E-09 is QUALIFIED CLOSED for the demonstrated local P1 runtime, with every exclusion retained. E-02 remains OPEN; real sources remain DISABLED. P2 is NOT AUTHORIZED. Await separate owner authorization before further work.**
