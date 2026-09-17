---
title: "Production recovery — audit context"
description: "Передаваемые факты сессии, ограничения доказательств и regression cases."
updated: 2026-09-12
---
# Production recovery — audit context

## Purpose

Контекст аудита этой сессии для новых tasks; live состояние перепроверять. Это исходные наблюдения, не текущая release certification. Не публиковать PII/secrets.

## Scope and source counts

Tenant ai_jobs. Предыдущий registry: 123 записи, 111 enabled, 12 disabled; aliases/keyword expansions нельзя суммировать как отдельные boards. Health reliable/rich не доказывает full detail или field correctness. За доступные 10 дней source fetches повторяются между runs и не равны unique вакансии. Основные ACCEPT в срезе QuickOffer/HireHi, остальные boards существенно менее представлены; ноль ACCEPT не доказывает parser failure.

## Ingest defects and fixtures

GetMatch 36153: raw title 33 chars; detail содержит salary 300000 RUB/month net, Moscow hybrid и полноценное описание. 36124: raw title ~50 chars, API company={name:Сбер}, detail содержит company и Moscow office. Generic career source пропускает detail для любого non-null rich_payload; API sniffer допускает title+ID; extraction metadata helper не поддерживает single nested company dict.
GeekJob detail IDs 6a7f21ae2c644e385100571f и 6a98133e387c9acec800c132: search records 35/53 chars, details ~1476/1732 chars; текущий search JSON путь не делает detail.
HireHi custom JSON-LD parse сохраняет title/description, теряет organisation/location/workmode; иногда detail failure превращается в title fallback. Source JSON-LD может содержать Amsterdam/RU, salary placeholders и market estimates; не доверять формально structured data.
Hirify API в server direct probe 429, residential probe 200 и полноценные jobs; вакансии 1039545 и 1045281 отсутствовали в observations этого tenant. Это availability gap, не автоматически profile-match доказательство.
Habr limited parser probe получал full details, production source имел deadline/detail failures. staff.am reliable/rich при коротких UnknownJob; RemoteRocketship выдавал listing; ITJobsUZ Next Flight refs; Kaspi/Jumys listing contamination.

## Field defects

Compensation regex принимает первое monetary amount: Dwelly HireHi 78825 $470 million GMV → USD470; Delivery Heroes 83593 €1000 education budget → EUR1000; Picnic 87358 relocation €2000 → EUR2000. Пример: Бюджет на обучение €1000. Зарплата €8000 в месяц. Согласовать parser unit/period и net/gross.
Geo dictionary не проверяет city-country consistency; Astana/Russia и Amsterdam/RU воспроизводимы. Нормализация может сохранять stale city/country; group merge меняет location без остальных полей; renderer предпочитает city/country. HireHi 85493/85500 имели wrong Amsterdam Russia; firstseen Sep1, вне последнего 10-day окна. Exact Астана Россия в текущих 931 jobs и видимых 461 сообщениях не обнаружено; удалённая история неизвестна. Russian/RUB не означает Russia.
Postaccept FullExtraction только ACCEPT; completed не field quality. Early acquisition titles могут теряться до full enrichment. Postaccept не меняет terminal lane.

## Delivery and duplicates

TelegramCardSender.send возвращает None при rejected render без API вызова; channel publisher после await считает sent=true и пишет ledger. FakeBot replay подтвердил sent=1 при actual calls=0. Ledger receipt отсутствует; jf_outbox был пуст при отдельном KV publishing ledger. Это опровергает предположение ledger=actual delivered.
Visible channel @ai_engineer_jobs last10d: 461 текста, exact fulltext duplicates 0. Подтверждённая duplicate pair сообщений 1839/1840: HH vacancy137177581 через hh.ru и региональный hh.kz; company CharmCleoCosmetic. High-confidence cross-source OpenBrain сообщения1710/1882: HireSeeker10008871 и HH137053258, same tech lead role/Almaty/hybrid/KZT compensation. AstanaHub message1779 кандидат, не считать confirmed.
Full ledger: 617 IDs → groups; 16 stable native-ID duplicate clusters, 9 clusters firstseen last10d, что не является physical publication date. 315/634 blocking keys отличались от current canonical; 22 canonical fingerprints не принадлежали своей группе — integrity indicators, не столько же доказанных дублей. Toloka query jid сохранять.
10d unique OpenObserve events: scheduler_channel_published sum sent501; publication_card_rejected36 unique. Raw log counts inflated duplicates. Не выводить число удалённых сообщений из разницы.

## Access probes and unknowns

Residential proxy работал server RU/US и local RU в реальных probes; requested geo не независимо verified IP geography. CapSolver balance before9.9842 after9.9818; три test tasks solved, local residential demo verification success. Новые paid probes не нужны до controlled approval. Actual HH challenge application/acceptance не доказано: direct/proxy страницы в пробе200, production в другое время blocked.
248 unique production CAPTCHA attempt events:137 unsupported recaptcha,49 deadline insufficient,62 unauthorized domain. Не все сделали paid provider request. timeout setting40 fed as minimum provider seconds, не actual solve timeout. HH domains разрешены runtime overrides.
Internal proxy global tracker ~1.019GiB при1GiB лимите, m.hh.ru .3695GiB при .05GiB; creation-time budget checks/reused adapter recording без per-request gate. Numeric DataImpulse remaining quota неизвестна: control-plane API credential не обнаружен.

## Production read-only access

SSH root@167.172.47.34, remote /opt/job-ftch; использовать уже настроенный доступ, не читать ключи/secret files. Проверенный ключевой locator находится C:/Users/User/AppData/Local/Temp/artem-server-access/id_ed25519_nddev_artem; path не разрешение читать содержимое ключа. Containers telegram_bot-bot-1, telegram_bot-public-api-1, telegram_bot-postgres-1, telegram_bot-qdrant-1, job-ftch-observability-openobserve-1, job_ftch_site-web-1. Секреты резолвить внутри штатного app settings, не печатать. asyncpg default_transaction_read_only=on, statement_timeout bounded; OpenObserve search POST — read-only query, schema flattened fields ограничены, reasons находятся также body JSON.
Source registry внутри сети: http://public-api:8080/public/tenants/ai_jobs/sources.json. Postgres предыдущий размер ~749MB не доказательство причины роста; нужна раздельная inventory. Production scheduler наблюдался ~12h, run journal мог быть stale. Never invoke production run/reset/replay during audit.

## Retention and history limits

Existing snapshots purge и outcome max-runs не единая14-day policy; OpenObserve compose local disk v0.90.3 без явной retention. Проверить effective server settings/version. Publication archive хранит exact text/full source evidence/significant stage results долговечно, operational diagnostics14d. Current mutable canonical data не архив. Deleted Telegram texts и old stage evidence восстановимы только по реальным первичным копиям; backfill маркировать reconstructed.

