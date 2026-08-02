# План: единый владелец dedup/terminal lifecycle (волны 1-3)

Дата: 2026-08-02. Ветка-основа: `dev`. Рабочая ветка: `fix/dedup-terminal-lifecycle`.

Источник: аудит 2026-08-02 против внешнего ревью. Волны 4+ вынесены в
`docs/techdebt.md` (TD-035..TD-044) и в этот план не входят.

## Зачем этот план

Одна и та же ответственность (settlement dedup-claim после терминального
решения) сегодня реализована дважды с разными условиями срабатывания. Из-за
этого элемент со статусом `DEFERRED` теряется навсегда: граф коммитит claim,
пайплайн считает, что claim освобождён, повторная обработка отбрасывает
вакансию как дубль.

Волны 1-2 закрывают потерю данных и убирают механизм, который позволил ей
прожить незамеченной (`getattr`-негоциация капабилити). Волна 3 снимает
сериализацию dedup, которая упирает throughput в один лок.

## Приоритет и порядок

| Волна | Тема | Риск для воронки | Требует eval |
| --- | --- | --- | --- |
| 1 | Единый владелец settlement | средний | нет |
| 2 | Запрет тихого no-op, единый `decision_version` | низкий | нет |
| 3 | Dedup throughput и мёртвый код | высокий | да |

Волны выполняются последовательно, каждая отдельным коммитом и отдельным
прогоном гейтов. Волна 3 не начинается, пока волна 1 не зафиксирована: она
меняет тот же файл (`job_ftch/nodes/dedup.py`).

---

# Волна 1. Единый владелец dedup settlement

## Проблема (проверено по коду)

1. `GraphExecutor.run_many` (`job_ftch/application/graph/executor.py:112-122`)
   после успешного выполнения графа вызывает `_settle_deferred_dedup_claims(commit=True)`
   для всех `seen_items`. Статус `DEFERRED` исключением не является, поэтому
   claim коммитится.
2. `DedupNode.commit_claim` (`job_ftch/nodes/dedup.py:86-90`) вызывает
   `_remember_records`, то есть навсегда записывает url/content-ключи в store.
3. `Pipeline._finalize_item_result` (`job_ftch/application/pipeline.py:973-986`)
   для `outcome == "deferred"` выставляет `finalized = False` с комментарием
   "Deferred work must not be marked processed; the claim is released below".
4. `Pipeline._settle_dedup_claims` (`job_ftch/application/pipeline.py:1100-1109`)
   ищет `commit_claim`/`release_claim` через `getattr` по `self._nodes`.
5. В graph-режиме `nodes = [GraphPipelineStage(executor)]`
   (`job_ftch/application/tenant_runner.py:1031`), а `GraphPipelineStage`
   (`job_ftch/application/graph/pipeline_stage.py`) этих методов не имеет.

Итог: `getattr` возвращает `None`, release не происходит, claim уже
закоммичен. Ключ остаётся в store, replay отбрасывается как `DUPLICATE_URL` /
`DUPLICATE_CONTENT`.

Дополнительно проверено:

- `DedupNode` присутствует в production-графе: `config/pipelines/evidence_v2.yaml`
  (`{id: dedup, node: dedup, effect: gate, after: [hard_constraints]}`), который
  через `evidence_v2_compact` -> `..._postaccept` -> `..._prefilter` является
  champion-рецептом (`config/recipes/champion_artifact.json`, `provenance.graph_path`).
- `DedupNode` собирается с `defer_commit=True` (`job_ftch/application/builder.py:1496`),
  то есть claim-путь активен.
- `SnapshotFilterNode` (`job_ftch/nodes/snapshot_filter.py`) **не имеет**
  `commit_claim`/`release_claim`, поэтому строка
  `nodes_to_notify.append(self._snapshot_filter)` в `_settle_dedup_claims`
  сегодня тоже мёртвая ветка.
- `scripts/eval/run_pipeline_eval.py:1346` вызывает `executor.run_many(raw)`
  **напрямую, без `Pipeline`**. Это критично: наивное удаление settlement из
  executor лишит eval-гарнитуру settlement полностью, claims будут висеть до
  истечения TTL (300 с), и цифры eval сдвинутся по причине, не связанной с
  качеством фильтрации.

