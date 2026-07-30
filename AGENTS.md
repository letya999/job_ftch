---
title: "job_ftch — agent instructions"
description: "Async pipeline: Telegram channels/groups/comments + career sites → structured JSON vacancies."
updated: 2026-07-24
---
# job_ftch — agent instructions

Async pipeline: Telegram channels/groups/comments + career sites → structured JSON vacancies.

## Read first

| File | Why |
|------|-----|
| [docs/vision.md](docs/vision.md) | What this is, what it is NOT, who it's for. Read before touching anything. |
| [docs/architecture.md](docs/architecture.md) | Current layer rules, evidence-decision pipeline, data flow. Mandatory before writing code. |
| [docs/ontology/compiler.md](docs/ontology/compiler.md) | How labeled shots become compiled ontology, graph terms, and legacy runtime tables. Mandatory before touching ontology/relevance behavior. |
| [docs/recipes/pipeline_recipe.md](docs/recipes/pipeline_recipe.md) | Зафиксированный production-рецепт: тестовый пользователь, тенант, граф, модели, 40 shots, 17 источников, датасеты, метрики и regression gates. |
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
- `application/` has no infrastructure imports except a small set of composition-root/runtime modules (builder, pipeline, tenant runner, source inputs). CI check: `uv run python scripts/check_module_boundaries.py` — the script is the source of truth for the exact exception list, do not duplicate it here.
- `SanitizeNode` is always first in any pipeline chain.
- Type changes happen only via `Stage[In, Out]`. No ad hoc `isinstance` / union routing in core.
- No credentials in code. `.env` only.
- New adapter backends must self-register. No `if/elif` dispatch by adapter kind in core.
- Sinks must not rewrite the whole output file on every `emit`.
- New dependency → update `docs/tech_stack.md` first.
- Architectural choice → write ADR in `docs/adr/` first.
- Runtime decision flow is owned by `EvidenceDecisionNode`; legacy routing nodes stay historical unless a compatibility path explicitly needs them.
- Commits: `feat`, `fix`, `chore`, `docs`, `refactor` only.

## Extending

| Want to add | Where |
|-------------|-------|
| New data source | Prefer declarative `CareerSiteConfig`; otherwise add a single self-registered file with `@register_source`, or a third-party entry-point plugin |
| New site parser | `infrastructure/sources/site_parsers/` — implement `SiteParser` Protocol |
| New processing step | `nodes/` — implement `Stage` / `ProcessingNode` Protocol. Same-type nodes: implement `ProcessingNode[T]`. Type-changing nodes (e.g. extraction/normalization): implement `Stage[In, Out]` directly. |
| New output | `sinks/` — implement `Sink` Protocol and self-register if it is a backend |
| New storage backend | `infrastructure/stores/` — implement `Store` Protocol |
| Trainable prefilter | `fixtures/prefilter/` artifact + `scripts/eval/train_relevance_prefilter.py`. Requires a labelled dataset (2000+ rows, 150+ positives). See [docs/nodes/relevance_prefilter.md](docs/nodes/relevance_prefilter.md). |
| New LLM backend | `infrastructure/llm/` — implement `LLMProvider` Protocol |

## Never add

Kafka · Celery · Airflow · LangChain · LangGraph · Scrapy · heavy ORMs
- Hardcoded domain-specific hosts or parser switches in `config.py` or core composition
# >>> AI REPO SAFETY RULES >>>
## AI Repo Safety Addendum

Run `ai-repo-safety scan --target .` before commits and `ai-repo-safety prepush --target .` before pushes. Use `ai-repo-safety github-guard` for reading GitHub issues, PRs, commits, and branches into AI context.
# <<< AI REPO SAFETY RULES <<<
