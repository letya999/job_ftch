---
title: "CompanyCanonicalizer"
description: "Legacy/company canonicalization по alias YAML и fuzzy matching."
updated: 2026-07-27
---
# CompanyCanonicalizer

`CompanyCanonicalizer` нормализует `company_canonical` через alias map и fuzzy
matching. Это отдельный company node для сценариев, где нужен explicit alias
словарь.

## Вход и выход

**Вход:** `Job` с полем `company`.

**Выход:** `Job` с обновлённым `company_canonical`, если найден canonical alias.

Если `company` пустой или alias не найден, узел возвращает item без изменений.

## Параметры

`aliases_path` — YAML вида `canonical_name: [aliases...]`.

`fuzzy_threshold = 85` — порог `rapidfuzz.fuzz.token_set_ratio`.

## Логика

При инициализации узел загружает canonical names и aliases, нормализуя ключи
через domain helper `normalize_company_name`.

При обработке сначала ищет exact normalized alias, затем fuzzy fallback по всем
alias keys.

## Границы

Узел не должен подменять source_name на company: агрегаторы и job boards часто
не являются работодателем. Это только alias canonicalization для уже
извлечённого company value.
