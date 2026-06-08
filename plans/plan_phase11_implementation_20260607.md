# Plan: Implement Phase 11 — Multi-source orchestration (RM-063 to RM-067b)

## Context

This plan implements Phase 11 of the job_ftch roadmap. The project uses a
hexagonal architecture (Ports & Adapters). The current flat layout is
`domain/`, `application/`, `nodes/`, `sinks/`, `infrastructure/`.

**Module boundary rules (HARD — never violate):**
- `domain/` → only pydantic + stdlib
- `application/` → only domain/ + stdlib + pydantic
- `nodes/`, `sinks/` → only domain/ + application/
- `infrastructure/` → everything above + external clients

**Key existing files to be modified or referenced:**
- `domain/models.py` — domain models (SourceKind, RawItem, Job)
- `application/contracts.py` — Source, Stage, Sink, Store, LLMProvider protocols
- `application/registry.py` — open registries for source/sink/store/llm factories
- `config.py` — Settings (pydantic-settings, env-driven)
- `app.py` — composition root + CLI
- `infrastructure/stores/in_memory.py` — InMemoryStore
- `infrastructure/sources/declarative.py` — CareerSiteConfig, DeclarativeCareerSiteParser

## Tasks

### Task 1: RM-063 — domain/source_spec.py (SourceSpec discriminated union)

Create `domain/source_spec.py` with:

```python
"""Source configuration models — what to fetch, never how to authenticate."""

from __future__ import annotations

from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field


class TelegramChannelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["telegram_channel"] = "telegram_channel"
    entity: str = Field(min_length=1)
    limit: int = Field(default=100, gt=0)
    auth_source_id: str | None = None
    source_name: str | None = None  # override display name


class TelegramGroupSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["telegram_group"] = "telegram_group"
    entity: str = Field(min_length=1)
    limit: int = Field(default=100, gt=0)
    auth_source_id: str | None = None
    source_name: str | None = None


class TelegramCommentsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["telegram_comments"] = "telegram_comments"
    entity: str = Field(min_length=1)
    post_limit: int = Field(default=20, gt=0)
    comment_limit_per_post: int = Field(default=50, gt=0)
    auth_source_id: str | None = None
    source_name: str | None = None


class DeclarativeHtmlSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["declarative_html"] = "declarative_html"
    url: str = Field(min_length=1)
    parser_kind: str = "auto"  # "auto", "greenhouse", or any registered parser kind
    limit: int = Field(default=100, gt=0)
    source_name: str | None = None


class CareerSiteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["career_site"] = "career_site"
    url: str = Field(min_length=1)
    limit: int = Field(default=100, gt=0)
    source_name: str | None = None


class LocalFixtureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["local_fixture"] = "local_fixture"
    path: str = Field(min_length=1)
    source_name: str | None = None


SourceSpec = Annotated[
    Union[
        TelegramChannelSpec,
        TelegramGroupSpec,
        TelegramCommentsSpec,
        DeclarativeHtmlSpec,
        CareerSiteSpec,
        LocalFixtureSpec,
    ],
    Field(discriminator="type"),
]
```

No imports outside pydantic + stdlib. File must stay under 80 lines.

---

### Task 2: RM-063 (continued) — application/source_loader.py

Create `application/source_loader.py` with:

```python
"""Load and validate a list of SourceSpec entries from a YAML or JSON file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from domain.source_spec import SourceSpec


def load_sources(path: Path) -> list[SourceSpec]:
    """Read a YAML or JSON file and return validated SourceSpec list."""
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-untyped]
            data: Any = yaml.safe_load(text)
        except ImportError as exc:
            msg = "PyYAML is required to load .yaml source files: pip install pyyaml"
            raise RuntimeError(msg) from exc
    else:
        data = json.loads(text)

    if isinstance(data, dict) and "sources" in data:
        data = data["sources"]

    adapter: TypeAdapter[list[SourceSpec]] = TypeAdapter(list[SourceSpec])
    return adapter.validate_python(data)
```

Note: PyYAML is an optional import. The function raises a clear RuntimeError with install instructions if missing.
Only imports: stdlib (json, pathlib) + pydantic + domain.source_spec. No infra imports.

---

### Task 3: RM-063 (continued) — Export sources.schema.json

Add a script or inline generation to export the JSON Schema of `SourceSpec`.
Place it at `config/sources.schema.json`.