## Целевой дизайн

Владелец lifecycle ровно один и он вызывается явно. Executor и Pipeline
перестают быть владельцами; executor только **сообщает состав участников**.

Новый модуль `job_ftch/application/dedup_settlement.py`:

- `SettlementOutcome` — enum: `COMMIT`, `RELEASE`.
- `DedupSettlement` — `@runtime_checkable` Protocol с `commit_claim(item_id: str) -> None`
  и `release_claim(item_id: str) -> None`.
- `DedupSettlementParticipants` — Protocol с
  `settlement_participants() -> tuple[DedupSettlement, ...]`; реализуют
  `GraphPipelineStage` и `GraphExecutor`.
- `DedupSettlementCoordinator` — класс с `settle(item_id: str, outcome: SettlementOutcome) -> None`:
  - собирает участников один раз при создании, дедуплицирует по `id()`;
  - идемпотентен: повторный `settle` для того же `item_id` — no-op (нужно для
    fan-out, где родитель и дети проходят через один координатор);
  - при пустом списке участников логирует `dedup_settlement_no_participants`
    на уровне `warning` — это делает будущую регрессию наблюдаемой, а не тихой.

Важное ограничение, которое надо зафиксировать в докстринге (подтверждено
спецификацией typing): `@runtime_checkable` проверяет только **наличие** членов,
не сигнатуры, и `isinstance` по протоколу медленнее `hasattr`. Поэтому:

- `isinstance` вызывается **на сборке пайплайна**, не на каждый item;
- корректность сигнатур обеспечивает mypy, не рантайм;
- рантайм-проверка ловит ровно тот класс ошибки, который дал текущий баг:
  метод отсутствует целиком.

## Изменения по файлам

| Файл | Изменение |
| --- | --- |
| `job_ftch/application/dedup_settlement.py` | новый: протоколы, enum, координатор |
| `job_ftch/application/graph/executor.py` | удалить `_settle_deferred_dedup_claims` и оба вызова в `run_many`; добавить `settlement_participants()` |
| `job_ftch/application/graph/pipeline_stage.py` | реализовать `DedupSettlement` и `DedupSettlementParticipants`, делегируя в executor |
| `job_ftch/application/pipeline.py` | `_settle_dedup_claims` переводится на координатор; убрать ручной обход `self._nodes` и мёртвую ветку со `_snapshot_filter` |
| `scripts/eval/run_pipeline_eval.py` | явный settlement вокруг `run_many`: `RELEASE` при `DEFERRED` и при исключении, `COMMIT` иначе |

## Решение по `SnapshotFilterNode`

Ветка `nodes_to_notify.append(self._snapshot_filter)` сегодня ничего не делает.
Действие: проверить `save_and_purge` (`job_ftch/nodes/snapshot_filter.py:166`) на
предмет того, должен ли snapshot откатываться при `DEFERRED`. По умолчанию
принимается решение **удалить мёртвую ветку** и зафиксировать вывод в описании
коммита. Если выяснится, что откат нужен, это отдельный пункт техдолга, а не
расширение волны 1.

## Тесты

Новый `tests/application/test_dedup_settlement.py`:

1. `test_deferred_releases_claim_in_graph_mode` — item проходит граф, терминал
   отдаёт `DEFERRED`, claim освобождён, `commit_claim` не вызван.
2. `test_accept_and_reject_commit_claim` — оба терминальных исхода коммитят.
3. `test_exception_releases_claim` — исключение внутри графа освобождает.
4. `test_settlement_is_idempotent` — двойной `settle` не приводит к двойному
   `commit`/`release`.
5. `test_missing_participant_is_visible` — stage без `DedupSettlement` даёт
   предупреждение/ошибку, а не тихий no-op.

Регрессия end-to-end (главный тест волны), `tests/application/test_pipeline_graph_contract.py`:

