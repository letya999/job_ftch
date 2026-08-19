---
title: "MCP operator surface redesign plan"
description: "Plan for turning the MCP adapter from thin TenantRunner wrappers into a Telegram-aligned operator API for sources, examples, bypass, browser sessions, pipeline, jobs, feedback, and prefilter training."
updated: 2026-08-12
---
# MCP operator surface redesign plan

## Goal

Turn `job_ftch.adapters.mcp` into an agent-facing operator API that uses the
same mental model as the Telegram bot:

- `examples`, `resumes`, `vacancies` instead of exposing internal `shots` as
  the primary language;
- `sources`, `pipeline`, `jobs`, `feedback`, `search_sessions` matching the
  Telegram/FastAPI adapter names;
- explicit bypass/browser/runtime setup tools for agent workflows;
- explicit prefilter dataset/training tools, separate from ontology refresh.

The current MCP server technically works, but its public surface is too close to
internal `TenantRunner` methods and has duplicates such as separate
`list_sources` / `list_source_health` and `run_pipeline` / `run_all_pipelines`.
The redesigned surface should be smaller, more operator-oriented, and safer.

## Non-goals

- Do not reintroduce removed MCP legacy aliases without an explicit migration
  request. Current implementation slice is intentionally operator-only.
- Do not bypass legal/config gates, approval gates, captcha authorization, or
  secret handling.
- Do not read or expose `.env*`, tokens, browser profiles, cookies, proxy
  endpoints, or credentials.
- Do not put Patchright/browser dependencies in `domain`, `application`, or
  `nodes`.
- Do not hardcode host-specific parser switches in `config.py` or core
  composition.

## Naming alignment with Telegram adapter

Telegram bot command language:

| Bot concept | Bot commands / routes | MCP public concept |
|---|---|---|
| Examples overview | `/examples` | `examples` |
| Resume examples | `/positive`, `/negative`, `/resumes` | `resume examples` |
| Vacancy examples | `/positive_job`, `/negative_job`, `/vacancies` | `vacancy examples` |
| Sources | `/sources`, `/pipeline/sources/{tenant_id}` | `sources` |
| Pipeline | `/run`, `/pipeline/run`, `/pipeline/status/{tenant_id}` | `pipeline` |
| Browser diagnostics | `/pipeline/browser-capabilities`, `/pipeline/browser-routes` | `browser_capabilities`, `browser_routes` |
| Search sessions | `/pipeline/search-sessions/...` | `search_sessions` |
| Jobs | `/jobs/search` | `jobs` |
| Feedback | `/feedback`, vacancy feedback buttons | `feedback` |
| Profiles | `/profiles/{tenant_id}/{user_id}` | `profiles` |

Internal `shots` remain an implementation detail. Public MCP tools should use
`examples`, while returned diagnostics may mention shot-store sync when useful.

## Target MCP tools

### Tenant / status

```text
list_tenants()
get_tenant_status(tenant_id)
```

`get_tenant_status` should aggregate the current status payload plus source
degradation summary and latest run metadata.

### Sources

```text
get_sources(tenant_id, include_health=true, include_diagnostics=true)
add_source(tenant_id, link, source_type=null, limit=100, added_by=null)
disable_source(tenant_id, source_id)
update_source(tenant_id, source_id, patch)
remove_source(tenant_id, source_id)
```

`get_sources` replaces the current split between `list_sources` and
`list_source_health`. Each source item should include, when requested:

- identity: `source_id`, `source_kind`, `source_name`, locator/public locator;
- origin: config/runtime/bot/api/mcp;
- enabled/disabled;
- health: pending/ok/degraded/failed, failure streak, last error;
- assessment/freshness diagnostics;
- browser requirement hints;
- current or recommended monitor/parser/bypass route.

### Pipeline

```text
run_pipeline(tenant_id=null, user_id=null, scope="tenant|all", source_ids=null, max_items=null)
get_pipeline_status(tenant_id)
list_pipeline_runs(tenant_id=null, limit=20)
get_pipeline_run(run_id, tenant_id=null)
cancel_pipeline_run(run_id)
```

`run_pipeline(scope="all")` replaces a separate `run_all_pipelines` top-level
tool. If `source_ids` is supplied, the implementation may initially reject with
`unsupported` unless source-scoped pipeline execution is implemented in the
application layer.

### Source diagnostics / parser execution

```text
probe_source(tenant_id, source_id, mode="cheap|full", max_items=5)
run_source(tenant_id, source_id, max_items=null, parser=null, bypass=null)
run_source_escalation(tenant_id, source_id, strategy="recommended|all", max_tier=null, max_items=5)
explain_source_failure(tenant_id, source_id, run_id=null)
get_source_artifacts(tenant_id, source_id, run_id=null, artifact_type="summary|html|screenshot|trace|raw")
```

