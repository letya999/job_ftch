# Plan: Resolve in-progress merge conflicts in feat/phases-23-25

## Context

**Worktree:** `C:/Users/User/a_projects/job_ftch_p2325`  
**Branch:** `feat/phases-23-25`  
**Status:** A `git merge парсеры` was started and is mid-state. There are 12 unresolved conflict files (marked UU in git status). The merge must be COMPLETED — do NOT run `git merge --abort`.

## Conflict files to resolve (all UU)

1. `job_ftch/application/builder.py`
2. `job_ftch/application/contracts.py`
3. `job_ftch/domain/source_spec.py`
4. `job_ftch/infrastructure/sources/browser_utils.py`
5. `job_ftch/infrastructure/sources/career_site_source.py`
6. `job_ftch/infrastructure/sources/monitors/__init__.py`
7. `job_ftch/infrastructure/sources/monitors/dom.py`
8. `job_ftch/infrastructure/sources/monitors/personio.py`
9. `job_ftch/infrastructure/sources/monitors/rss_board.py`
10. `job_ftch/infrastructure/sources/monitors/sitemap.py`
11. `job_ftch/infrastructure/sources/scrapers/__init__.py`
12. `job_ftch/infrastructure/sources/scrapers/embedded.py`

## Resolution strategy (CRITICAL — keep ALL changes from BOTH sides)

These are additive, non-competing changes:
- `HEAD` side (phases-23-25): new tenant/mcp/bot modules, registry additions, ruff formatting
- `парсеры` side: adaptive escalation fixes, new bypass/scraper classes, 20/20 source improvements

**Rule: for every conflict marker, include code from BOTH sides. Never drop changes from either side.**

For `<<<<<<< HEAD ... ======= ... >>>>>>> парсеры` blocks:
- Keep the HEAD version's new symbols AND the парсеры version's new symbols
- Where both sides modified the same function: merge the logic so both sets of changes are present
- Where one side added imports and the other added different imports: keep all imports
- Where both sides modified a list/dict: union of all entries

## Steps

### Step 1: Resolve each conflict file

For each of the 12 UU files:
```bash
cd C:/Users/User/a_projects/job_ftch_p2325
# Read file, identify all <<<<<<< markers, resolve keeping both sides
# Write the resolved version back
git add <file>
```

### Step 2: Verify no conflict markers remain
```bash
grep -r "<<<<<<< HEAD" job_ftch/ tests/ && echo "CONFLICTS REMAIN" || echo "Clean"
```

### Step 3: Complete the merge
```bash
git commit --no-edit
# This uses the auto-generated merge commit message
```

### Step 4: Quality gates (ALL must pass)
```bash
uv run python scripts/check_module_boundaries.py
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest tests/ -q
```

If `ruff format --check` fails:
```bash
uv run ruff format .
git add -u
git commit -m "style: apply ruff format after парсеры merge"
```

If boundary/mypy/pytest fails — fix and commit `fix: ...`.

### Step 5: Confirm
```bash
git log --oneline -8
git status --short
```

Expected: clean working tree (only `?? .runtime/`), log shows merge commit on top of 2e1803b.

## Flow

Use `claude_exec` flow. Do NOT merge to `парсеры` or `main`.