The schema is generated from:
```python
from pydantic import TypeAdapter
from domain.source_spec import SourceSpec
adapter = TypeAdapter(list[SourceSpec])
schema = adapter.json_schema()
```

Create `config/sources.example.yaml` with one entry per source type:
```yaml
sources:
  - type: telegram_channel
    entity: ai_jobs_channel
    limit: 50

  - type: telegram_group
    entity: some_group
    limit: 100

  - type: declarative_html
    url: https://boards.greenhouse.io/companyname
    parser_kind: greenhouse
    limit: 50

  - type: career_site
    url: https://jobs.example.com/
    limit: 100

  - type: local_fixture
    path: fixtures/debug/raw_items.json
```

Both files must be created.

---

### Task 4: RM-063a — Promote CareerSiteConfig in declarative.py

`CareerSiteConfig` is already in `infrastructure/sources/declarative.py`.
The promotion in RM-063a means:
1. Add a `from_spec(spec: DeclarativeHtmlSpec) -> CareerSiteConfig` classmethod to `CareerSiteConfig`
   that resolves parser_kind "auto" by attempting Greenhouse detection, or falls back to generic link parsing.
2. Ensure `DeclarativeCareerSiteParser` can be built from `CareerSiteConfig.from_spec(spec)`.

No other changes needed in the declarative module — it is already the correct default path.

---

### Task 5: RM-064 — infrastructure/sources/composite.py (CompositeSource)

Create `infrastructure/sources/composite.py`:

```python
"""CompositeSource: fan-in over multiple Source adapters."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from application.contracts import Source
    from domain import QuarantinedRawItem, RawItem

logger = logging.getLogger("job_ftch.composite_source")


class CompositeSource:
    """Fan-in source that aggregates items from multiple child sources.

    Sequential mode (concurrency=1): yields items from each child in order.
    Parallel mode (concurrency>1): uses asyncio.TaskGroup + bounded Queue.
    A failing child records an error and does not abort others.
    """

    def __init__(
        self,
        sources: Sequence[Source[RawItem]],
        *,
        concurrency: int = 1,
        queue_capacity: int = 100,
    ) -> None:
        if not sources:
            raise ValueError("CompositeSource requires at least one child source.")
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1.")
        self._sources = list(sources)
        self._concurrency = concurrency
        self._queue_capacity = queue_capacity
        self.failed_sources: int = 0

    async def fetch(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        if self._concurrency == 1:
            async for item in self._fetch_sequential():
                yield item
        else:
            async for item in self._fetch_parallel():
                yield item

    async def _fetch_sequential(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        for source in self._sources:
            try:
                async for item in source.fetch():
                    yield item
            except Exception:
                self.failed_sources += 1
                logger.exception("child_source_failed", extra={"source": repr(source)})

    async def _fetch_parallel(self) -> AsyncIterator[RawItem | QuarantinedRawItem]:
        _SENTINEL = object()
        queue: asyncio.Queue[object] = asyncio.Queue(maxsize=self._queue_capacity)

        async def _drain_one(source: Source[RawItem]) -> None:
            try:
                async for item in source.fetch():
                    await queue.put(item)
            except Exception:
                self.failed_sources += 1
                logger.exception("child_source_failed", extra={"source": repr(source)})

        async def _run_all() -> None:
            async with asyncio.TaskGroup() as tg:
                for source in self._sources:
                    tg.create_task(_drain_one(source))
            await queue.put(_SENTINEL)

        producer = asyncio.create_task(_run_all())
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                yield item  # type: ignore[misc]
        finally:
            if not producer.done():
                producer.cancel()
```

Important notes:
- The `fetch()` method must be an async generator (`async def fetch(self)` + `yield`).
- Module stays within infra layer only; imports only domain + application + stdlib.
- `failed_sources` counter is mutable state on the instance (tests can inspect it).

---

### Task 6: RM-065 — Tests for CompositeSource

Create `tests/test_composite_source.py` with:
1. `test_sequential_ordering`: two fake sources, yields all items from first then second.
2. `test_sequential_child_failure_isolation`: first source raises, second still yields items.
3. `test_sequential_empty_child`: one child yields nothing, other still works.
4. `test_parallel_order_independence`: with concurrency=2, all items arrive eventually.
5. `test_parallel_error_isolation`: one child raises, other still yields, failed_sources == 1.
6. `test_no_sources_raises`: CompositeSource([]) raises ValueError.

