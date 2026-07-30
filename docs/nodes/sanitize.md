---
title: "SanitizeNode"
description: "Первый обязательный stage: нормализация, URL policy и базовая валидность RawItem."
updated: 2026-07-27
---
# SanitizeNode

`SanitizeNode` — обязательная первая стадия любой pipeline chain. Он приводит
сырой `RawItem` к безопасному и воспроизводимому виду до classifier, dedup,
parser, embedding или LLM-узлов.

## Вход и выход

**Вход:** `RawItem` от adapter/crawler/scraper/monitor.

**Выход:** валидированный `RawItem` с нормализованными `text`, `source_name`,
`external_id`, `url` и `metadata`.

**Reject:** `RawItemRejected`, если после sanitation пустой текст, пустой
`source_name`, отсутствует locator (`external_id` или URL), URL невалиден или
origin host запрещён для типа источника.

## Логика

Узел делает Unicode NFKC normalization, убирает BOM/zero-width/control chars,
сжимает whitespace, нормализует URL scheme/host и удаляет fragment.

Длинный текст не дропается, а обрезается до `max_text_length` по ближайшему
пробелу. Это важно для career-site страниц: валидная вакансия часто приходит
вместе с навигацией, футером и boilerplate.

Для Telegram source kinds разрешены только `t.me` и `www.t.me`. Для
`CAREER_SITE` применяется allow-list `allowed_career_site_hosts`, если она
задана в builder/runtime config.

## Metadata

Строковые metadata-поля нормализуются тем же способом, что и основной текст.
URL-поля `board_url`, `job_url`, `post_url` валидируются отдельно как origin
URL.

Если `original_posting_text` отсутствует, узел сохраняет туда исходный текст.
Если текст распознан как structured vacancy, узел добавляет
`extraction_source = telegram_structured` и найденные поля, не перезаписывая
уже пришедшие metadata.

## Границы

`SanitizeNode` не решает jobness/relevance, не дедуплицирует и не извлекает
вакансию. Его задача — сделать последующие стадии детерминированными и не дать
мусорным locator’ам или невалидным URL пройти глубже.
