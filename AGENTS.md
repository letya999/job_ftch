# job_ftch — agent instructions

Async pipeline: Telegram channels/groups/comments + career sites → structured JSON vacancies.

## Read first

| File | Why |
|------|-----|
| [docs/vision.md](docs/vision.md) | What this is, what it is NOT, who it's for. Read before touching anything. |
| [docs/architecture.md](docs/architecture.md) | 5 Protocols, layer rules, data flow. Mandatory before writing code. |
| [docs/tech_stack.md](docs/tech_stack.md) | Chosen libs and why. Check before adding any dependency. |
| [docs/rules.md](docs/rules.md) | Development process: research → design → implement → verify. |
| [docs/adr/](docs/adr/) | All past architectural decisions. Read before making a new one. |

## How to work

**Think before coding.** Ambiguous request → ask, don't guess. State the forks out loud.
**Simplicity first.** Minimum code that solves the task. Nothing speculative. See [docs/rules.md](docs/rules.md).
**Surgical changes.** Touch only what was asked. Don't refactor uninvited.
**Goal-driven.** Define success criteria before starting. Each action = step + verify.

## Hard rules

- `domain/` has zero imports outside `pydantic` and stdlib. No exceptions.
- `SanitizeNode` is always first in any pipeline chain.
- Type changes happen only via `Stage[In, Out]`. No ad hoc `isinstance` / union routing in core.
- No credentials in code. `.env` only.
- New adapter backends must self-register. No `if/elif` dispatch by adapter kind in core.
- Sinks must not rewrite the whole output file on every `emit`.
- New dependency → update `docs/tech_stack.md` first.
- Architectural choice → write ADR in `docs/adr/` first.
- Commits: `feat`, `fix`, `chore`, `docs`, `refactor` only.

## Extending

| Want to add | Where |
|-------------|-------|
| New data source | Prefer declarative `CareerSiteConfig`; otherwise add a single self-registered file with `@register_source`, or a third-party entry-point plugin |
| New site parser | `infrastructure/sources/site_parsers/` — implement `SiteParser` Protocol |
| New processing step | `nodes/` — implement `Stage` / `ProcessingNode` Protocol. Same-type nodes: implement `ProcessingNode[T]`. Type-changing nodes (e.g. extraction/normalization): implement `Stage[In, Out]` directly. |
| New output | `sinks/` — implement `Sink` Protocol and self-register if it is a backend |
| New storage backend | `infrastructure/stores/` — implement `Store` Protocol |
| New LLM backend | `infrastructure/llm/` — implement `LLMProvider` Protocol |

## Never add

Kafka · Celery · Airflow · LangChain · LangGraph · Scrapy · heavy ORMs
- Hardcoded domain-specific hosts or parser switches in `config.py` or core composition
