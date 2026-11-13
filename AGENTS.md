# Koras AI Job Hunter

Owner authorization KAJH-P1-AUTH-001 permits local implementation and tests in this repository
and demonstrably owned local Docker resources. KAJH-OWN-001 remains the architecture baseline.

Never activate real sources, make billable model calls, send business messages, deploy,
change global Docker settings, or modify unrelated work. Remote operations require explicit
owner authorization; the owner separately approved publication and branch governance for
`kkora/koras-ai-job-hunter` as a public repository. This does not authorize P2 implementation.
Use synthetic data and generated local credentials only. E-02 remains open. Report E-09 from
actual evidence. Keep credentials and runtime artifacts in ignored `.local/`.

P1 is accepted for bounded local synthetic-only use under KAJH-OWN-002; E-09 is qualified
closed with exclusions retained. Follow `docs/operations/github-governance.md` for Git work.
Ordinary authorized implementation belongs on `develop` or a feature branch from `develop`;
ordinary pull requests target `develop`. Never develop or commit directly on `main`.
Release promotion to `main` uses a separate reviewed pull request from `develop`.

Before every commit, verify the effective author and committer are exactly
`kkora <kora.kanchan@hotmail.com>` and the proposed message has no co-author trailer.
After every commit, inspect its actual author, committer, message and trailers before push.
Do not add any Co-Authored-By or other AI co-author trailer, bypass hooks, rewrite accepted
history, force push, or delete `main`/`develop`. Stop on attribution or governance mismatch.

Python imports use `services/api`, `services/worker`, and `packages/domain-python`.
The frontend is `apps/web`. Root scripts own the isolated Compose lifecycle.
Do not request renewed permission for ordinary local work within the owner authorization.

Verify with the appropriate unit, integration, security, workflow, type, and browser checks.
Do not claim PASS from source existence. Record limitations and actual test commands.