6. `test_deferred_item_is_reprocessable_after_replay` — прогнать item через
   graph-режим до `DEFERRED`, затем прогнать тот же content второй раз и
   убедиться, что он **не** отбрасывается как `DUPLICATE_URL`/`DUPLICATE_CONTENT`.
   Этот тест обязан падать на текущем `dev` и проходить после волны 1.

Обновляемые тесты:

- `tests/application/test_graph_fanout.py:190-221` — сейчас утверждает
  `sorted(claim.committed) == ["a", "b", "parent-1"]` и в комментарии прямо
  описывает текущее поведение как побочный эффект реализации. Переписать под
  новый контракт: executor сам не коммитит, координатор коммитит только
  участников и только по терминальному исходу; родитель, не бравший claim, не
  должен получать broadcast.
- `tests/nodes/test_dedup.py:108-145` — существующие тесты release при
  retryable failure должны продолжать проходить без правок. Если они падают,
  это сигнал, что дизайн сломал sequential-путь.

## Критерии приёмки волны 1

- [ ] `tests/application/test_pipeline_graph_contract.py::test_deferred_item_is_reprocessable_after_replay` падает на `dev` и проходит на ветке (приложить оба прогона в описание PR).
- [ ] `grep -rn "_settle_deferred_dedup_claims" job_ftch scripts` даёт пусто.
- [ ] `grep -rn "commit_claim\|release_claim" job_ftch --include=*.py` показывает вызовы только из `dedup_settlement.py` и определения в `DedupNode`.
- [ ] В `job_ftch/application/graph/executor.py` нет `getattr(node, method_name, None)` для settlement.
- [ ] Eval-гарнитура настраивает settlement явно; `scripts/eval/run_pipeline_eval.py` содержит обработку `DEFERRED` вокруг `run_many`.
- [ ] `just tests-path tests/application` и `just tests-path tests/nodes` зелёные.
- [ ] `just code-verify` и `just architecture-verify` зелёные.
- [ ] Прогон `just eval-filtering` **до** и **после** даёт идентичные P/R (волна 1 не должна двигать метрики; расхождение означает, что settlement всё-таки менял поведение фильтра, и это надо объяснить до мержа).

---

# Волна 2. Запрет тихого no-op и единый `decision_version`

## Проблема

Причина живучести бага из волны 1 — не размер `Store`, а то, что несоответствие
контракту деградирует в `None` вместо ошибки. Текущие места:

- `job_ftch/application/pipeline.py:1116` — `getattr(self._store, "enqueue_outbox", None)`;
- `job_ftch/application/pipeline.py:1161` — `getattr(self._store, "list_pending_outbox", None)`;
- `job_ftch/application/pipeline.py:1211` — `getattr(self._store, "record_observation", None)`;
- `job_ftch/application/pipeline.py:1219-1223` — `getattr(store, "tenant_id", None) or getattr(store, "_tenant_id", None) or "default"`, то есть чтение приватного атрибута чужого объекта с молчаливым фолбэком на `"default"`, что в мультитенантной системе означает запись в чужой ledger;
- `job_ftch/application/pipeline.py:624` и `:767` — `getattr(node, "is_fan_out_stage", False)`.

Отдельно: `_enqueue_outbox` (`job_ftch/application/pipeline.py:1128,1137`) жёстко
подставляет `decision_version="pipeline-v1"`, тогда как observation ledger
(`:1237`) берёт версию из settings. Смена `pipeline_decision_version` меняет
ledger, но не idempotency key outbox, поэтому replay под новой политикой
молча схлопывается по старому ключу и ничего не доставляет.

## Целевой дизайн

Новый модуль `job_ftch/application/capabilities.py` с узкими
`@runtime_checkable` протоколами поверх существующего `Store` (сам `Store` в
этой волне **не режется** — это TD-036):

- `OutboxCapable`: `enqueue_outbox`, `list_pending_outbox`, `mark_outbox_delivered`;
- `ObservationLedgerCapable`: `record_observation`, `get_observation`;
- `TenantScoped`: свойство `tenant_id: str`;
- `FanOutStage` уже существует в `contracts.py:77` — использовать его вместо
  `getattr(node, "is_fan_out_stage", False)`.

