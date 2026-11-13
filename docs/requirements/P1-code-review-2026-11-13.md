# P1 code review and fixes — November 13, 2026

The owner requested a code review and fixes for pending defects after GitHub governance commit `ae8742f`. This work stays within the existing local synthetic P1 scope on `develop`. It does not start P2, activate real sources, change GitHub protection, run hosted CI, deploy, make paid model calls, or perform external business actions.

## Findings fixed

| Area | Defect and resulting behavior |
|---|---|
| Workflow authority | Canonical changes left old reviews/workflows active and could prevent replacement assessments. Each assessment now binds to the canonical version; stale pending runs are held and exactly one current replacement is created. |
| Checkpoint recovery | Persisted state could skip initial validation on resume. Run/workspace/graph/policy/content metadata now require matching types and values; terminal checkpoint status must agree with durable state. Completion of review-required work independently requires a trusted consumed approval. |
| Provenance boundaries | Tenant-only foreign keys allowed a source run or observation to be attributed to a different source in the same tenant. Composite source/run/observation constraints now reject those mismatches, cross-workspace merge targets and self-merges. |
| Source permissions | Revocation could require a second disable request, and SQL/text/Python JSON comparisons could disagree about true, numeric 1 and string values. Revocation now disables atomically; SQL and Python permission gates fail closed on malformed or conflicting grants. JSON persistence and reserved-fact checks use strict JSON equality. |
| Concurrent API writes | Same-key concurrent requests could conflict instead of replaying; saved writes could race. Transaction-scoped locks serialize matching operations. Ingestion job keys are source-scoped bounded hashes, including for maximum-length request keys. |
| Canonical evidence | Saved counts included unavailable opportunities; source splitting could leave stale facts, expiry and evidence. Counts now use visible provenance; splitting reconstructs retained facts, evidence and expiry and replaces assessments. Legacy unmerge is versioned and idempotent. |
| Eligibility and normalization | Booleans/non-finite numbers, mixed malformed countries, missing commercial units/currency and nested invalid deadlines could be misclassified. These fail closed. Long provider identifiers cannot collide by truncation, requisition case is preserved conservatively, and bounded decimal formatting preserves exact digits. |
| API boundaries | Login stripped password whitespace; malformed nested profiles and non-finite JSON were insufficiently checked. Inputs now preserve password bytes and reject malformed configuration. Early rejection and unknown-route responses consistently carry structured errors, correlation/security headers and redacted logs. |
| Frontend proxy | Upload limits were enforced after buffering; malformed API configuration escaped structured handling. Requests are bounded while streaming and invalid configuration returns a structured service-unavailable response. |
| Frontend state/accessibility | Editing empty preferences restored unwanted defaults; expired sessions and stale responses could leave private UI visible. Empty values remain empty, invalid sessions clear the shell, and obsolete requests cannot replace current data. Modals isolate background focus and restore focus; informational notice contrast was corrected. |
| Local operations | Unknown lifecycle flags could silently execute a real action; TCP half-close could discard the upstream response. Unknown flags now fail before mutation, pytest options still pass through, and the gateway preserves the reverse stream after a FIN. |
| Validation safety | Partial backup shutdown could strand services, cleanup could delete an archive it did not create, optimized Python could skip validation assertions, and repeated evidence writes replaced prior results. Recovery now covers shutdown failure, cleanup tracks archive ownership, validation rejects optimization, and evidence uses exclusive creation with unique rerun names. |

## Database changes

Additive migrations are applied locally:

- `005_workflow_content_binding.sql`: approval consumption requires the assessed canonical version.
- `006_provenance_boundaries.sql`: composite provenance/merge constraints and stricter source visibility.
- `007_atomic_policy_checks.sql`: strict JSON permission types and consistent sources → observations → opportunity → workflow lock order.

Previously applied migrations 001–004 and their recorded checksums were not edited. ORM constraints match the additions. No real source is enabled.

## Validation performed

