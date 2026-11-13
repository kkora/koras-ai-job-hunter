# GitHub repository and branch governance

Date: November 13, 2026. Owner: `kkora` (personal GitHub account), Koras Technologies LLC.

The owner created [kkora/koras-ai-job-hunter](https://github.com/kkora/koras-ai-job-hunter) as **PUBLIC**, superseding the earlier proposed private visibility, and explicitly approved the remaining setup by replying "push changes" to the approval question. This authorizes the audited initial publication and the governance changes described here; it does not authorize application features, P2, paid services or deployment.

## Baseline and branches

The six existing commits are preserved without rewriting: M1 `82d6c6a`, M2 `b5634d1`, M3 `e6f0ead`, M4 `7759cc9`, M5 `9211ae4`, and KAJH-OWN-002 `e1bb4f1`. Both branches were initially published at `e1bb4f1e97999d59310bebce478f24ffaa2c82fc`. The subsequent governance-only commit is on `develop`; `main` retains the accepted baseline until a reviewed release promotion.

| Branch | Purpose and enforced configuration |
|---|---|
| `develop` | GitHub default and ordinary integration branch; direct authorized solo-development pushes permitted; administrator restrictions enabled; force pushes and deletion blocked |
| `main` | Release branch; PR required with one approving review, stale-approval dismissal, latest-push approval by another person and conversation resolution; restrictions apply to administrators; force pushes and deletion blocked |

These are classic branch protections, verified through both branch metadata and the protection configuration, rather than merely a successful update request. Personal-repository push allowlists are not supported and are not claimed. The owner retains the ability to edit repository rules; this is distinct from an administrator merge bypass.

Automatic head-branch deletion and auto-merge are disabled. Merge commits are enabled; squash/rebase merging is disabled to preserve commit identities. Do not force push, rewrite the accepted commits, delete either long-lived branch, or replace the approved `origin` remote.

Ordinary authorized implementation and feature branches start from `develop`; ordinary PRs target `develop`. `main` receives a separate reviewed release-promotion PR from `develop`. These branch instructions do not authorize P2 or new application work.

## Git identity and attribution

Required author and committer: **`kkora <kora.kanchan@hotmail.com>`**. Co-authors: **NONE**. Repository-local `user.name`, `user.email`, `user.useConfigOnly=true` and `push.default=simple` pin the intended defaults without changing global configuration.

Before every commit:

1. Check the current branch, clean/expected working changes and exact staged diff; stop if ordinary work targets `main`.
2. Inspect `git var GIT_AUTHOR_IDENT` and `git var GIT_COMMITTER_IDENT`, including environment/config overrides. Both must use the required name/email.
3. Inspect the proposed message for case-insensitive co-author or AI-attribution trailers; none is permitted.

After every commit, inspect `git show -s --format=fuller HEAD` and `git log -1 --format=%B`. Verify actual author, committer and trailers before pushing. Stop on any mismatch; correcting existing history requires separate authorization. Never bypass unrelated security hooks. No existing hooks were disabled during setup.

Project `.claude/settings.json` sets `attribution.commit` and `attribution.pr` to empty strings and `attribution.sessionUrl` to false. Installed Claude Code 2.1.319 supports these fields. Shared settings do not replace per-commit validation: managed, command-line or project-local settings and explicit instructions can affect behavior. Validate settings with non-model diagnostics and inspect loaded sources when available; do not claim a live Claude-generated commit was tested unless it actually was. No billable model call is authorized for attribution testing.

Setup verification used the official Agent SDK 0.3.319 `resolveSettings()` merge engine in an isolated temporary verifier with network access and file writes denied. The effective local snapshot was exactly `{"commit":"","pr":"","sessionUrl":false}`, with project-settings provenance. Watched settings files stayed byte-identical during resolution, the temporary verifier was removed, and application dependency files were unchanged. This snapshot does not fetch server-managed settings or execute `policyHelper`; no live Claude-generated commit or model call was tested.

The native `claude doctor` diagnostic exited successfully with no settings errors, but unexpectedly updated internal `migrationVersion` metadata in the user-level `.claude.json` and created an automatic backup. Comparing that backup with the resulting JSON showed no other semantic changes, including no credential, preference or attribution changes. No further native diagnostic invocation or reversion was performed.

## Publication and sensitive content

The complete pre-publication object inventory contained six commits, 90 unique blobs and 80 historical paths. Recognized credential patterns and eight actual generated local credential values had no usable matches. All blobs were text; no binary or large artifact was found. Pattern scanning is not proof against every possible unknown secret.

The approved public history intentionally contains the required author email, business/project identifiers and some absolute local-user paths. Credentials, `.local/` evidence/logs/backups, dependencies and browser sessions were not published. New ignore rules cover additional environment, private-key, backup and browser-auth filenames. Ignore rules do not remove historical content and can be overridden, so review staged contents and history before future authorized pushes.

Push only explicit approved branch refs, with tag following disabled. Do not use `--all`, `--mirror`, `--force` or an unreviewed tag push. Recheck repository owner/visibility, remote refs and actual commit attribution after publication.

## CI and unsupported controls

**Required status checks are NOT ENFORCED at bootstrap.** No suitable CI workflow/check exists yet. Do not configure fictional or permanently pending check names. CI activation, execution/spending boundaries and stable job names must be reviewed separately before checks become mandatory. This setup does not create workflows, run hosted jobs, activate paid features or claim CI coverage for `develop`.

When separately authorized CI exists, use the actual successful check names and expected provider, validate PRs targeting `develop` and release promotions to `main`, then require those checks through an explicit governance update. Failure to enforce another approved protection must be reported, not silently treated as full protection.

No organization-only push allowlists or immutable server-side author-email/co-author enforcement are claimed. Local identity checks and instructions are governance controls; Git administrators can change local configuration. Existing commit signing is not retroactively required or repaired by rewriting history.

## Solo-owner maintenance and recovery

PR authors cannot approve their own PRs. Work may continue on `develop` under an authorized task, while `main` release promotion waits for an independent authorized reviewer. Do not invite or contact a reviewer without owner authorization.

If a reviewer is unavailable, stop. With a separate explicit owner recovery instruction, the owner may temporarily relax review requirements, use a recorded PR-based recovery, restore the original one-review/latest-push settings immediately, and re-verify both protection configuration and protected branch metadata. Never automatically bypass protection or force push. The ability to edit rules is the documented recovery path, not permission to exercise it routinely.

If setup or a later push fails, preserve the local history and existing remote contents, inspect actual refs/settings, and report the blocker. Do not automatically delete the repository, change visibility, remove protections or purchase an upgrade. Resume only within the owner's authorized scope.

## Application acceptance boundaries

[KAJH-OWN-002](../decisions/KAJH-OWN-002-P1-ACCEPTANCE.md) still accepts P1 only for bounded local synthetic use. E-09 remains QUALIFIED CLOSED with all exclusions retained. E-02 remains OPEN; all real sources stay disabled. P2, production use, cloud provisioning/deployment, billable inference, paid services and external business actions remain unauthorized.

Historical decision and acceptance records retain the facts and scope at their decision time, including the then-local-only repository. Current public hosting is recorded here and does not retroactively alter P0 results or application acceptance evidence. No runtime test, application change or new acceptance result is implied by repository publication.
