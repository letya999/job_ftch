# Fix layer-boundary violations + add boundary CI gate — 2026-06-13

## Context
Audit of feat/integrated found two module-level imports that violate the hexagonal layer rules
in `docs/architecture.md` ("nodes/ only domain+application", "application/ only domain+stdlib+pydantic"):
1. `job_ftch/nodes/job_normalization.py:16` — `from job_ftch.infrastructure.ontology.normalizer import OntologyNormalizer, get_default_normalizer`
2. `job_ftch/application/tenant_runner.py:51` — `from job_ftch.infrastructure.metrics.prometheus import PrometheusExporter`

Also: architecture.md claims a CI boundary check exists, but `.github/workflows/ci.yml` only has a
NAMESPACE check (flat imports outside `job_ftch/**`), not a layer-boundary check. So these regress silently.

Rule for the fix: REMOVE module-level infrastructure imports from `domain/`, `application/`, `nodes/`,
`sinks/`. Lazy imports INSIDE a function/method (indented, not column 0) are the accepted escape hatch
(builder.py already uses them) and are allowed. Preserve all runtime behavior. Keep tests green.

## Task 1 — Port-ify the ontology normalizer used by nodes/job_normalization.py [x]
- Add a `Normalizer` Protocol to `job_ftch/application/contracts.py` (runtime_checkable not required)
  declaring exactly the methods the node uses: `infer_role_family(title: str) -> ...`,
  `infer_seniority(title: str) -> ...`, `normalize_skills(skills) -> ...`. Match the real signatures
  in `infrastructure/ontology/normalizer.py` (read it to copy exact types).
- In `job_ftch/nodes/job_normalization.py`:
  - Remove the module-level infrastructure import (line 16).
  - Type-hint the `normalizer` params against the new `Normalizer` Protocol (import the Protocol from
    `job_ftch.application.contracts`; if only for typing, put under `if TYPE_CHECKING:`).
  - Keep the `normalizer: Normalizer | None = None` constructor default for backward compatibility,
    BUT resolve the default via a LAZY import inside `__init__`:
    `from job_ftch.infrastructure.ontology.normalizer import get_default_normalizer` then call it.
    The lazy import must be indented (inside the method), never at module top level.
  - This applies to BOTH node classes in the file (the title/role node and the skill node).
- Do NOT change node behavior, method bodies, or outputs.

## Task 2 — Port-ify the Prometheus exporter used by application/tenant_runner.py [x]
- Add a `MetricsExporter` Protocol to `job_ftch/application/contracts.py` declaring exactly the methods
  `tenant_runner.py` calls on the exporter instance (read tenant_runner.py around lines 519-560 to find
  them, e.g. start/export/record). 
- In `job_ftch/application/tenant_runner.py`:
  - Remove the module-level infrastructure import (line 51).
  - Type-hint the exporter fields/params (lines ~519, 542) against `MetricsExporter` (TYPE_CHECKING import
    of the Protocol is fine).
  - Move the concrete construction `PrometheusExporter(...)` (around line 546) behind a LAZY import inside
    the method: `from job_ftch.infrastructure.metrics.prometheus import PrometheusExporter`.
  - Preserve the per-port caching logic and all behavior exactly.

## Task 3 — Add a real layer-boundary gate to CI [x]
In `.github/workflows/ci.yml`, add a step (near the existing namespace check at ~line 32):
```yaml
      - name: Enforce layer boundaries (no infra imports in domain/application/nodes/sinks)
        run: |
          if rg -n '^from job_ftch\.infrastructure|^import job_ftch\.infrastructure' \
              job_ftch/domain job_ftch/application job_ftch/nodes job_ftch/sinks; then
            echo "Layer boundary violation: module-level infrastructure import in a pure layer"; exit 1
          fi
```
The `^` anchor matches only column-0 (module-level) imports, so indented lazy imports remain allowed.

## Task 4 — De-stale architecture.md [x]
In `docs/architecture.md` section "Целевое состояние funnel":
- Update the line claiming `RoutingNode` "ещё не выделен" — it now exists (`nodes/routing.py`) and is
  wired in `builder.py`. State that RoutingNode is landed.
- Soften "rollout полного master-plan field set ещё не завершён" — field coverage is now near-complete;
  note remaining gaps are ontology (ESCO) and full benchmark layer, not the field set.
- Do NOT touch other sections.

## Acceptance criteria
1. `rg -n '^from job_ftch\.infrastructure|^import job_ftch\.infrastructure' job_ftch/domain job_ftch/application job_ftch/nodes job_ftch/sinks` returns NOTHING.
2. `python -m pytest -q -m "not e2e and not network and not telegram"` stays green (was 389 passed).
3. `python -c "import job_ftch"` and importing the two touched modules still work.
4. No behavior change: normalization output and tenant metrics behavior identical.
5. The new CI step is present in ci.yml.

## Out of scope (do NOT do here)
- ESCO/ISCO ontology backing (separate effort).
- Benchmark/eval harness expansion (separate effort).
- builder.py lazy imports (already compliant — they are indented).
- Any new dependency.
