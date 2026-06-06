# Phase 3 Completion

Completed on 2026-06-06.

Scope implemented:
- `RM-018` low-cost heuristic triage for early rejection.
- `RM-019` Telegram-specific heuristics split across channel/group/comment behavior.
- `RM-020` career-site non-job/navigation filtering.
- `RM-021` stage conversion reporting with overall counters and `by_source_kind` breakdown.

Follow-up cleanup also completed:
- Removed dead defensive branches from `RawItemRejected.to_quarantined()`.
- Replaced runtime sanitize flag with explicit `SanitizingNode` and `ProcessingNode` contracts.
- Centralized shared stats fields in `application.pipeline.StatsBase`.

Verification completed with repo gates:
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy .`
- `uv run pytest tests/`
- `uv run bandit -r app.py config.py application domain infrastructure nodes sinks -ll`

Caution:
- There is an untracked local `plans/` path in the worktree that is not part of the Phase 3 implementation unless explicitly intended by the user.