| Check | Observed result | Evidence |
|---|---|---|
| Full Python regression | **241 passed**, zero failures/errors/skips, 23.841 seconds | `.local/evidence/code-review-20261113T231110469398Z-pytest.xml` |
| Browser regression | **20 passed**, zero failures/skips/flaky cases, 15.468 seconds | `.local/evidence/frontend-review/playwright-results.json` |
| Accessibility | Desktop/mobile axe reported zero violations; modal keyboard/focus and serious/critical accessibility checks passed | `.local/evidence/frontend-review/` axe JSON and screenshots |
| Python static checks | Ruff passed; mypy passed for 26 source files | Executed against the frozen code, recorded in the Codex conversation |
| Frontend quality | TypeScript, ESLint and optimized Next.js build passed, including the final contrast correction | Executed by the frontend reviewer, recorded in the Codex conversation |
| Actual updated runtime | API/worker rebuilt and restarted; fixed gateway recreated; migrations rerun as a no-op after application | Owned local stack only |
| Final runtime integrity | All 28 Python/SQL files match the running API and worker; seven migration registry entries verified; 20 application tables force RLS; four restricted roles lack superuser/BYPASSRLS; zero real sources enabled | Read-only final inspection in the Codex conversation |
| Backup/restore regression | **PASS**; 21 table inventories and 888 checkpoint rows matched; restore hold, fresh approval, new-process replay, retention, cleanup and source recovery passed | `.local/evidence/E09-backup-restore-20261113T231501050970Z-405fb910.json` |

Python breakdown: 99 domain, 24 ingestion, 31 workflows, 29 API integration/remediation, 14 API security, 19 provenance-boundary, 4 existing database-boundary, 4 migration-integrity, 16 runtime-environment/lifecycle/gateway and 1 contract case.

The modal accessibility check initially exposed a notice-text contrast failure. The earlier 19/20 browser report is retained in `frontend-review/playwright-results-before-notice-contrast-fix.json`; the final run passed after correction. Early focused review runs reused the default browser JSON report path, so the accepted original 13-case result remains a historical result recorded in the owner acceptance/conversation rather than a claim about the subsequently reused default JSON file. Current review results are isolated under `frontend-review/`; no historical run was reconstructed or fabricated.

Playwright now allocates a unique private evidence directory for each default invocation and shares it with JSON, screenshots and axe outputs. Explicit path overrides remain available. Two discovery-only invocations verified distinct directories and 20 discovered tests, and TypeScript/ESLint passed after this final tooling-only change; the application and final 20-test report were unchanged. See [frontend test instructions](../../apps/web/TESTING.md).

Runtime evidence reruns now preserve existing JSON artifacts and report their unique filename. The original `E09-backup-restore.json` was preserved during this review's updated restore exercise. Backup counts refer to the validation snapshot before the temporary probe is removed; the archive and disposable restore resources are deleted afterward. No retained off-device recovery backup is claimed.

Executed static commands were `.venv/bin/ruff check scripts services packages tests infrastructure/local/gateway.py` and `.venv/bin/mypy scripts services/api/kajh_api services/worker/kajh_worker packages/domain-python/kajh_domain infrastructure/local/gateway.py`. The full `pytest` run used `scripts/manage.py`'s restricted `app_env()`, a fresh review-specific base temporary directory and the JUnit path above. Frontend commands were `npm run typecheck`, `npm run lint`, `npm run build` and Playwright against the existing loopback stack with its explicit evidence directory. The final read-only check confirmed restore resources were removed and both pre-existing unrelated containers still ran. Scanning the 37 changed/new source files and ten built browser JavaScript files found none of the eight actual generated local secret values; this does not claim detection of every unknown secret.

## Remaining boundaries

The original [P1 acceptance evidence](P1-acceptance.md), [KAJH-OWN-002 decision](../decisions/KAJH-OWN-002-P1-ACCEPTANCE.md), historical P0 results and qualified E-09 exclusions remain historical and unchanged. New database negatives and this bounded restore exercise add evidence; they do not silently dispose of every excluded scenario or production requirement.

E-02 source rights remain OPEN and all real adapters disabled. Required hosted CI checks remain unenforced under the documented [GitHub governance](../operations/github-governance.md) gate. P2, paid services, cloud deployment, production readiness and external business actions remain outside this task. No remote push or GitHub configuration change is part of this local review/fix instruction.
