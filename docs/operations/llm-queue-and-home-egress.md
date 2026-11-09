---
title: "Опциональная LLM-очередь и домашний выход"
description: "Procrastinate/PostgreSQL, общие квоты, закрепление запросов и Android на домашнем Wi-Fi."
updated: 2026-11-10
---
# Опциональная LLM-очередь и домашний выход

Основание: [ADR 106](../adr/106-optional-postgres-llm-dispatch.md).

## Независимые режимы

По умолчанию `execution: inline`: существующий compatible API с токеном
работает без Procrastinate, очереди и телефона. CLIProxy остаётся отдельным
опциональным sidecar. Очередь подключается к именованному provider profile через
`execution: queued`, `quota_scope` и запись `llm_dispatch_policies` этого scope.
Несколько профилей одного аккаунта обязаны иметь общий scope.

Установите extra `job-ftch[llm_queue]` только для queued-клиентов/worker.
`JOB_FTCH_LLM_QUEUE_DSN` — секретная PostgreSQL DSN; при отсутствии используется
`JOB_FTCH_STORE_DSN`. SQLite не является queue backend. Windows-клиентам нужен
SelectorEventLoop для psycopg; worker CLI выбирает его автоматически. Production
worker запускается в Linux-контейнере.

`config/runtime.llm_queue.yaml` — opt-in overlay для существующих CLIProxy
профилей. Для прямого compatible API аналогично настройте его именованный
профиль, сохранив endpoint, модель и credential reference.

## Гарантии и ограничения

- Каждая HTTP-попытка идёт через общий PostgreSQL quota scope; Procrastinate
  сериализует этот scope между worker. FIFO и ограничение backlog одного tenant
  предотвращают неограниченное накопление; взвешенный tenant scheduler не добавлен.
- Запрос, результат и broker job создаются одной psycopg-транзакцией. Существующие
  asyncpg stores не переписываются. Credentials worker получает из environment.
- Запросы имеют tenant/session/body/config fingerprint. Повторный source replay
  использует сохранённый ответ. Незавершённый item не становится processed;
  source cursors остаются незакоммиченными. Долгое ожидание — `waiting_llm`.
- `Retry-After` сохраняется в БД. Запрет авторизации приостанавливает scope.
  Перенос аккаунта/модели автоматически не выполняется.
- Heartbeat recovery помечает потерянную выполняющуюся попытку `uncertain`.
  Её нельзя автоматически отправлять повторно: inference мог уже выполниться.
- Non-streaming `/chat/completions` поддерживается; streaming и другие HTTP
  endpoints отклоняются. Instructor продолжает разбирать ответы в обычном клиенте.
- Вход ограничен 2 MB, результат 4 MB, есть hourly call/token budget и retention.
  Без usage сохраняется консервативная оценка токенов. OCR имеет короткий TTL.
- Лимиты действуют только на потребителей этой очереди. Использование того же
  upstream-аккаунта сторонними клиентами нужно учитывать отдельно.

## Сервер: очередь

### Продолжение и публикация после простоя

[ADR 108](../adr/108-observation-continuations-and-publication-backlog.md)
добавляет в `TenantRunner` checkpoint для CLIProxy logical run. Он содержит
ссылки на immutable observation ledger. После backpressure оставшиеся полученные
записи сохраняются без новых LLM-вызовов; следующая попытка обрабатывает их,
не собирая заново все сайты. Tenant lock и прежняя сессия сохраняются. Отсутствие
сохранённой записи — явная ошибка, не повод молча очистить checkpoint.
Обычная отмена задачи при остановке контейнера оставляет logical run в ожидании;
непредвиденная ошибка по-прежнему требует operator review. Это не снимает
account pause и не разрешает повтор unknown HTTP outcome.

Принятые job IDs записываются в `bot_publish:candidates` до отметки processed.
Публикация читает этот backlog независимо от `fetched_at` и прежнего временного
окна. Для каждого target сохраняются handled IDs и причины: подтверждённая
отправка, ранее опубликованная запись, дубль или terminal rejection. Unknown
delivery остаётся в outbox для reconciliation. Это восстановление доставки
работает и с прямым LLM API, без включения CLIProxy-механизмов.

После рестарта scheduler проверяет backlog даже до следующего планового сбора
и во время паузы LLM. Старое `pending_publish_since` остаётся совместимым
recovery marker. При обновлении сверяйте принятые записи за период простоя с
журналом конкретного target; наличие строк в каталоге не доказывает отправку.

