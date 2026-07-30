---
title: "AuthProvider"
description: "**Слой**: `application`"
updated: 2026-07-24
---
# AuthProvider

**Слой**: `application`
**Файл**: `job_ftch/application/contracts.py`

## Что это

`AuthProvider` — boundary для разрешения секретов по `auth_source_id`.

Базовый контракт:

```python
def resolve(self, source_id: str) -> dict[str, str]
```

## Зачем он нужен

`SourceSpec` должен оставаться безопасной конфигурацией, пригодной для yaml и
git. Секреты не должны попадать в repo, runtime source overlay или tenant
config.

`AuthProvider` отделяет:

- декларацию: какой credential set нужен
- разрешение: какие реальные токены и ключи использовать

## Где это используется

- Telegram sources
- API-based sources
- browser / scraping paths при необходимости
- некоторые LLM/runtime integrations

## Реализации

В текущем репозитории есть встроенные реализации через env и file-based path.

Главное правило: провайдер можно заменить, не меняя source contracts.

## Что не делать

- не хранить токены прямо в `SourceSpec`
- не логировать результат `resolve()`
- не тащить `AuthProvider` в nodes, которым секреты не нужны

## Связанные документы

- [SourceSpec](source_spec.md)
- [Source](source.md)
- [Protocols](protocols.md)