This is the operator path for Getmatch and other career sites. It must be
source-scoped and explainable: which monitor/parser/bypass route was tried,
what succeeded, what failed, and which artifact was captured.

### Bypass capabilities and route recommendations

```text
get_bypass_capabilities()
get_bypass_routes(tenant_id=null, source_id=null, bypass=null)
recommend_bypass_route(tenant_id, source_id, goal="listing|detail|challenge", constraints=null)
probe_bypass_route(tenant_id, source_id, bypass, max_items=3)
```

The public inventory should cover independent axes, not only a linear ladder:

- transport: `noop`, `curl_stealth`, `tls_client`;
- browser/runtime: `stealth_browser`, `patchright_browser`, `nodriver`,
  `camoufox`, `cloak`;
- network/proxy: configured proxy route, residential/static/sticky caveats;
- session/profile: ephemeral, persistent, per-domain, manual profile handoff;
- challenge/CAPTCHA: `browser_wait`, configured captcha providers, manual
  challenge;
- gates: install status, env/config status, safety/legal approval requirement,
  cost/risk.

Each response should include:

- installed/missing;
- whether Patchright/browser runtime is required;
- whether the route can work without a browser runtime;
- setup hint;
- risk/cost;
- approval requirement;
- expected use cases.

### Runtime setup recommendations

```text
recommend_runtime_setup(tenant_id=null, source_id=null, goal="basic|career_sites|protected_sites|browser|captcha|prefilter|full", platform=null)
validate_runtime_setup(goal="browser|bypass|prefilter|mcp", tenant_id=null, source_id=null)
```

`recommend_runtime_setup` answers what to install/configure. It should be a
recommendation tool, not an installer.

Example returned fields:

```json
{
  "goal": "protected_sites",
  "missing_extras": ["browser", "stealth", "nodriver"],
  "missing_binaries": ["patchright chromium"],
  "missing_env": ["JOB_FTCH_CAPTCHA_PROVIDER"],
  "commands": [
    "uv sync --extra browser --extra stealth",
    "uv run patchright install chromium"
  ],
  "manual_steps": [
    "authorize captcha domains",
    "configure browser profile root if persistent sessions are needed"
  ],
  "warnings": []
}
```

Do not expose secrets or current secret values.

### Browser sessions and probes

```text
open_browser_session(tenant_id, source_id=null, url=null, engine="auto|patchright|nodriver|camoufox|cloak", headed=true, bypass=null, profile="ephemeral|persistent|domain", manual_challenge=false)
get_browser_session(session_id)
continue_browser_session(session_id, instruction=null)
capture_browser_artifact(session_id, artifact_type="screenshot|html|text|cookies_summary|trace")
close_browser_session(session_id)
run_browser_probe(tenant_id, source_id=null, url=null, probe="listing|detail|challenge|fingerprint|custom_safe", engine="auto|patchright|nodriver|camoufox|cloak", bypass=null, headed=false, max_items=5)
```

This block must support both Patchright-style and non-browser runtimes. If a
runtime cannot be controlled through Patchright, the tool should still expose a
consistent session/probe contract around the underlying capability.

Listing/detail/challenge probes and ephemeral sessions are implemented
(ADR-081/083). `persistent`/`domain` profiles, `fingerprint`/`custom_safe`,
and `trace` artifacts stay `not_implemented` / `unsupported`.

### Examples / resumes / vacancies

```text
get_examples_summary(tenant_id, user_id, profile_id=null)
list_examples(tenant_id, user_id, profile_id=null, kind="all|resume|vacancy", label=null)
add_example(tenant_id, user_id, kind="resume|vacancy", label="positive|negative", text, profile_id=null, refresh_policy="auto|defer|sync")
remove_example(tenant_id, user_id, kind, label, index=null, text=null, profile_id=null)
clear_examples(tenant_id, user_id, kind="all|resume|vacancy", profile_id=null)
```

Optional convenience aliases may be added only if they do not create too much
surface area:

```text
add_resume_example(...)
add_vacancy_example(...)
list_resumes(...)
list_vacancies(...)
```

Implementation mapping:

```text
resume + positive  -> user:{user_id}@tenant:{tenant_id}:resume:positive
resume + negative  -> user:{user_id}@tenant:{tenant_id}:resume:negative
vacancy + positive -> user:{user_id}@tenant:{tenant_id}:vacancy:positive
vacancy + negative -> user:{user_id}@tenant:{tenant_id}:vacancy:negative
```

After any successful examples/profile/resume write path:

```text
save profile/examples
-> sync shot store
-> compile ontology
-> mark prefilter dirty
```

Ontology compile should not be a normal user-facing step the operator has to
remember. It is part of the write-path, with debounce/async allowed.