Если backlog пуст, все кандидаты найдены и нет uncertain outbox, старое окно
публикации закрывается как `skipped / no_pending_candidates`. Обработка
сохранённых наблюдений продолжается со следующего прохода scheduler. Отсутствие
кандидата в каталоге или неизвестный исход отправки сохраняют окно для разбора;
они не приравниваются к успешно завершённой доставке.

В отчётах `emitted` — работа текущей попытки. `new_publish_candidates` считает
впервые зарегистрированные job IDs, `replayed_observations` — входы из checkpoint.
Именно новые кандидаты создают новое окно публикации: повторный `emitted` уже
обработанной карточки не должен блокировать продолжение. Checkpoint записывается
только при изменении ссылок; ошибка записи оставляет прежнее состояние и позволяет
повторить сохранение. Это сокращает лишние записи в PostgreSQL при replay.
`waiting_llm` не увеличивает processing errors; deferred и review показаны
отдельно. Счётчики повторных попыток нельзя складывать как уникальные вакансии.
При долгом `waiting_llm` бот повторяет отчёт владельцу не чаще заданного
интервала автозапуска; подтверждённые публикации продолжают идти в канал.
Длинный список ошибок источников в Telegram сокращается с указанием числа
пропущенных строк, чтобы отчёт помещался в сообщение.
Интервал между вызовами может превышать minimum из-за token/call pacing и
cooldown; оптимизация продолжения не отменяет эти ограничения.

Для Т-Банка применяется [ограниченное источником доверие TLS](../sources/scoped-tls-trust.md).

### Дополнительная защита только CLIProxy

ADR 107 расширяет ADR 106. Только профиль с `gateway: cliproxy` и
`execution: queued` получает новые механизмы. `llm_gateway`, имя профиля и
hostname не используются как эвристика. Inline/direct compatible API сохраняет
прежнее поведение; direct и CLIProxy не могут делить один quota scope.

Production overlay задаёт минимум 30 секунд между началами запросов плюс
`jitter_seconds: 10`. Дополнительно учитывается равномерное распределение
часового call/token budget. На плановом старте выбирается однократное смещение
`run_jitter_seconds: 300`, сохраняемое в БД. Это локальные ограничения нагрузки,
не обещание допустимых Google значений. После простоя лимиты не обнуляются.
Основной CLIProxy HTTP timeout — 120 секунд, OCR — 20 секунд. Caller не ждёт
полный inference: queued wait остаётся ограниченным, затем работа продолжается
через deferred. Потеря ответа всё равно требует проверки оператора.

Текстовые CLIProxy bindings получают одну логическую сессию на запуск; отложенная
работа с той же выборкой источников/пользователем продолжает её после рестарта.
Каждая вакансия имеет отдельный request identity и prompt. Общая affinity не
приклеивает историю других вакансий. OCR делит account budget и наследует run
session, если вызван внутри такого запуска; standalone OCR сохраняет item scope.
CLIProxy получает `X-Session-ID`; конфигурационный helper включает affinity с
TTL 168h. Строгая привязка сохраняет один credential на экземпляр CLIProxy.
При замене Google-аккаунта увеличьте `account_generation` и разберите старый run.

Сохраняются структурированные Google RetryInfo, quotaResetDelay/TimeStamp и
CLIProxy model_cooldown. Общий RESOURCE_EXHAUSTED не объявляется автоматически
исчерпанием квоты. Длительное ожидание освобождает worker. Model circuit после
серии 5xx/transport failures делает одну сериализованную recovery attempt;
авторизация, квота и потеря сети учитываются отдельно.

Очередь, operator-required ответ, внешний deadline и переполнение не дают
частичную extraction и не помечают наблюдение processed. Состояния failed,
expired и uncertain сохраняют причину для оператора. Смена scheduling policy
не инвалидирует результаты; semantic/route changes требуют разбора pending.

Уже принятые карточки сохраняют отдельное окно публикации, даже когда другие
наблюдения ждут LLM. Scheduler доставляет их через обычные gates и ledger.
Deferred-наблюдения освобождают свои dedup claims, включая дочерние вакансии
внутри digest; продолжение не должно удерживать claims чужой работы.

`run_summary_enabled: true` ставит максимум один полезный отчёт после завершения
реальной работы. В prompt входят только агрегаты и счётчики; бюджет и session
общие с запуском, output ограничен 512 токенами. Ошибка отчёта не повторяет ingest.
У пустого запуска нет дополнительной генерации. Никаких опечаток, маскирующих
запросов, искусственного прогрева и генераций во время простоя нет. Одна коррекция
JSON допускается только после настоящей ошибки schema validation.

