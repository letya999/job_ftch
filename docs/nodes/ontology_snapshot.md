---
title: "OntologySnapshotNode"
description: "Immutable ontology snapshots в RawItem metadata для replay/provenance."
updated: 2026-07-27
---
# OntologySnapshotNode

`OntologySnapshotNode` прикрепляет к `RawItem` immutable ontology views,
выбранные builder’ом для конкретного pipeline run.

## Вход и выход

**Вход:** `RawItem`.

**Выход:** `RawItem` с `metadata.ontology_snapshots`.

Если snapshots не переданы, узел no-op.

## Параметры

`snapshots: Mapping[str, OntologySnapshot]` — map `profile_id -> snapshot`.

## Логика

Builder создаёт snapshots один раз до обработки items. Узел только копирует в
metadata `version` и `payload_json` каждого snapshot.

## Границы

Узел не читает live ontology store во время processing и не компилирует
онтологию. Его задача — сделать run replayable: downstream decisions можно
связать с конкретной версией ontology payload.
