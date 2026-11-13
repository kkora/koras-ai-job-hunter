# P1 milestone ledger

Authorization: **KAJH-P1-AUTH-001**. Date: November 13, 2026. Commits are local only. This ledger records implementation evidence, not owner acceptance.

| Milestone | Implemented files and behavior | Verification | Result / remaining risk |
|---|---|---|---|
| M1 foundation | `pyproject.toml`, `uv.lock`, `infrastructure/local/*`, ownership-safe lifecycle scripts, initial migrations, configuration and health | Locked Python dependencies installed; Linux ARM64 digest-pinned images pulled and application built; actual PostgreSQL 17.12/pgvector 0.8.7 and GoTrue health; local API and dashboard started | PASS for local foundation. Requires existing Docker Desktop; development HTTP only. |
| M2 security/domain | `services/api/kajh_api/*`, `database/migrations/*`, `tests/security/*`, versioned profiles and source registry | Actual GoTrue login; role, workspace, CSRF, revoked-session, forged-token, RLS and privilege negatives; current source visibility and dedup reversal | PASS for tested P1 boundaries. E-02 remains OPEN; no production security claim. |
| M3 ingestion/eligibility | `packages/domain-python/kajh_domain/*`, worker ingestion, synthetic fixture, unit/integration tests | 66 deterministic domain tests and 8 PostgreSQL ingestion tests initially passed; 14 observations → 13 canonical opportunities; leases, cursor replay, DLQ, retention and provenance exercised | PASS for initial tests. Final regression and additional retention hardening recorded in acceptance evidence. |
| M4 dashboard/workflow | `apps/web/*`, worker workflow, PostgreSQL checkpointing, browser/workflow tests | 13 LangGraph tests; crash/restart and approval replay; 13 Chromium E2E cases; desktop/mobile axe scans and visual inspection; Next build/lint/typecheck | PASS for tested P1 features. Mock processing only; no external dispatch or P2 ranking. |
| M5 hardening | Runtime validation scripts, security/operations documentation, final regression | Vector benchmark, role/RLS checks, outage/egress tests, backup/restore and final quality gates | See final `P1-acceptance.md`; incomplete tests must remain explicit until evidence exists. |

Local commit hashes and final counts are recorded in the acceptance report after each commit. No remote repository is created. Tests leave labeled synthetic audit/history records in the development database.