Функция `verify_pipeline_capabilities(store, stages, *, requires: frozenset[str]) -> None`
вызывается один раз из `PipelineBuilder.build()` и поднимает
`CapabilityError` со списком того, чего не хватает, с именем конкретного класса.

`decision_version`: `Pipeline.__init__` резолвит версию один раз и хранит в
поле; `_enqueue_outbox` и `_record_observation` используют одно и то же поле.
Хардкод `"pipeline-v1"` удаляется из обоих мест.

`tenant_id`: перестаёт читаться через приватный атрибут. Если store не
`TenantScoped`, `Pipeline` требует `tenant_id` явным аргументом конструктора.
Фолбэк на `"default"` допустим только когда tenant явно не задан во всей
конфигурации, и в этом случае логируется один раз на run, а не молча.

## Новый статический гейт

Добавить в `scripts/run_ci_checks.py architecture` проверку: в
`job_ftch/application/**` и `job_ftch/nodes/**` запрещён паттерн
`getattr(<expr>, "<строковый литерал>", <default>)` для имён, объявленных в
любом Protocol из `contracts.py`/`capabilities.py`. Allowlist — для
диагностики и телеметрии (`getattr(x, "stats", None)` и подобное), с
обязательным комментарием на строке.

Формулировка правила для `AGENTS.md`/ревью: необязательность капабилити
выражается протоколом и проверяется на сборке; `getattr` с литеральным именем
метода в production-пути запрещён.

## Изменения по файлам

| Файл | Изменение |
| --- | --- |
| `job_ftch/application/capabilities.py` | новый: протоколы + `verify_pipeline_capabilities` + `CapabilityError` |
| `job_ftch/application/pipeline.py` | замена всех перечисленных `getattr`; единый `decision_version`; явный `tenant_id` |
| `job_ftch/application/builder.py` | вызов preflight в `build()` |
| `scripts/run_ci_checks.py` | новая architecture-проверка |
| `pyproject.toml` | при необходимости — `[[tool.mypy.overrides]]` для новых модулей |

Замечание по mypy: `strict = true` уже включён, но `disallow_any_explicit` в
`--strict` **не входит** (подтверждено документацией mypy). Включать его здесь
не нужно — это отдельная задача TD-037, иначе волна 2 разрастётся на
`pipeline.py` целиком.

## Тесты

Новый `tests/application/test_capability_preflight.py`:

1. `test_store_without_outbox_fails_build` — сборка пайплайна со store без
   outbox-методов даёт `CapabilityError` с именем класса и списком методов, а
   не тихую деградацию.
2. `test_stage_declaring_fanout_must_implement_protocol`.
3. `test_missing_tenant_scope_is_explicit` — store без `tenant_id` требует
   явного аргумента; молчаливого `"default"` нет.

Новый `tests/application/test_decision_version_contract.py`:

4. `test_outbox_and_ledger_share_decision_version` — при
   `pipeline_decision_version="policy-v2"` и ledger, и `OutboxRecord` несут
   `policy-v2`.
5. `test_version_bump_changes_idempotency_key` — тот же контент под новой
   версией даёт другой `idempotency_key`, то есть replay действительно
   доставляется, а не схлопывается.

Обновляемые: `tests/application/test_pipeline_outbox_targets.py:54` содержит
ожидание `decision_version="pipeline-v1"` — перевести на значение из settings.

## Критерии приёмки волны 2

- [ ] `grep -rn 'getattr(self\._store' job_ftch/application/pipeline.py` даёт пусто.
- [ ] `grep -rn '_tenant_id' job_ftch/application/pipeline.py` даёт пусто.
- [ ] `grep -rn '"pipeline-v1"' job_ftch/application` даёт пусто (значение остаётся только как default в `job_ftch/config.py:162`).
- [ ] Новая architecture-проверка падает на искусственно возвращённом `getattr`-паттерне (проверить негативным прогоном перед мержем).
- [ ] `just code-verify`, `just architecture-verify`, `just tests-all` зелёные.
- [ ] Eval не запускается: волна не трогает решающие узлы. Если P/R сдвинулись, значит что-то из preflight изменило состав узлов — разбирать до мержа.

