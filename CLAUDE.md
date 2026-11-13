# Koras AI Job Hunter — repository governance

Read and follow `AGENTS.md` and `docs/operations/github-governance.md` before Git work.

- Use `develop` or a feature branch based on it for ordinary authorized work; ordinary PRs target `develop`.
- Never implement or commit directly on `main`. Release promotion is a separate reviewed `develop` → `main` PR.
- Required author and committer: `kkora <kora.kanchan@hotmail.com>`. Verify both before and after each commit.
- Add no co-author, AI-attribution or Claude-Session trailers. Check the actual message before pushing; do not bypass hooks or rewrite existing history to conceal a mismatch.
- Project settings suppress default commit/PR attribution and session links. Inspect higher-precedence settings if behavior differs; a JSON file alone is not proof of effective behavior.
- Keep credentials, evidence, backups, browser sessions and generated artifacts out of Git. Review the exact staged diff and history before an authorized push.
- P1 remains accepted for bounded local synthetic-only use. E-09 is qualified closed with exclusions; E-02 is open and all real sources disabled.
- P2, production use, cloud deployment, paid services and external business actions remain unauthorized. GitHub code publication does not expand application permissions.
