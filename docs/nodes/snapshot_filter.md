---
title: "SnapshotFilterNode"
description: "Run-based snapshot фильтр: не перерабатывает неизменившиеся элементы источника."
updated: 2026-07-27
---
# SnapshotFilterNode

`SnapshotFilterNode` сравнивает текущий `RawItem` со snapshot последнего
завершённого run того же source и дропает unchanged items. Это ускоритель
повторных запусков, а не дедупликация вакансий.

## Вход и выход

**Вход:** `RawItem` с `stable_id`, source identity, URL и текстом.

**Выход:** тот же `RawItem`, если item новый или изменился.

**Drop:** `RawItemDropped(reason=ALREADY_SEEN)`, если в последнем snapshot для
этого source тот же `stable_id` имеет тот же content hash.

## Параметры и состояние

`store` читает и пишет source snapshots.

`tenant_id`, `run_id` задают scope. `run_id` обязан быть непустым; runtime может
позже синхронизировать его через `set_run_id`.

`ttl_days` управляет purge новых snapshots.

`fail_open` решает, пропускать ли item при ошибке чтения snapshot или падать.

Узел лениво bind’ит source при первом item, держит hash map последнего run и
rows текущего run по каждому source.

## Жизненный цикл

`process()` вызывается на каждый raw item. Даже unchanged item добавляется в
current snapshot rows перед drop: иначе run, состоящий почти полностью из
unchanged postings, сохранил бы неполный snapshot и сломал следующий запуск.

После прохода runtime вызывает `save_and_purge()`, чтобы записать snapshot
текущего run и применить TTL purge.

## Границы

Это не `DedupNode`. Snapshot отвечает только на вопрос “изменился ли этот
stable_id относительно прошлого run этого source”. Cross-source duplicates,
content keys и claims принадлежат `DedupNode`.
