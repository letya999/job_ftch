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

## Hard rules

- `domain/` has zero imports outside `pydantic` and stdlib. No exceptions.
- `SanitizeNode` is always first in any pipeline chain.
- No credentials in code. `.env` only.
- New dependency → update `docs/tech_stack.md` first.
- Architectural choice → write ADR in `docs/adr/` first.
- Commits: `feat`, `fix`, `chore`, `docs`, `refactor` only.

## Extending

| Want to add | Where |
|-------------|-------|
| New data source | `infrastructure/sources/` — implement `Source` Protocol |
| New processing step | `nodes/` — implement `Node` Protocol |
| New output | `sinks/` — implement `Sink` Protocol |
| New storage backend | `infrastructure/stores/` — implement `Store` Protocol |
| New LLM backend | `infrastructure/llm/` — implement `LLMProvider` Protocol |

## Never add

Kafka · Celery · Airflow · LangChain · LangGraph · Scrapy · heavy ORMs