---

# Волна 3. Dedup throughput и мёртвый код

## Проблема

`DedupNode.process` (`job_ftch/nodes/dedup.py:51-84`) держит `asyncio.Lock`
вокруг `_find_duplicate()` (походы в store) и цикла `acquire_dedup_claim()`.
При `pipeline_item_concurrency` по умолчанию 4 (`job_ftch/config.py:114`) все
item'ы выстраиваются в очередь на этом локе, причём лок удерживается через
`await` к удалённому store.

Сопутствующее:

- `_find_near_duplicate` (`job_ftch/nodes/dedup.py:142-182`) — 40 строк, не
  вызывается ниоткуда;
- `_get_fingerprint_records` (`:232-238`) грузит **всю** таблицу FINGERPRINT в
  память процесса и кэширует навсегда; вызывается только из мёртвого метода;
- `_dedup_cache` (`:47`) растёт неограниченно вместе с базой;
- `acquire_dedup_claim` в SQL-адаптере (`job_ftch/infrastructure/stores/sql_adapter.py:174-180`)
  делает `INSERT .. ON CONFLICT` и отдельный `SELECT` — два роундтрипа и
  окно между ними.

## Целевой дизайн

**Атомарный резерв на стороне store.** Новый метод в порт-протоколе
(`DedupRepository` вводится здесь минимально, полное разрезание `Store` — TD-036):

```
async def compare_and_reserve(
    self, keys: Sequence[str], owner_id: str, *, ttl_seconds: int
) -> DedupReservation
```

Возвращает: какие ключи зарезервированы, какой ключ уже занят (для причины
отказа). Семантика — всё или ничего: при отказе store сам откатывает частично
взятые ключи, вызывающему не нужно компенсировать.

Реализации:

- `in_memory.py` — тривиально, под уже существующей структурой `_dedup_claims`;
- `sql_adapter.py` — один statement с `RETURNING owner_id` вместо
  `INSERT` + `SELECT`. SQLite поддерживает `RETURNING` с 3.35, PostgreSQL
  давно; проверить минимальную версию SQLite в CI-образе перед реализацией и,
  если она ниже, оставить два statement, но внутри одной транзакции;
- фейк в `tests/test_contracts.py:81` — обновить.

**Лок только вокруг локального состояния.** Порядок в `DedupNode.process`:

1. без лока: собрать ключи, спросить store (`compare_and_reserve` или lookup);
2. под локом: обновить `self._claims` и `self._dedup_cache`;
3. без лока: записать duplicate-record и поднять `RawItemDropped`.

`RuntimeError("dedup claim is held; retry item later")` остаётся, но теперь
поднимается по результату атомарной операции, без ручного компенсирующего
цикла release.

**Мёртвый код.** Удалить `_find_near_duplicate` и `_get_fingerprint_records`.

**Решение по FINGERPRINT-записям** (отдельный, обратимый шаг): после удаления
мёртвого читателя записи FINGERPRINT никто не читает. Перед тем как перестать
их писать, выполнить проверку: `grep -rn "FINGERPRINT" job_ftch scripts tests`
и убедиться, что нет внешнего потребителя (в том числе в eval и в bot-хендлерах).
Если потребителей нет — прекратить запись за флагом
`dedup_fingerprint_records_enabled` со значением по умолчанию `False`, чтобы
решение можно было откатить настройкой, а не релизом. Существующие строки в
базе не удалять: чистка — отдельная maintenance-операция (TD-010).

**Bounded-кэш.** `_dedup_cache` -> LRU с потолком (стартовое значение 10_000,
настраиваемое). Метрика попаданий/промахов в `stats`, чтобы потолок можно было
подобрать по данным, а не наугад.

## Изменения по файлам

