# Plan: Phase 11 — Post-review fixes

## Context

After implementing Phase 11 (multi-source orchestration), a code review found the
following issues that must be fixed:

## Issues found

### CRITICAL

**1. `MinimalStore` in `tests/test_contracts.py` does not implement new kwargs**

The `Store` protocol was updated: `get_run_state` and `set_run_state` now accept
optional keyword arguments `source_kind: str | None = None` and
`source_name: str | None = None`. The `MinimalStore` in `test_contracts.py` was
NOT updated. Calling `store.get_run_state("key", source_kind="x")` on `MinimalStore`
raises `TypeError: got an unexpected keyword argument 'source_kind'`.

Runtime `isinstance` still returns `True` (runtime_checkable only checks method exists),
so the test currently passes — but the Store is not actually protocol-compliant.

**Fix**: Update `MinimalStore.get_run_state` and `MinimalStore.set_run_state` to
accept the new optional kwargs and use `_ns` helper or just implement them properly.

New signatures:
```python
async def get_run_state(
    self,
    key: str,
    *,
    source_kind: str | None = None,
    source_name: str | None = None,
) -> str | None:
    ...

async def set_run_state(
    self,
    key: str,
    value: str,
    *,
    source_kind: str | None = None,
    source_name: str | None = None,
) -> None:
    ...
```

Also add a test that calls `get_run_state` with the new kwargs on `MinimalStore`:
In `test_contracts.py`, add test `test_minimal_store_implements_namespaced_run_state`.

### MODERATE

**2. `application/source_loader.py` — `Path` imported only under `TYPE_CHECKING`**

`pathlib.Path` is stdlib. Guarding it under `TYPE_CHECKING` saves nothing, adds confusion,
and makes it unclear that `path.read_text()` is called at runtime on a `Path` object.

**Fix**: Move `from pathlib import Path` to unconditional imports (remove from `TYPE_CHECKING` block).

### MISSING TESTS (6 cases)

All of these belong in new test functions within existing test files.

**3. `CareerSiteConfig.from_spec()` — no tests**

Add to `tests/test_phase11_multisource.py`:
- `test_career_site_config_from_spec_detects_greenhouse`: URL contains `greenhouse.io` → returns config with `kind="greenhouse"` and `href_contains="/jobs/"`
- `test_career_site_config_from_spec_generic_fallback`: URL is `https://jobs.example.com` → returns config with `kind="generic"`
- `test_career_site_config_from_spec_explicit_greenhouse_kind`: `parser_kind="greenhouse"` on non-greenhouse URL → still returns greenhouse config (explicit overrides auto-detect)

**4. `create_source_from_spec` with unknown type raises ValueError**

Add to `tests/test_phase11_multisource.py`:
```python
def test_create_source_from_spec_unknown_type_raises():
    from pydantic import ValidationError
    import pytest
    # Can't create an invalid SourceSpec via Pydantic, but can test the factory path directly
    # by bypassing Pydantic with a mock spec
    from unittest.mock import MagicMock
    from application.registry import create_source_from_spec, load_extensions
    load_extensions()
    fake_spec = MagicMock()
    fake_spec.type = "does_not_exist"
    with pytest.raises(ValueError, match="Unsupported source type"):
        create_source_from_spec(fake_spec)
```

**5. `EnvAuthProvider.resolve` with no matching env vars returns empty dict**

Add to `tests/test_phase11_multisource.py`:
```python
def test_env_auth_provider_resolve_no_matching_vars(monkeypatch):
    # Remove any env vars that might match
    for key in list(os.environ.keys()):
        if key.startswith("JOB_FTCH_AUTH_NONEXISTENT_"):
            monkeypatch.delenv(key)
    provider = EnvAuthProvider()
    creds = provider.resolve("nonexistent-source")
    assert creds == {}
```

**6. `CompositeSource` with `concurrency=0` raises ValueError**

Add to `tests/test_composite_source.py`:
```python
def test_invalid_concurrency_raises():
    s = FakeSource([build_item("1")])
    with pytest.raises(ValueError, match="concurrency must be"):
        CompositeSource([s], concurrency=0)
```

**7. Backward compat: `store.get_run_state("key")` without namespace still works**

Add to `tests/test_store.py`:
```python
@pytest.mark.asyncio
async def test_in_memory_store_run_state_backward_compat_without_namespace():
    store = InMemoryStore()
    await store.set_run_state("cursor", "xyz")
    val = await store.get_run_state("cursor")
    assert val == "xyz"
    # With namespace, should NOT see the value set without namespace
    assert await store.get_run_state("cursor", source_kind="tg", source_name="ch") is None
```

**8. `load_sources` with invalid JSON raises json.JSONDecodeError**

Add to `tests/test_phase11_multisource.py`:
```python
def test_load_sources_invalid_json_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json at all {{{")
    with pytest.raises(json.JSONDecodeError):
        load_sources(path)
```

---

## Files to MODIFY

| File | Change |
|------|--------|
| `tests/test_contracts.py` | Update `MinimalStore.get_run_state` and `set_run_state` to accept new kwargs; add `test_minimal_store_implements_namespaced_run_state` test |
| `application/source_loader.py` | Move `from pathlib import Path` out of `TYPE_CHECKING` block into unconditional imports |
| `tests/test_phase11_multisource.py` | Add 5 new tests: from_spec greenhouse, from_spec generic, from_spec explicit greenhouse, unknown type raises, env_auth_no_vars, load_sources invalid json |
| `tests/test_composite_source.py` | Add 1 new test: `test_invalid_concurrency_raises` |
| `tests/test_store.py` | Add 1 new test: `test_in_memory_store_run_state_backward_compat_without_namespace` |

## Files to CREATE

None.

## Constraints

- Do NOT change any production code other than `application/source_loader.py`.
- Do NOT change `application/contracts.py` — the protocol is already correct.
- Do NOT change `infrastructure/stores/in_memory.py` — already correct.
- Only `MinimalStore` in `test_contracts.py` needs its method signatures updated.
- All new tests must be `@pytest.mark.asyncio` where needed.
- No `print()` in tests — use assertions only.
- No new imports outside what's already in each file.
- After changes: `uv run pytest tests/ -x` must pass all tests.
- After changes: `uv run ruff check .` and `uv run ruff format --check .` must pass.
- No .md files to create.

## Verification

Run these commands after implementation:
1. `uv run pytest tests/ -x --tb=short -q`
2. `uv run ruff check .`
3. `uv run ruff format --check .`
4. `uv run mypy . --ignore-missing-imports`

All must pass with 0 errors.