### Profiles / resume ingestion

```text
ingest_resume(tenant_id, user_id, resume_text, profile_id=null, activate=true)
list_profiles(tenant_id, user_id)
save_profile(tenant_id, user_id, profile_id, payload, activate=true)
activate_profile(tenant_id, user_id, profile_id)
```

`ingest_resume` and `save_profile` should trigger the same learning refresh path
as `add_example`.

### Learning refresh / ontology

```text
refresh_examples(tenant_id, user_id, profile_id=null, include=["shot_store", "ontology"], mode="async|sync", force=false)
get_examples_refresh_status(tenant_id, user_id, profile_id=null)
compile_examples_ontology(tenant_id, user_id, profile_id=null, dry_run=false, force=false)
```

`compile_examples_ontology` is maintenance/debug. The normal path is automatic
refresh after write operations.

If ontology cannot compile because LLM or ontology store is unavailable, the
write operation should still succeed and return a warning with refresh status.

### Prefilter / TF-IDF LogReg training

```text
get_prefilter_requirements(profile_type=null)
get_prefilter_status(tenant_id, profile_id=null)
prepare_prefilter_dataset(tenant_id, profile_id=null, source="examples|feedback|eval_dataset|mixed", output=null)
validate_prefilter_dataset(dataset_id_or_path)
train_prefilter(tenant_id, profile_id=null, dataset_id_or_path=null, dry_run=true)
evaluate_prefilter(tenant_id, artifact_id, dataset_id_or_path=null)
promote_prefilter(tenant_id, artifact_id, threshold=null, require_gate_pass=true)
rollback_prefilter(tenant_id, artifact_id)
list_prefilter_artifacts(tenant_id, profile_id=null)
```

`get_prefilter_requirements` must answer the exact dataset format and size
requirements. Baseline contract from current docs:

- dataset format: JSONL;
- required fields: `text`, `label`;
- labels: `positive`, `negative`;
- recommended production dataset: at least 2000 rows and 150 positives;
- include enough negatives for a meaningful pre-LLM drop gate;
- run eval before promotion;
- TF-IDF/LogReg promotion is explicit and gated, not automatic after each
  example change.

The prefilter is marked dirty after examples/profile/feedback changes. Training
is explicit because it changes a model artifact and needs metrics, review, and
rollback.

### Jobs

```text
search_jobs(query, tenant_id=null, user_id=null, limit=20)
get_job(job_id, tenant_id=null)
get_job_lineage(job_id, tenant_id=null)
get_latest_jobs(tenant_id, limit=10)
```

### Feedback

```text
list_feedback(tenant_id, audience=null)
add_vacancy_feedback(tenant_id, job_id, user_id, verdict="not_profile", note=null)
promote_feedback_to_example(tenant_id, feedback_id=null, job_id=null, user_id=null, label="negative")
clear_feedback(tenant_id)
set_feedback_audience(tenant_id, audience="off|admin|all")
```

`promote_feedback_to_example` should add a vacancy negative example and trigger
learning refresh.

### Search sessions

```text
create_search_session(tenant_id, user_id=null, profile_id=null, source_scope=null, max_items=null, max_sources=null, result_limit=20)
plan_search_session(session_id)
approve_search_session(session_id, approved_source_ids=null, approved_capability_ids=null, approve_all_sensitive=false, note=null)
run_search_session(session_id, skip_pipeline=false)
get_search_session(session_id)
list_search_session_results(session_id, limit=20)
explain_search_session(session_id, source_id=null, job_id=null)
cancel_search_session(session_id)
```

### Resources

Keep existing resources and add public read-only aliases:

```text
jobs://{tenant_id}/latest
jobs://{tenant_id}/run_summary
config://{tenant_id}
sources://{tenant_id}/registry
examples://{tenant_id}/{user_id}/summary
prefilter://{tenant_id}/status
browser://capabilities
```

## Compatibility plan

### Slice 1 — MCP operator surface over existing services

Current branch decision: expose the operator surface only; legacy aliases are
removed and covered by tests so they are not accidentally reintroduced.

- `get_sources` combines `list_sources` and `list_source_health`;
- `run_pipeline` accepts `scope` and replaces separate `run_all_pipelines`
  behavior;
- expose search-session public names as `plan_search_session`,
  `get_search_session`, `list_search_session_results`;
- add `get_bypass_capabilities` / `get_bypass_routes` over current
  browser capability inventory and route explanation;
- add `recommend_runtime_setup` and `validate_runtime_setup` with read-only
  diagnostics;
- add `get_prefilter_requirements` as a pure read-only static/dynamic contract
  from docs/config.

### Slice 2 — examples and learning refresh

- Add `get_examples_summary`, `list_examples`, `add_example`, `remove_example`,
  `clear_examples`.