Use simple async generators as fake sources.

---

### Task 7: RM-066 — Per-source run state namespacing in InMemoryStore

Modify `infrastructure/stores/in_memory.py`:

Add a helper `_ns(source_kind: str, source_name: str, key: str) -> str` at module level:
```python
def _ns(source_kind: str, source_name: str, key: str) -> str:
    return f"{source_kind}:{source_name}:{key}"
```

Update `get_run_state` and `set_run_state` methods to accept optional `source_kind` and `source_name` keyword arguments. When both are provided, use `_ns(source_kind, source_name, key)` as the actual storage key. When not provided, use `key` directly (backward-compatible).

Store protocol in `application/contracts.py` must also be updated: add optional `source_kind: str | None = None` and `source_name: str | None = None` to `get_run_state` and `set_run_state` signatures.

---

### Task 8: RM-067 — CLI sources-file integration in app.py

Modify `app.py`:

1. Add `--sources-file` argument to `parse_args()`:
```python
parser.add_argument(
    "--sources-file",
    default=None,
    help="Path to YAML or JSON file with a list of source configs.",
)
```

2. In `build_settings()`, propagate `sources_file_path` if provided.

3. Add `sources_file_path: Path | None` to `Settings` in `config.py`.

4. In `run_pipeline()` (or a new `build_source()` variant), detect when:
   - `settings.sources_file_path` is set → load via `load_sources()`, build `CompositeSource`
   - Otherwise, fall back to existing single-source `create_source(settings)` path.

The new build path in `app.py`:
```python
def build_composite_source_from_file(path: Path) -> Source[RawItem]:
    from application.source_loader import load_sources
    from application.registry import create_source_from_spec
    from infrastructure.sources.composite import CompositeSource
    specs = load_sources(path)
    child_sources = [create_source_from_spec(spec) for spec in specs]
    return CompositeSource(child_sources)
```

This requires `create_source_from_spec` to be added to registry (Task 9).

---

### Task 9: RM-067a + RM-067b — Registry v2 + AuthProvider + factory migration

#### 9a: AuthProvider protocol in application/contracts.py

Add to `application/contracts.py`:
```python
@runtime_checkable
class AuthProvider(Protocol):
    def resolve(self, source_id: str) -> dict[str, str]:
        """Resolve credentials for a source by its auth_source_id."""
```

#### 9b: EnvAuthProvider in infrastructure/auth/env_auth.py

Create `infrastructure/auth/__init__.py` (empty).
Create `infrastructure/auth/env_auth.py`:
```python
"""Environment-variable-based credential resolution."""

from __future__ import annotations

import os


class EnvAuthProvider:
    """Reads JOB_FTCH_AUTH_{SOURCE_ID}_{KEY} env vars."""

    def resolve(self, source_id: str) -> dict[str, str]:
        prefix = f"JOB_FTCH_AUTH_{source_id.upper().replace('-', '_')}_"
        return {
            key[len(prefix):].lower(): value
            for key, value in os.environ.items()
            if key.startswith(prefix)
        }
```

#### 9c: New factory type aliases in application/registry.py

Add new type aliases alongside the existing ones:
```python
from domain.source_spec import SourceSpec

SourceSpecFactory = Callable[[SourceSpec, AuthProvider], object]
```

Add parallel registry dict:
```python
_source_spec_factories: dict[str, SourceSpecFactory] = {}
```

Add `@register_source_v2(kind)` decorator and `create_source_from_spec(spec, auth=None)` function.

#### 9d: Keep old Settings-based factories as shims

The existing `@register_source(kind)` and `create_source(settings)` must continue to work unchanged. The new API is additive.

#### 9e: New entry-point groups

Extend `load_extensions()` to also iterate:
- `job_ftch.bypass`
- `job_ftch.job_backends`
- `job_ftch.search_backends`
- `job_ftch.embedding_providers`
- `job_ftch.vector_backends`

Add stub decorator functions to `application/registry.py`:
- `@register_bypass(name)`
- `@register_job_backend(name)`
- `@register_search_backend(name)`
- `@register_embedding_provider(name)`
- `@register_vector_backend(name)`

Each backed by its own dict (`_bypass_factories`, etc.) and `load_extensions()` loading them via entry points.

---

### Task 10: Tests for Phase 11

Add tests in `tests/test_phase11_multisource.py`:

