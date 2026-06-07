# 011 — SourceSpec + AuthProvider separation

**Status**: ACCEPTED
**Date**: 2026-06-07

## Context

Phase 11 introduces multi-source pipelines driven by a declarative `sources.yaml`. The original design baked source config and credentials together into `Settings` — a single flat env-driven model. This breaks as soon as N sources of the same type exist (two Telegram channels can't both have `TELEGRAM_ENTITY`), and it means YAML files would contain secrets, which must not be committed.

## Decision

Split source configuration into two orthogonal concerns:

- `SourceSpec` — a discriminated Pydantic union (`type` field as discriminator). One subclass per source kind (`TelegramChannelSpec`, `CareerSiteSpec`, `HHApiSpec`, etc.). Credentials-free. Safe to commit in YAML.
- `AuthProvider` — a runtime resolver that maps a `auth_source_id` string to actual secret values. Implementations: `EnvAuthProvider` (reads env vars), `FileAuthProvider` (reads `.secrets.yaml`), `VaultAuthProvider` (calls HashiCorp Vault).

Registry factory signature changes from `Callable[[Settings], Source]` to `Callable[[SourceSpec, AuthProvider], Source]`. A `SettingsShim` wrapper in CLI preserves backward compatibility until `Settings`-based factories are fully migrated (RM-067b).

## Consequences

- (+) Multiple instances of the same source type, each with distinct config.
- (+) YAML source lists are safe to commit; secrets live only in env / Vault.
- (+) Third-party plugins receive the same separation contract.
- (-) Factory migration from `Settings` to `(SourceSpec, AuthProvider)` requires updating all existing built-in factories (RM-067b).
