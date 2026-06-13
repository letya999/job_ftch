# Plan: Merge branch `парсеры` into `feat/phases-23-25`

## Context

**Worktree:** `C:/Users/User/a_projects/job_ftch_p2325` (branch `feat/phases-23-25`)  
**Goal:** Make `feat/phases-23-25` the single most-forward branch containing everything from both `парсеры` (20 live sources, adaptive escalation) and the phases 23-25 implementation (multi-tenant, MCP server, Telegram bot).

A prior `git merge парсеры --no-commit --no-ff` attempt was already made and aborted. The merge tree was partially built; `git merge --abort` cleaned it. The worktree is now clean (only `?? .runtime/` untracked — ignore it).

## Branch divergence

- Branch point: commit `907e5fb` (feat(config): add part 2 of RU/KZ job boards configuration)
- `парсеры` tip: `e8b4a31` (fix: achieve 20/20 live sources with adaptive escalation and config corrections) — **1 commit ahead of branch point**
- `feat/phases-23-25` tip: `2e1803b` — **5 commits ahead of branch point**

## Step 1 — Merge

```bash
cd C:/Users/User/a_projects/job_ftch_p2325
git merge парсеры --no-ff -m "merge(парсеры): integrate 20/20 live sources and adaptive escalation into phases-23-25"
```

If conflicts arise (expected: 12 files), resolve ALL of them by keeping BOTH sides. Strategy per file:

### Conflict resolution strategy (CRITICAL — keep ALL changes)

**General rule:** these are additive changes. The парсеры side adds scraper/bypass improvements; the phases-23-25 side adds new modules (tenant, mcp, telegram). There is NO logical reason to drop code from either side. For every conflict:
- Identify what the `<<<<<<< HEAD` side has (phases-23-25 additions)
- Identify what the `>>>>>>> парсеры` side has (adaptive escalation fixes)
- Produce a result that includes BOTH, in a clean, non-duplicating way

### Known conflict files and their expected resolution:

1. **`job_ftch/application/builder.py`**  
   `парсеры` side: added `BuildProfile` rename, new schedule/source params, adaptive bypass fields.  
   `phases-23-25` side: added `TenantConfig`, `TenantRunner` imports, clone() method, multi-tenant support.  
   Resolution: Keep both — include the new fields from парсеры AND the tenant additions from phases-23-25.

2. **`job_ftch/application/contracts.py`**  
   Both sides may have added new protocol methods or type aliases.  
   Resolution: Include all new signatures from both sides. No deletions.

3. **`job_ftch/domain/source_spec.py`**  
   Resolution: Accept all field additions from both sides.

4. **`job_ftch/infrastructure/sources/browser_utils.py`**  
   Resolution: Keep all utility function changes from both sides.

5. **`job_ftch/infrastructure/sources/career_site_source.py`**  
   Resolution: Keep both sets of changes.

6. **`job_ftch/infrastructure/sources/monitors/__init__.py`**  
   Resolution: Keep all registered monitor names from both sides (union of lists).

7. **`job_ftch/infrastructure/sources/monitors/dom.py`**  
   Resolution: Keep both sets of changes.

8. **`job_ftch/infrastructure/sources/monitors/personio.py`**  
   Resolution: Keep both sets of changes.

9. **`job_ftch/infrastructure/sources/monitors/rss_board.py`**  
   Resolution: Keep both sets of changes.

10. **`job_ftch/infrastructure/sources/monitors/sitemap.py`**  
    Resolution: Keep both sets of changes.

11. **`job_ftch/infrastructure/sources/scrapers/__init__.py`**  
    Resolution: Keep all registered scraper names from both sides (union of lists).

12. **`job_ftch/infrastructure/sources/scrapers/embedded.py`**  
    Resolution: Keep both sets of changes.

## Step 2 — Quality gates (ALL must pass)

Run from `C:/Users/User/a_projects/job_ftch_p2325`:

```bash
uv run python scripts/check_module_boundaries.py
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest tests/ -q
```

If `ruff format --check` fails (unformatted files), run:
```bash
uv run ruff format .
git add -u
git commit -m "style: apply ruff format after парсеры merge"
```

If `mypy` or `pytest` fails — fix the error, commit as `fix: ...`, do NOT abort the merge commit.

## Step 3 — Final commit

After all gates pass:
```bash
git log --oneline -8
git status --short
```

The working tree should be clean (except `?? .runtime/`). The branch `feat/phases-23-25` should now contain everything from `парсеры` plus all phases 23-25 code.

## Step 4 — Report

Output a summary:
- Which files had conflicts and how they were resolved
- Final gate results (boundary/ruff/mypy/pytest)
- Final `git log --oneline -6` showing all commits
- Any deviations from the plan

## Flow

Use `claude_exec` flow (gemini and codex quota exhausted). Do NOT merge to `парсеры` or `main`.