1. `test_source_spec_telegram_channel_valid`: parse a dict with type=telegram_channel.
2. `test_source_spec_discriminated_union`: list with mixed types validates correctly.
3. `test_source_spec_invalid_type_rejected`: unknown type raises ValidationError.
4. `test_load_sources_from_json`: write temp JSON file, load_sources returns correct list.
5. `test_load_sources_from_dict_with_sources_key`: {"sources": [...]} format works.
6. `test_settings_sources_file_path`: Settings accepts sources_file_path.
7. `test_env_auth_provider_resolve`: EnvAuthProvider reads env vars with correct prefix.
8. `test_create_source_from_spec_local_fixture`: creates a LocalFixtureSource from spec.
9. `test_store_namespaced_run_state`: get/set_run_state with source_kind+source_name uses namespaced key.

---

### Task 11: Update config.py to add sources_file_path

Add to `Settings`:
```python
sources_file_path: Path | None = None
```

Add to `strip_optional_strings` or add a new validator if needed — actually Path | None doesn't need stripping.
No breaking change, default is None.

---

### Task 12: Generate and commit config/sources.schema.json + config/sources.example.yaml

Create `config/` directory.
Generate `config/sources.schema.json` from the SourceSpec TypeAdapter.
Create `config/sources.example.yaml` with one entry per type.

Schema generation script (inline, not a separate file unless one exists):
The schema is a static file — generate it once and commit. Do not regenerate at runtime.

To generate:
```python
import json
from pydantic import TypeAdapter
from domain.source_spec import SourceSpec

adapter = TypeAdapter(list[SourceSpec])
schema = adapter.json_schema()
print(json.dumps(schema, indent=2))
```

Run this and write output to `config/sources.schema.json`.

---

## Files to CREATE

| File | Task |
|------|------|
| `domain/source_spec.py` | Task 1 |
| `application/source_loader.py` | Task 2 |
| `config/sources.schema.json` | Task 3 |
| `config/sources.example.yaml` | Task 3 |
| `infrastructure/sources/composite.py` | Task 5 |
| `tests/test_composite_source.py` | Task 6 |
| `tests/test_phase11_multisource.py` | Task 10 |
| `infrastructure/auth/__init__.py` | Task 9b |
| `infrastructure/auth/env_auth.py` | Task 9b |

## Files to MODIFY

| File | Task | What changes |
|------|------|--------------|
| `application/contracts.py` | Task 9a | Add AuthProvider protocol; update Store.get/set_run_state signatures |
| `application/registry.py` | Task 9c-e | Add SourceSpecFactory, _source_spec_factories, register_source_v2, create_source_from_spec, stub decorators for bypass/job_backend/etc., extend load_extensions() |
| `config.py` | Task 11 | Add sources_file_path: Path | None = None |
| `app.py` | Task 8 | Add --sources-file arg, build_composite_source_from_file(), update run_pipeline() to use it |
| `infrastructure/stores/in_memory.py` | Task 7 | Add namespacing helper, update get/set_run_state to accept optional source_kind/source_name |
| `infrastructure/sources/declarative.py` | Task 4 | Add CareerSiteConfig.from_spec() classmethod |

## Verification

After implementation, run:
1. `uv run ruff check .`
2. `uv run ruff format --check .`
3. `uv run mypy .`
4. `uv run pytest tests/ -x`
5. `grep -r "from infrastructure" domain/ application/ nodes/ sinks/` must return empty

## Important constraints

- `domain/source_spec.py` MUST NOT import anything outside pydantic + stdlib.
- `application/source_loader.py` MUST NOT import infrastructure.
- `application/contracts.py` MUST NOT import infrastructure.
- No credentials in SourceSpec models — only `auth_source_id: str | None` as a lookup key.
- All new models must use `ConfigDict(extra="forbid", frozen=True)`.
- Registry v2 is ADDITIVE — existing `@register_source` + `create_source(settings)` continue to work.
- `CompositeSource.fetch()` must be an async generator method.
- Python 3.12+ only. Use `asyncio.TaskGroup` (not `gather`).
- No YAML dependency in `domain/` or `application/`. Optional import only in source_loader.
- Test file names must start with `test_`.
- No print() — use structlog or logging.
- No em dashes in comments or strings. Use hyphens.
- English only in code, comments, commits.
- Do NOT create any .md files unless specified above.
