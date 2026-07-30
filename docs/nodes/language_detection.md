---
title: "LanguageDetectionNode"
description: "JobRecord language detection через injected LanguageDetectorPort."
updated: 2026-07-27
---
# LanguageDetectionNode

`LanguageDetectionNode` определяет язык уже нормализованной вакансии и пишет
результат в metadata.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с `metadata.detected_language`.

Если нет title и description, узел no-op.

## Логика

Sample строится из title и первых 300 символов description. Узел вызывает
injected `LanguageDetectorPort.detect(sample)` и сохраняет результат.

## Границы

Узел не импортирует конкретный detector backend и не меняет `JobRecord.language`
enum. Он только пишет metadata для translation/presentation/observability.