| Файл | Изменение |
| --- | --- |
| `job_ftch/nodes/dedup.py` | лок только на локальную мутацию; удаление мёртвого кода; LRU |
| `job_ftch/application/contracts.py` | `compare_and_reserve` в порт |
| `job_ftch/infrastructure/stores/in_memory.py` | реализация |
| `job_ftch/infrastructure/stores/sql_adapter.py` | реализация одним statement |
| `job_ftch/infrastructure/stores/sqlite.py`, `postgres.py` | SQL-константы |
| `job_ftch/config.py` | `dedup_cache_max_entries`, `dedup_fingerprint_records_enabled` |
| `tests/infrastructure/stores/test_store_contracts.py` | контрактные тесты нового метода для всех реализаций |
| `tests/test_contracts.py` | обновить фейк |

## Тесты

1. `test_compare_and_reserve_is_all_or_nothing` — при занятом втором ключе
   первый не остаётся зарезервированным.
2. `test_compare_and_reserve_respects_ttl` — по истечении TTL ключ снова
   берётся (аналог существующего `test_store_contracts.py:107-110`).
3. `test_concurrent_dedup_does_not_serialize` — N параллельных item'ов с
   искусственно медленным store: суммарное время ближе к `T`, чем к `N*T`.
   Порог держать мягким (например, `< N*T/2`), чтобы тест не флакал на CI.
4. `test_dedup_cache_is_bounded` — после `max_entries + K` уникальных ключей
   размер кэша не превышает потолок.
5. Все существующие тесты `tests/nodes/test_dedup.py` проходят без правок.

## Бенчмарк

До и после, зафиксировать числа в описании PR:

- 500 item'ов, sqlite-store, `pipeline_item_concurrency` = 1 / 4 / 8;
- фиксировать wall-time и время внутри `DedupNode` (через `stats`);
- ожидание: при concurrency 4 время в dedup падает кратно, при concurrency 1
  не растёт.

Одноразовый скрипт бенчмарка кладётся в `scripts/` только если он станет
частью гейта; иначе — прогон вручную и числа в PR. Полноценный baseline —
TD-039, в эту волну не входит.

## Критерии приёмки волны 3

- [ ] В `DedupNode.process` нет ни одного `await self._store.*` внутри `async with self._lock`.
- [ ] `grep -n "_find_near_duplicate\|_get_fingerprint_records" job_ftch/nodes/dedup.py` даёт пусто.
- [ ] `compare_and_reserve` реализован во всех трёх store и покрыт контрактным тестом, который прогоняется для каждой реализации.
- [ ] `just eval-filtering` **до** и **после**: precision и recall не ниже текущего production-пола. Любое снижение — блокер мержа, а не "шум".
- [ ] Бенчмарк-числа приложены к PR.
- [ ] `just code-verify`, `just architecture-verify`, `just tests-all` зелёные.

---

# Общие правила выполнения

## Гейты после каждой волны

```
just code-verify
just architecture-verify
just tests-all
```

Дополнительно после волны 3:

```
just eval-filtering
```

## Коммиты

Три коммита, по одному на волну, Conventional Commits, английский язык, без
AI-атрибуции и без co-authored-by:

- `fix(dedup): single owner for claim settlement lifecycle`
- `refactor(pipeline): replace getattr capability negotiation with typed ports`
- `perf(dedup): atomic compare-and-reserve and lock only local state`

## Откат

Каждая волна откатывается отдельным `git revert` без затрагивания соседних:
волна 1 не меняет сигнатуры store, волна 2 не меняет узлы, волна 3 не меняет
lifecycle. Если после волны 3 eval просел — ревертится только третий коммит.

## Чего в этих волнах намеренно нет

- объединения `Pipeline` и `GraphExecutor` в один движок (TD-044): делается
  после того, как lifecycle вынесен наружу, иначе движки сливаются вместе с их
  расхождениями;
- разрезания `Store` на репозитории (TD-036): волна 2 вводит capability-протоколы
  поверх текущего `Store`, не трогая его состав;
- `disallow_any_explicit` и типизации graph payload (TD-037);
- batching, resource-класс лимитеров, precompile графа, lazy registry,
  вынесения зависимостей из core (TD-038..TD-042);
- любых изменений в `config/pipelines/*.yaml`: граф в этих волнах не меняется,
  поэтому `graph_hash` обязан остаться прежним. Это отдельный пункт проверки
  перед мержем.
