# Базовые протоколы (Interfaces)

**Слой**: application
**Файл**: `job_ftch/application/contracts.py`

Большинство интерфейсов в системе реализованы через `typing.Protocol` с
декоратором `@runtime_checkable`.
Это позволяет использовать утиную типизацию
вместе с проверкой типов во время выполнения (`isinstance`).

## 1. Source[T]
Интерфейс источника данных.
*   `fetch() -> AsyncIterator[T | QuarantinedRawItem]` — основной метод для
    получения элементов.
*   **Реализации**: `TelegramSource`, `ScraperSource`, `RestAPISource`.
*   **Применение**: Начальное звено пайплайна.

## 2. Stage[In, Out]
Интерфейс узла обработки (ноды).
*   `process(item: In) -> Out | None` — обрабатывает элемент.
Возвращает
    трансформированный элемент или `None` для прерывания цепочки (дропа).
*   **Реализации**: `ExtractionNode`, `DedupNode`, `RiskScoringNode`.
*   **Применение**: Промежуточные звенья пайплайна.

## 3. Sink[T]
Интерфейс вывода данных.
*   `emit(item: T) -> None` — отправляет финальный результат во внешнюю систему.
*   **Реализации**: `TelegramPostingSink`, `JsonFileSink`.
*   **Применение**: Финальное звено пайплайна.

## 4. Store
Интерфейс хранилища состояния пайплайна.
*   `has_processed(id) / mark_processed(id)` — отслеживание обработанных айтемов.
*   `has_dedup_key / remember_dedup_key` — хранение ключей дедупликации.
*   `get_run_state / set_run_state` — хранение курсоров и смещений.
*   **Реализации**: `SQLiteStore`, `PostgreSQLStore`, `InMemoryStore`.

## 5. LLMProvider
Интерфейс для работы с языковыми моделями.
*   `extract(text, schema) -> T` — извлекает структурированные данные по схеме.
*   **Реализации**: `OpenAIProvider`.
*   **Применение**: Используется в `ExtractionNode`.

## 6. AuthProvider
Интерфейс разрешения учётных данных.
*   `resolve(source_id) -> dict` — возвращает словарь с секретами (токены, API
    keys) для конкретного источника.
*   **Реализации**: `EnvAuthProvider`, `FileAuthProvider`, `VaultAuthProvider`.
*   **Применение**: Используется при инициализации `Source`.

## Пример реализации своего Stage

```python
from job_ftch.application.contracts import Stage
from job_ftch.domain.models import JobDraft

class MyCustomFilter(Stage[JobDraft, JobDraft]):
    async def process(self, item: JobDraft) -> JobDraft | None:
        if "BadWord" in item.description_raw:
            return None  # Drop item
        return item
```
