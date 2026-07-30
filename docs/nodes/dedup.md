---
title: "DedupNode"
description: "Raw-level exact dedup по URL+content и normalized content key."
updated: 2026-07-27
---
# DedupNode

`DedupNode` предотвращает повторную обработку одного и того же raw item через
store-backed dedup keys. Это глобальнее snapshot-фильтра: snapshot смотрит
последний run source, а dedup помнит canonical keys через store.

## Вход и выход

**Вход:** `RawItem`.

**Выход:** `RawItem`, если duplicate не найден.

**Drop:** `RawItemDropped` с reason из `DuplicateRejectionReason`, если найден
совпавший URL+content или normalized content key. Перед drop узел пишет
`DuplicateRecord` в store.

## Ключи

URL key строится как `url:<canonical_url>:<content_hash>`. URL сам по себе не
считается immutable дубликатом, потому что содержимое по тому же locator может
измениться.

Content key строится через domain helper `dedup_content_key_for_raw_item`.

Fingerprint key сохраняется для near-match explainability, но current raw
suppression не использует near-match как безопасный drop: изменённые зарплата,
роль или условия часто разделяют boilerplate.

## Параметры и режимы

`near_duplicate_score_cutoff`, `title_score_cutoff` сохранены для near-match
compatibility/analysis.

`defer_commit=True` включает claim flow: узел сначала acquire’ит URL/content
claims, а runtime позже вызывает `commit_claim(item_id)` или
`release_claim(item_id)`.

`claim_ttl_seconds` защищает от зависших claims.

## Состояние

Узел serializes `process()` через lock, держит local cache dedup keys и owner id
для claims. Это предотвращает гонки внутри одного pipeline process.
