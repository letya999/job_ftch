---
title: "035 — SecretStr Policy for Sensitive Settings"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 035 — SecretStr Policy for Sensitive Settings

**Status**: ACCEPTED
**Date**: 2026-06-18
**Extends**: [011-source-spec-auth-provider.md](011-source-spec-auth-provider.md)

## Context

Seven `Settings` fields hold credentials as `str | None`:

- `telegram_api_hash`
- `telegram_bot_token`
- `telegram_proxy_password`
- `openai_api_key`
- `qdrant_api_key`
- `langfuse_secret_key`
- `vector_pgvector_password`

Plain `str` means any of the following leaks the value:

- `Settings().model_dump()` writes the real value into JSON, breaking log capture
  and config dump tooling.
- `repr(settings)` or `str(settings)` includes the value.
- `pprint`/`logging` output that calls `__str__` exposes the value.
- Error tracebacks that print `locals()` include the value.

ADR-011 separated `SourceSpec` from `AuthProvider` so secrets live outside
declarative YAML. The runtime contract is correct; the type-level contract is
not. Pydantic ships `SecretStr` exactly for this: `__repr__` / `__str__` show
`SecretStr('**********')`, `get_secret_value()` returns the real value at the
point of use, and `model_dump(exclude=secrets_set())` keeps them out of
serialised output.

## Decision

1. All seven fields above are typed `SecretStr | None` (was `str | None`).
2. Settings adds `model_config = SettingsConfigDict(hide_secrets_in_model_dump=True)`
   if the pydantic-settings version supports it; otherwise a `model_dump_safe()`
   helper on Settings excludes the seven field names explicitly.
3. Code that previously read e.g. `settings.openai_api_key` now calls
   `settings.openai_api_key.get_secret_value()` (or `None` if absent). Grep
   audit:
   - `infrastructure/llm/openai_provider.py`
   - `infrastructure/sources/telegram.py`
   - `infrastructure/observability/otel_setup.py`
   - `infrastructure/stores/postgres.py` (if DSN embeds password, split it)
   - `infrastructure/embeddings/openai_provider.py`
4. `.env.dev.example` and `.env.prod.example` keep the same variable names so
   existing deployments work without env-file edits. Pydantic-settings parses
   `SecretStr` from plain env values.
5. New `tests/test_settings_secrets.py` asserts that:
   - `repr(Settings())` contains no real secret value.
   - `Settings().model_dump(exclude_secrets=True)` (or our wrapper) contains no
     real secret value.
   - `get_secret_value()` returns the real value when called explicitly.

## Consequences

- (+) Defense in depth: secrets are masked in repr/str/JSON even by accident.
- (+) Aligns with Pydantic v2 idiom and ADR-011's auth isolation contract.
- (-) Every consumer call site changes from `field` to `field.get_secret_value()`.
  Acceptable — there are seven fields and ~7 call sites.
- (-) Library users who introspect `Settings().openai_api_key` directly will
  see a `SecretStr` object instead of `str`. Documented in CHANGELOG.
- (-) `model_dump()` now returns `SecretStr` objects; callers that JSON-encode
  directly need `model_dump(mode="json", exclude=...)` or `get_secret_value()`
  expansion. Tests and CLI tooling updated accordingly.
