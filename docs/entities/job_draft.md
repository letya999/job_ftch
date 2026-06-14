# JobDraft

**Слой**: domain
**Файл**: `job_ftch/domain/models.py`
**Протокол / Базовый класс**: `pydantic.BaseModel`

## Что это

`JobDraft` — это структурированный "черновик" вакансии, полученный сразу после
этапа извлечения данных (extraction) с помощью LLM.
Он содержит поля в том виде,
в котором они были найдены в тексте (raw fields), и является промежуточным
звеном перед финальной нормализацией и валидацией.

## Поля

| Поле | Тип | Описание |
| :--- | :--- | :--- |
| `draft_id` | `str` | Уникальный ID черновика. |
| `raw_item_id` | `str` | Ссылка на исходный `RawItem.stable_id`. |
| `source_url` | `HttpUrl` | Ссылка на оригинал вакансии. |
| `title_raw` | `str` | Заголовок вакансии из текста. |
| `company_name_raw` | `str` | Название компании из текста. |
| `description_raw` | `str` | Описание вакансии (обычно очищенный текст). |
| `work_mode` | `WorkMode` | Формат работы (Remote, Onsite, Hybrid). |
| `compensation` | `CompensationRange` | Данные о зарплате (минимум, максимум, валюта). |
| `extraction_status` | `Status` | Статус извлечения (COMPLETE, PARTIAL, FAILED). |
| `provenance` | `Provenance` | Метаданные о том, какой моделью и когда извлечено. |

## Когда создаётся / откуда берётся

Создаётся в `ExtractionNode` в результате обработки текста `RawItem` через
`LLMProvider`.

## Куда идёт после

После создания `JobDraft` проходит через:
1.  `ExtractionValidationNode` (проверка качества извлечения).
2.  Узлы нормализации (Title, Company, Location, Compensation).
3.  `JobValidationNode`, где он превращается в `JobRecord`.

## Что с ней нельзя делать / инварианты

1.  `JobDraft` не является окончательной версией вакансии и не должен
    попадать в публичные Sinks.
2.  Поле `description_raw` не может быть пустым.
3.  Поля нормализованных данных (напр. `role_family`) могут быть пустыми на
    этом этапе.

## Связанные сущности

*   `RawItem` — источник данных для черновика.
*   `LLMProvider` — инструмент, создающий черновик.
*   `JobRecord` — финальная стадия развития черновика.

## Пример

```python
from job_ftch.domain.models import JobDraft, SourceKind, WorkMode

draft = JobDraft(
    raw_item_id="hash123",
    source_kind=SourceKind.TELEGRAM,
    source_name="dev_jobs",
    description_raw="Ищем senior python разработчика...",
    title_raw="Senior Python Developer",
    work_mode=WorkMode.REMOTE
)
```
