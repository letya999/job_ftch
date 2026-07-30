---
title: "BgeMThreeNode"
description: "BGE-M3 encoder: dense+sparse vectors in RawItem metadata."
updated: 2026-07-27
---
# BgeMThreeNode

`BgeMThreeNode` кодирует текст raw item через BGE-M3 и кладёт dense+sparse
представления в metadata, чтобы downstream scoring не делал повторный encode.

## Вход и выход

**Вход:** `RawItem`.

**Выход:** `RawItem` с `metadata.bgem3_dense` и `metadata.bgem3_sparse`, если
encoding успешен.

При пустом тексте, неверном ответе provider или исключении узел fail-open
возвращает item без vectors и пишет warning в logger.

## Параметры

`provider` — объект с sync методом `encode(text, max_length=..., return_sparse=True)`.

`max_chars = 4096` — ограничение карточки текста.

`max_length = 1024` — max length для provider.

Graph params могут проверить ожидаемый `model`, а также переопределить
`max_chars` и `max_length`.

## Логика

Текст собирается через `build_bgem3_card(item.text, metadata=item.metadata)`,
чтобы учитывать body и важные metadata-поля. Encode запускается в thread
(`asyncio.to_thread`), потому что provider синхронный/CPU-bound.

Результат кешируется по построенной карточке текста. Provider обязан вернуть
оба ключа: `dense` и `sparse`; отсутствие одного из них считается ошибкой
контракта.

## Границы

Узел не делает scoring и не решает relevance. Он готовит общий embedding
substrate для semantic prefilter, shot scorer, reranker/evidence stages.
