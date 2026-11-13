# Frontend checks and evidence

Run `npm run typecheck`, `npm run lint`, and `npm run build` from this directory.
Run Playwright with `PLAYWRIGHT_BROWSERS_PATH=../../.local/playwright npx playwright test` against the existing owned local services.

By default, each Playwright invocation creates a private directory under `.local/evidence/` named with its UTC timestamp, process ID, and an atomic random suffix. JSON results, browser artifacts, axe reports, and screenshots share that directory. A `--list` invocation records test discovery only; it does not establish passing test execution.

An explicit `KAJH_E2E_EVIDENCE_DIR` selects the evidence directory, relative to the repository root or absolute. `PLAYWRIGHT_JSON_OUTPUT_FILE` still overrides the JSON report destination. Reusing an explicit destination can overwrite its previous results; use a fresh destination when preserving history.

During the remediation review, early focused runs reused the former default `.local/evidence/playwright-results.json` path. A seven-test focused result replaced the earlier thirteen-test JSON report. That earlier report has not been reconstructed or fabricated. The separately recorded historical acceptance result remains distinct from the later twenty-test regression evidence in `.local/evidence/frontend-review/`.