`status` показывает запросы/причины/attempts/tokens, model circuits и последние
run IDs. `report RUN_ID` показывает сохранённую статистику и готовый анализ.
`release-run RUN_ID` разрешён после разбора и отмены outstanding запросов; он не
снимает account pause. `cancel REQUEST_ID` не доказывает отсутствие inference.
Для uncertain сначала проверьте реальный исход, затем осознанно отмените запрос.
Не автоматизируйте release/resume при auth, verification или unknown outcome.
`resume` отклоняется, пока в защищённом scope есть uncertain-запросы.

После явного временного отказа модели (`model_capacity`, `provider_unavailable`,
`rate_limit`) тот же run и request identity продолжаются автоматически после
cooldown. Это относится и к старому operator-held run, если в нём нет других
ошибок или uncertain-запросов. Auth, quota, verification и неизвестный исход
по-прежнему требуют разбора оператором. Продолжение сначала обрабатывает
сохранённые наблюдения, начиная с самых новых; новый сбор источников начнётся
после опустошения checkpoint.

Метрики `job_ftch.cliproxy.*` экспортируются через существующий OpenTelemetry
runtime: attempts, outcomes, waits, circuit transitions, latency и oldest age.
Worker подключается к `public_site` для внутреннего OpenObserve endpoint;
CLIProxy остаётся только в закрытой `llm_private` сети.
Operator-required/circuit transitions пишутся без payload и credentials. Bot
получает reason через существующие deferred/scheduler reports.

Перед переходом с ADR 106 остановите новых producers и дождитесь drain старой
очереди. Неразобранные unknown outcomes не переносите автоматической повторной
отправкой. Запустите `init`, обновите runtime overlay и пересоздайте клиентов/worker.
Сохраните PostgreSQL backup и старый image. Изменение schema аддитивное.

Перед обновлением сохраните текущий image tag и backup БД. Не сбрасывайте state.
Добавьте overlay после базового production Compose, включите профиль `llm-queue`:

```bash
docker compose --env-file job_ftch/adapters/telegram_bot/.env.prod \
  -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml \
  -f job_ftch/adapters/telegram_bot/docker-compose.llm-queue.yml \
  --profile llm-queue build llm-worker
```

Сначала выполните `run --rm llm-worker python -m
job_ftch.infrastructure.llm.dispatch_worker init` с теми же Compose аргументами.
Затем запустите worker и пересоздайте bot/public-api с актуальным runtime image,
включающим extra. Schema init создаёт отсутствующую схему; будущие обновления
Procrastinate требуют его штатных миграций, а не повторного schema init.

Администрирование из worker контейнера:

```bash
python -m job_ftch.infrastructure.llm.dispatch_worker status
python -m job_ftch.infrastructure.llm.dispatch_worker pause google_primary
python -m job_ftch.infrastructure.llm.dispatch_worker resume google_primary
python -m job_ftch.infrastructure.llm.dispatch_worker cancel REQUEST_ID
```

`status` не выводит body, ответы или credentials. Для `uncertain` сначала
разберите реальный исход; `cancel` не означает, что провайдер не выполнил запрос.
Отключение очереди требует drain/cancel pending work до переключения inline.

## Сервер: домашний маршрут

Нужен Docker Compose 2.24.4+ (`!override`). Добавьте третьим файлом
`docker-compose.home-egress.yml` и включите профиль `home-egress`.
Соберите и запустите `llm-home-egress`. Он сохраняет Tailscale identity в volume.
Откройте login URL из его логов на телефоне и авторизуйте устройство
`job-ftch-egress` в своём Tailscale аккаунте.

Настройте CLIProxy без вывода секретов:

```bash
python scripts/configure_llm_home_proxy.py --config deploy/cliproxy/config.yaml
```

Скрипт сохраняет backup с правами 0600 под ignored `.runtime/backups/`,
устанавливает proxy URL и ограничивает внутренние повторы. Пересоздайте CLIProxy:
read-only file mount должен открыть новый файл. Закрепите image digest текущей
проверенной версии. Для строгой привязки используйте один OAuth credential в
каждом изолированном экземпляре CLIProxy; не выводите файлы credentials.

CLIProxy находится только в internal-сети и не имеет прямого интернет-выхода.
HTTP relay принимает соединения только после подтверждения выбранного exit node.
До появления Android readiness возвращает 503; worker откладывает задачи без
upstream attempts и quota reservations. Tailscale/телефон не являются liveness
условием bot/API. Сетевой контейнер до настройки телефона ожидаемо unhealthy.

## Android: всё, что делает владелец

1. Подключите телефон к домашнему Wi-Fi и питанию. Разрешите фоновую работу
   Tailscale и восстановление после перезагрузки; не используйте агрессивное
   ограничение батареи. Если нужен исключительно домашний IP, отключите mobile
   data и автоматическое переключение сети.
