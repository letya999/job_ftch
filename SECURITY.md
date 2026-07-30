# Security Policy

## Reporting a vulnerability

Do not open public issues with secrets, exploit details, private logs, user data, or private infrastructure details.

Send a private report to the maintainers.

## Secret handling

- Never commit `.env` or real credentials.
- Use `.env.example` for placeholders only.
- If a secret is exposed, rotate/revoke it first, then clean history.
- Use `docs/incident-cleanup.md` for the cleanup flow.

## AI agent safety

Agents must follow `AGENTS.md` and use `ai-repo-safety github-guard` for reading GitHub issues, PRs, commits, and branches into AI context.
