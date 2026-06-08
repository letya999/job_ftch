# Mypy Fix Plan: infrastructure/sources/api

## Context

`uv run mypy .` fails with 6 errors after Gemini's Phase 19 implementation.
All errors are in `infrastructure/sources/api/` files.
Do NOT touch anything outside these specific files + `domain/source_spec.py`.
Architecture layer rules must be preserved: domain/ has no infrastructure imports.

## Errors to Fix

```
infrastructure\sources\api\base.py:39: error: "RestAPISourceSpec" has no attribute "auth_source_id"
infrastructure\sources\api\base.py:40: error: "RestAPISourceSpec" has no attribute "auth_source_id"
infrastructure\sources\api\base.py:106: error: Incompatible types in assignment (expression has type "Any | None", variable has type "dict[str, Any]")
infrastructure\sources\api\hh.py:16: error: Value of type variable "FSourceV2" of function "register_source_v2" cannot be "type[HHAPISource]"
infrastructure\sources\api\greenhouse.py:16: error: Value of type variable "FSourceV2" of function "register_source_v2" cannot be "type[GenericRestAPISource]"
infrastructure\sources\api\greenhouse.py:23: error: Value of type variable "FSourceV2" of function "register_source_v2" cannot be "type[GreenhouseAPISource]"
```

## Root Causes

### Error 1-2: auth_source_id missing from RestAPISourceSpec

`infrastructure/sources/api/base.py` references `spec.auth_source_id` at lines 39-40.
`domain/source_spec.py`: `RestAPISourceSpec` has no `auth_source_id` field.
The Telegram specs all have it, but `RestAPISourceSpec` doesn't.

**Fix**: In `domain/source_spec.py`, add `auth_source_id: str | None = None` to `RestAPISourceSpec`
(after the existing `source_name` field, before the class ends).

### Error 3: Type mismatch in _get_by_path

`base.py` line 101-112:
```python
def _get_by_path(self, data: dict[str, Any], path: str) -> Any:
    parts = path.split(".")
    val = data  # mypy infers val as dict[str, Any]
    for part in parts:
        if isinstance(val, dict):
            val = val.get(part)  # returns Any | None, not dict[str, Any] => type error
```

**Fix**: Change `val = data` to `val: Any = data` on that line.
This tells mypy that `val` can hold any type as it traverses the path.

### Errors 4-6: register_source_v2 TypeVar constraint mismatch

`SourceSpecFactory = Callable[[Any, "AuthProvider"], object]` in `registry.py` — a 2-argument callable.
`FSourceV2 = TypeVar("FSourceV2", bound=SourceSpecFactory)`.

The `OfficialAPISource.__init__(self, spec, auth, store, source_kind)` has 4 params (+ self),
so it is NOT a valid `SourceSpecFactory`. Decorating the class directly fails the TypeVar bound.

**Fix strategy**: Make `store` optional in `OfficialAPISource.__init__`,
then replace the class-level `@register_source_v2` decorators with module-level factory functions.

**Changes required**:

**A. `infrastructure/sources/api/base.py`**:
- Change `OfficialAPISource.__init__` signature to make `store` optional:
  ```python
  def __init__(
      self,
      spec: RestAPISourceSpec,
      auth: AuthProvider,
      store: Store | None = None,
      source_kind: SourceKind = SourceKind.CAREER_SITE,
  ) -> None:
  ```
- `self._store = store`  (already optional semantically — it's only used for incremental cursor)
- Fix `val: Any = data` in `_get_by_path`

**B. `infrastructure/sources/api/greenhouse.py`**:
- Remove `@register_source_v2("rest_api")` decorator from `GenericRestAPISource` class
- Remove `@register_source_v2("greenhouse")` decorator from `GreenhouseAPISource` class
- Add module-level factory functions AFTER the class definitions:
  ```python
  @register_source_v2("rest_api")
  def _create_generic_rest_api_source(spec: Any, auth: AuthProvider) -> GenericRestAPISource:
      return GenericRestAPISource(spec, auth)


  @register_source_v2("greenhouse")
  def _create_greenhouse_source(spec: Any, auth: AuthProvider) -> GreenhouseAPISource:
      return GreenhouseAPISource(spec, auth)
  ```
  Import `Any` from `typing` if not already imported.

**C. `infrastructure/sources/api/hh.py`**:
- Remove `@register_source_v2("hh")` decorator from `HHAPISource` class
- Add module-level factory function AFTER the class definition:
  ```python
  @register_source_v2("hh")
  def _create_hh_source(spec: Any, auth: AuthProvider) -> HHAPISource:
      return HHAPISource(spec, auth)
  ```
  Ensure `Any` is imported from `typing`.
  Ensure `AuthProvider` is imported — it is already in `TYPE_CHECKING` block, so:
  - Either move `AuthProvider` import out of `TYPE_CHECKING` block, OR
  - Use `from __future__ import annotations` (already present) and keep it in `TYPE_CHECKING`
    (Python resolves it at runtime from `typing.get_type_hints` — fine for runtime factory use)

## Invariants to Preserve

1. `OfficialAPISource.__init__` store=None does NOT break existing code that passes store explicitly
   (greenhouse.py and hh.py call `super().__init__(spec, auth, store, ...)` — update these to
   match the new default: `super().__init__(spec, auth)` since factory functions don't receive store)
2. domain/ layer must not import from infrastructure/ — `auth_source_id` is a plain `str | None` field,
   adding it to `RestAPISourceSpec` in domain/ is safe
3. No new external library imports
4. Keep `from __future__ import annotations` in all modified files

## Validation Steps (run after all changes)

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest tests/ -x -q 2>&1 | Select-Object -First 60
```

All 4 must pass before done. If any test fails, investigate before giving up.