2. Установите официальный Tailscale и войдите в тот же аккаунт, где авторизован
   сервер `job-ftch-egress`. Откройте серверный login URL, если он ещё не принят.
3. В веб-консоли Tailscale переименуйте телефон в `android-home`.
4. На телефоне: Exit Node → Run as exit node. В веб-консоли Machines разрешите
   использование телефона как exit node (Edit route settings → Use as exit node).
5. Оставьте телефон на Wi-Fi с погашенным экраном. Сервер обнаружит его, выберет
   маршрут и закрепит Node ID. Очередь возобновится при следующей проверке.
6. Проверьте готовность worker/egress и уменьшение pending backlog. Отключение
   Wi-Fi должно остановить отправку, а восстановление — продолжить её без
   перехода на IP VPS. Проверка длительной стабильности делается на этом телефоне.

Для уже развёрнутого Compose используйте `sh scripts/llm_production.sh status`.
`phone-link` показывает серверную ссылку регистрации Tailscale.
Для замены отключённого аккаунта сначала уберите его credential в защищённый
backup и оставьте scope на паузе. `callback-target` показывает внутренний адрес
контейнера. На Android SSH-клиентом настройте local forwarding
`127.0.0.1:8085 → адрес callback-target` через сервер, запустите
`sh scripts/llm_production.sh login` и откройте показанный OAuth URL в браузере
телефона. После успешного callback выполните `sh scripts/llm_production.sh activate`.
Команды проверяют готовность домашнего маршрута и наличие ровно одного credential.
Пример для Termux: `ssh -L 8085:172.21.0.4:8085 USER@SERVER` (подставьте текущий
адрес из `callback-target`); нужен собственный
SSH-ключ пользователя сервера. Прямой management port в интернет не публикуется.

Домашний IP зависит от роутера/ISP. Android меняет маршрут, а не превращает
запросы в официальный клиент. Отсутствие бана не гарантируется. Документация:
[Tailscale exit nodes](https://tailscale.com/docs/features/exit-nodes?tab=android).

После переустановки телефона его Node ID меняется. Сброс pin-файла в серверном
volume требует явного операторского действия; другой одноимённый узел не
подхватывается автоматически.

## Проверки и откат

Готовность контейнеров и успешный ответ LLM не подтверждают публикацию.
Проверяйте цепочку: terminal ACCEPT → завершённое post-accept enrichment →
`new_publish_candidates` → pending backlog выбранного канала → delivery receipt
с Telegram message ID. Кандидаты и отметки handled хранятся в namespace tenant;
повторный запуск не должен отправлять уже подтверждённую карточку.

При fan-out готовые дочерние результаты публикуются независимо от ожидания LLM
у следующего ребёнка. Родитель остаётся в continuation до завершения всех детей;
processed-key и content hash outbox берутся из исходного RawItem ребёнка.
Если ACCEPT уже сохранён, но кандидат отсутствует, сначала подтвердите завершение
enrichment и принадлежность tenant, затем восстановите кандидат через
`record_publish_candidate`. Доставку выполняет штатный publisher с его gates,
ledger и обработкой uncertain; напрямую в Telegram ничего не отправляйте.

При непустом continuation логический прогон сохраняет identity и продлевает lease,
если маршрут прежний и нет неизвестных, отменённых или неретрабельных результатов.
TTL отдельных HTTP-заданий и квоты аккаунта не сбрасываются. Pending возвращается
сразу: другие объявления проходят дешёвые фильтры и заполняют ограниченную очередь,
а worker соблюдает общие интервалы и cooldown. Источники собираются по обычному
расписанию даже при старом остатке. После успешного ответа пауза пересчитывается
по фактическим токенам с сохранением минимального интервала и jitter; отдельно
установленный cooldown не сокращается. Между сборами идёт replay без refetch.
Старые observations обрабатываются первыми. См. [ADR 109](../adr/109-live-continuation-leases-and-admission.md).

Unit tests идут без PostgreSQL. Для интеграционных тестов задайте
`JOB_FTCH_TEST_LLM_QUEUE_DSN` отдельной disposable БД и запустите
`tests/infrastructure/llm/test_dispatch.py`. Тесты не должны смотреть в production.
Проверьте несколько worker, durable 429, tenant isolation, pending pipeline,
отсутствие прямого выхода, egress 503 до телефона и возобновление после него.

Откат: приостановите scope, дождитесь/отмените pending jobs, верните прежние
runtime overlay и image. Домашний proxy URL восстанавливается из protected
backup; Compose topology откатывается вместе с ним. БД и OAuth auth-dir не
удаляйте. Диагностические bodies не включайте в логи.
