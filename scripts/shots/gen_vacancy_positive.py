"""Generate 10 positive vacancy shots (jobs we DEFINITELY want).

Synthetic but realistic, mirroring the styles seen in the dataset:
- Telegram channel posts (free-form, emoji, "Что предстоит делать")
- career-site / hh postings (sections: о компании, обязанности, требования)
- aggregator postings (getmatch/remote-style, stack + salary)

Cluster: applied LLM / AI-product engineering — building products that CALL
LLM APIs (OpenAI/Anthropic/YandexGPT/GigaChat), RAG, agents, AI automation,
GenAI products, prompt engineering, AI-first fullstack. NOT model training,
NOT research, NOT classic ML/DS.
"""

from __future__ import annotations

import json
from pathlib import Path

SHOTS: list[tuple[str, str, str]] = [
    (
        "LLM Application Engineer",
        "career_site",
        """LLM Application Engineer

О компании: Мы продуктовая команда, которая строит AI-ассистентов для корпоративных клиентов.
Наш продукт помогает сотрудникам находить ответы во внутренней базе знаний через чат.

Чем предстоит заниматься:
- Разрабатывать продуктовые фичи поверх LLM API (OpenAI, Anthropic, YandexGPT) на Python.
- Строить и улучшать RAG-пайплайны: чанкинг, embeddings, векторный поиск, reranking.
- Проектировать промпты, system-инструкции, few-shot, structured output через JSON Schema.
- Настраивать guardrails: фильтрация, защита от prompt injection, проверка фактологичности.
- Оценивать качество ответов: offline eval, метрики, human-in-the-loop, A/B.
- Оптимизировать стоимость и латентность: кэширование, batching, выбор модели под задачу.

Требования:
- Уверенный Python, опыт промышленной разработки backend.
- Практический опыт интеграции LLM API в продукт (не только pet-проекты).
- Понимание RAG, embeddings, векторных БД (Qdrant, pgvector, Pinecone).
- Опыт с инструментами: LangChain / LlamaIndex / собственные пайплайны.

Будет плюсом: опыт построения агентов, function calling, оценки качества генерации.
Удаленно, полная занятость.""",
    ),
    (
        "AI Agent Engineer",
        "telegram_channel",
        """AI Agent Engineer (agentic systems)
#удаленка #senior

Компания: продуктовый стартап в сфере автоматизации поддержки
Что предстоит делать:
- Проектировать и внедрять multi-step AI-агентов для бизнес-задач (tool calling, планирование, память).
- Строить оркестрацию агентов на LangGraph / собственном фреймворке поверх LLM API.
- Интегрировать инструменты: поиск, БД, внешние API, выполнение действий.
- Настраивать observability агентов: трейсинг шагов, оценка качества, отлов галлюцинаций.
- Оптимизировать стоимость вызовов LLM и стабильность пайплайнов.

Наш стек: Python, OpenAI / Anthropic API, LangGraph, Qdrant, FastAPI, PostgreSQL.

Ждём:
- Сильный Python и опыт построения сервисов.
- Опыт с агентными системами, function calling, RAG.
- Понимание, как доводить LLM-фичи до production: eval, guardrails, мониторинг.

Удаленка, вилка по итогам интервью.""",
    ),
    (
        "AI Automation Engineer (n8n + LLM)",
        "telegram_channel",
        """🔹AI Automation Engineer
#удаленнаяработа #middle #senior

Компания: агентство по автоматизации бизнес-процессов
🔹Чем предстоит заниматься:
- Строить бизнес-автоматизации с LLM: n8n, Python, OpenAI API, Telegram-боты, CRM-интеграции.
- Проектировать workflow: триггеры, обработка документов через LLM, маршрутизация, уведомления.
- Внедрять RAG над клиентскими базами знаний для авто-ответов.
- Интегрировать сервисы: Google Workspace, amoCRM, Notion, вебхуки, REST/GraphQL API.
- Настраивать промпты и контролировать качество и стоимость генерации.

🔹Что важно:
- Python и опыт автоматизации (n8n / Make / собственные пайплайны).
- Практика интеграции LLM API в рабочие процессы.
- Умение собрать сквозной поток от данных до действия.

Это не чистый no-code: значительная часть - код на Python вокруг LLM.
Удаленно.""",
    ),
    (
        "GenAI Product Engineer",
        "career_site",
        """GenAI Product Engineer

О продукте: Мы делаем генеративные фичи внутри B2C-приложения - ассистент, генерация контента,
умный поиск. Генеративный AI - ядро продукта, а не побочная функция.

Обязанности:
- Разрабатывать продуктовые фичи на базе генеративных моделей (текст, мультимодальность) через API.
- Интегрировать GenAI в пользовательские сценарии: ассистенты, саммаризация, генерация, поиск.
- Проектировать промпты и пайплайны, structured output, streaming-ответы.
- Строить eval-инфраструктуру качества генерации и A/B-эксперименты.
- Работать в связке с продактами и дизайнерами над AI-UX.

Требования:
- Опыт промышленной разработки (Python / TypeScript).
- Реальный опыт интеграции GenAI / LLM API в пользовательский продукт.
- Понимание prompt engineering, RAG, ограничений и стоимости моделей.

Плюс: опыт с мультимодальными API, function calling, agentic-фичами.
Гибрид, Москва или удаленно.""",
    ),
    (
        "Prompt Engineer",
        "hh",
        """Prompt Engineer (LLM)

<p>Компания разрабатывает AI-ассистентов для клиентского сервиса крупных брендов.</p>
<p><strong>Чем предстоит заниматься:</strong></p>
<ul>
<li>Проектировать и итеративно улучшать промпты, system-инструкции, few-shot примеры для LLM.</li>
<li>Строить структурированные пайплайны: chain-of-thought, decomposition, structured output.</li>
<li>Настраивать поведение ассистентов под тон бренда, политики и edge-кейсы.</li>
<li>Создавать наборы для оценки качества, проводить регрессии промптов, A/B-тесты.</li>
<li>Работать с LLM API (OpenAI, Anthropic, GigaChat), анализировать ошибки и галлюцинации.</li>
</ul>
<p><strong>Требования:</strong></p>
<ul>
<li>Опыт работы с LLM API и глубокое понимание prompt engineering.</li>
<li>Базовый Python для построения eval-скриптов и пайплайнов.</li>
<li>Аналитический подход: умение измерять качество и улучшать его системно.</li>
</ul>
<p>Удаленно, полная занятость.</p>""",
    ),
    (
        "AI Integration Engineer",
        "telegram_channel",
        """AI Integration Engineer
#remote #ai #python

Компания: SaaS-платформа, встраивающая AI в продукты клиентов
Задачи:
- Интегрировать LLM API в существующие продукты и внутренние сервисы (backend на Python).
- Строить коннекторы к источникам данных, RAG-слой, structured extraction из документов.
- Разрабатывать API-обёртки и SDK для AI-функций, следить за надёжностью и стоимостью.
- Внедрять кэширование, rate-limiting, fallback между провайдерами моделей.
- Покрывать пайплайны тестами и мониторингом качества.

Стек: Python, FastAPI, OpenAI/Anthropic API, Qdrant/pgvector, PostgreSQL, Docker.

Ожидания:
- Крепкий backend-Python и опыт интеграций.
- Практика работы с LLM API в проде.
- Понимание RAG, embeddings, эвалюации качества.

Удаленка.""",
    ),
    (
        "AI-first Fullstack Engineer",
        "career_site",
        """AI-first Fullstack Engineer (Vibe Coder)

О нас: Небольшая продуктовая команда, быстро собираем AI-MVP и внутренние инструменты.
Работаем AI-first: активно используем Claude Code, Cursor, Codex для ускорения разработки.

Чем предстоит заниматься:
- Быстро прототипировать и доводить до прода продукты с LLM-фичами (Python + TypeScript).
- Собирать fullstack: backend на FastAPI/Node, frontend на React, интеграция LLM API.
- Строить RAG, чат-интерфейсы, агентные сценарии, structured output.
- Использовать AI-ассистентов разработки как часть рабочего процесса.
- Итеративно улучшать продукт по обратной связи, измерять качество AI-фич.

Требования:
- Fullstack-опыт (Python и/или TypeScript).
- Опыт интеграции LLM API в реальные продукты.
- AI-first подход к разработке, быстрый прототипинг.

Удаленно, гибкий график.""",
    ),
    (
        "LLM Solution Architect",
        "hh",
        """LLM Solution Architect

<p>Мы помогаем enterprise-клиентам внедрять LLM-решения. Ищем архитектора именно
LLM-продуктов (не ML-инфраструктуры в целом).</p>
<p><strong>Обязанности:</strong></p>
<ul>
<li>Проектировать архитектуру продуктов на базе LLM: RAG, агенты, оркестрация, интеграции.</li>
<li>Выбирать модели и провайдеров (OpenAI, Anthropic, YandexGPT), считать стоимость и латентность.</li>
<li>Определять подходы к качеству: eval, guardrails, безопасность, prompt injection.</li>
<li>Сопровождать команды разработки, ревью дизайна LLM-пайплайнов.</li>
<li>Готовить технические решения и PoC под задачи заказчика.</li>
</ul>
<p><strong>Требования:</strong></p>
<ul>
<li>Опыт проектирования и вывода в прод LLM-продуктов.</li>
<li>Глубокое понимание RAG, агентов, embeddings, оценки качества.</li>
<li>Сильный инженерный бэкграунд (Python), системное мышление.</li>
</ul>
<p>Удаленно.</p>""",
    ),
    (
        "RAG / Retrieval Engineer",
        "telegram_channel",
        """RAG / Retrieval Engineer
#удаленка #middle

Компания: продукт для работы с корпоративными знаниями
Что делать:
- Строить и улучшать RAG-пайплайн: ingestion, чанкинг, embeddings, гибридный поиск, reranking.
- Интегрировать LLM API для генерации ответов с цитированием источников.
- Развивать качество retrieval: оценка relevance, metrics, борьба с галлюцинациями.
- Работать с векторными БД (Qdrant, pgvector), sparse+dense поиском, фильтрами по метаданным.
- Оптимизировать стоимость и скорость: кэш, выбор модели, батчинг.

Стек: Python, OpenAI/Anthropic API, Qdrant, BGE/e5 embeddings, FastAPI.

Ждём:
- Python и опыт построения поисковых/RAG-систем.
- Понимание embeddings, reranking, эвалюации retrieval.
- Опыт интеграции LLM API в продукт.

Удаленно.""",
    ),
    (
        "AI Engineer (LLM products)",
        "getmatch",
        """AI Engineer (LLM products)

Локация: Remote
Зарплата: 250 000 - 350 000 ₽
Стек: Python, OpenAI API, Anthropic API, LangChain, Qdrant, FastAPI, PostgreSQL, Docker

Компания строит AI-продукты для автоматизации работы с документами и клиентами.

Задачи:
- Разрабатывать backend AI-фич на Python поверх LLM API.
- Строить пайплайны structured extraction, RAG, классификацию и маршрутизацию через LLM.
- Проектировать промпты и structured output, repair-loop для некорректных ответов модели.
- Внедрять eval качества, guardrails, мониторинг стоимости и латентности.
- Интегрировать векторный поиск и метаданные для релевантности.

Требования:
- 2+ года backend-разработки на Python.
- Реальный опыт интеграции LLM API в production-продукты.
- Знание RAG, embeddings, векторных БД, оценки качества генерации.

Не подойдёт тем, кто хочет заниматься обучением моделей с нуля или классическим DS.""",
    ),
]


def main() -> None:
    out = Path("fixtures/shots/vacancy_positive.jsonl")
    with out.open("w", encoding="utf-8") as f:
        for i, (role, src, text) in enumerate(SHOTS):
            rec = {
                "id": f"vac_pos_{i:02d}",
                "kind": "vacancy",
                "label": "positive",
                "role": role,
                "source_style": src,
                "text": text.strip(),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"wrote {len(SHOTS)} positive vacancy shots to {out}")


if __name__ == "__main__":
    main()
