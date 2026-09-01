# Security Policy

## Reporting a vulnerability

Do not open public issues with secrets, exploit details, private logs, user data, or private infrastructure details.

Use [GitHub's private vulnerability reporting](https://github.com/letya999/job_ftch/security/advisories/new).
If private reporting is unavailable, contact the maintainers privately through the
repository owner. We acknowledge reports within 3 business days and provide an
initial triage within 7 business days.

Supported versions: the default branch and the latest tagged release. Security
fixes are published in the next compatible release when disclosure permits.

## Secret handling

- Never commit `.env` or real credentials.
- Use `.env.example` for placeholders only.
- If a secret is exposed, rotate/revoke it first, then clean history.
- Use `docs/incident-cleanup.md` for the cleanup flow.

## AI agent safety

Agents must follow `AGENTS.md` and use `ai-repo-safety github-guard` for reading GitHub issues, PRs, commits, and branches into AI context.