- Reuse Telegram handler semantics and `profile_inputs`/`shot_sync` helpers.
- Ensure successful write paths trigger shot-store sync and ontology refresh
  when available.
- Mark prefilter dirty after examples/profile/feedback writes.
- Add tests for resume/vacancy positive/negative mapping.

### Slice 3 — prefilter artifact workflow

- Add status, dataset prepare/validate, train/evaluate/promote/rollback tools.
- Prefer wrapping existing `scripts/eval/train_relevance_prefilter.py` logic
  through an application-level service rather than shelling out from MCP.
- Keep promotion explicit and gated.

### Slice 4 — source-scoped parser/bypass/browser operations

Implemented on this branch:

- Add `probe_source`, `run_source`, `run_source_escalation`,
  `probe_bypass_route`, `run_browser_probe`.
- If live browser session services do not exist, return structured
  `not_implemented` with setup recommendations rather than pretending success.
- Keep Playwright and non-Playwright runtimes behind infrastructure/application
  ports; MCP should not directly import browser clients.
- Ingest path is `TenantRunner.run_tenant(..., source_ids=[source_id])`.
- Parser pins other than the current parser stay `unsupported`.
- Bypass pin and `strategy=all` / `max_tier` are Slice 6 (ADR-082).

### Slice 5 — live listing browser probe

Implemented on this branch (ADR-081):

- `run_browser_probe(probe="listing")` opens one ephemeral page through
  `TenantRunner.probe_browser_listing` → `infrastructure/browser_probe.py`.
- Engine `auto`/`patchright` maps to `patchright_browser`. Other engines use
  the bypass registry when installed.
- Hard deadline, SSRF check, `max_items` cap, no cookies / persistent profile.
- `detail` / `challenge` / `fingerprint` / `custom_safe` and session
  open/continue/close stay `not_implemented`.
- Sources without an http(s) listing URL (`local_fixture`, Telegram) return
  `unsupported` / `listing_url_required`.

### Slice 6 — operator bypass pin and sweep

Implemented on this branch (ADR-082):

- `run_source(bypass=X)` pins that mechanic for one TenantRunner call.
- `run_source()` / `strategy=recommended` keeps the standard adaptive ladder.
- `run_source_escalation(strategy="all")` walks `fallback_order` (cap 6);
  `max_tier` stops after that name.
- Browser routes use the listing probe; HTTP/other routes pin ingest.
- Responses include `parse.stage` / `parse.reason` so an operator can see
  challenge vs empty listing vs fetch vs parser/zero-yield.

### Slice 7 — sessions, detail/challenge, captcha wait, parser pin

Implemented on this branch (ADR-083):

- `run_browser_probe(probe="detail"|"challenge")` opens one ephemeral page.
- `solve=browser_wait|auto|provider` uses `CaptchaSolverBypass`; provider
  stays gated by `captcha_authorized_domains`.
- Ephemeral `open_browser_session` / continue / capture / close (TTL 180s).
- `run_source(parser=X)` pins a registered monitor/scraper for one call.

## Testing plan

For each slice:

```powershell
.venv\Scripts\python.exe -m pytest tests/adapters/test_mcp_server.py -q
.venv\Scripts\python.exe -m pytest tests/packaging/test_library_packaging.py::test_mcp_adapter_registers_tool_and_resource -q
.venv\Scripts\python.exe -m mypy job_ftch/adapters/mcp/server.py job_ftch/cli.py
.venv\Scripts\python.exe scripts/run_ci_checks.py architecture
.venv\Scripts\python.exe scripts/run_ci_checks.py lint
```

Docs changes:

```powershell
.venv\Scripts\python.exe scripts/build_index_docs.py --check
.venv\Scripts\python.exe scripts/lint_docs.py
.venv\Scripts\python.exe scripts/check_docs_generated.py
.venv\Scripts\python.exe -m mkdocs build --strict -f docs_scripts/mkdocs.yml
```

`just` recipes are preferred when the Windows `uv run` file lock is not present.
If `uv run` cannot replace `.venv\Scripts\job_ftch.exe`, use the direct
`.venv\Scripts\python.exe` equivalents above and report the lock.

Before commit:

```powershell
ai-repo-safety scan --target .
```

Before push:

```powershell
ai-repo-safety prepush --target .
```

## Expected deliverable for the current branch

Slices 1–7 are implemented on this branch. Remaining out of scope:

- persistent/domain browser profiles;
- `fingerprint` / `custom_safe` probes and `trace` artifacts;
- headed captcha that waits on a human indefinitely;
- pinning a URL-bound site parser onto a career site that is not that host.

Where a target tool cannot be implemented yet, keep a structured
`not_implemented` response only if the tool is necessary for discoverability,
and document the missing application service.
