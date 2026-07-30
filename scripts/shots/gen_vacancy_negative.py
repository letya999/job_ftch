"""Generate 10 negative vacancy shots (jobs we DEFINITELY do NOT want).

Synthetic but realistic, mirroring dataset styles (TG / career / hh / getmatch).
Cluster: ML-but-not-applied-LLM-product - classic DS, Data Engineer, ML
Researcher, MLOps, Computer Vision, NLP-at-ML-lab, Product Owner, BI/Analytics,
Principal/Staff ML, LLM Training/fine-tuning. These mention ML/AI/LLM as skills
but the CORE role is not building products that call LLM APIs.
"""

from __future__ import annotations

import json
from pathlib import Path

SHOTS: list[tuple[str, str, str]] = [
    (
        "Data Scientist",
        "career_site",
        """Data Scientist (продуктовая аналитика)

О команде: Развиваем продукт и принимаем решения на данных. Ищем DS с фокусом на
продуктовую аналитику и эксперименты.

Чем предстоит заниматься:
- Строить модели churn, LTV, propensity на табличных данных (CatBoost, LightGBM).
- Проектировать и анализировать A/B-тесты, считать статзначимость и guardrail-метрики.
- Делать feature engineering, сегментацию, forecasting спроса.
- Готовить дашборды и аналитические отчёты для продактов.

Требования:
- Сильная статистика, A/B-тестирование, классический ML на табличных данных.
- SQL, Python, pandas, sklearn, statsmodels.
- Опыт продуктовой аналитики и работы с бизнес-метриками.

Удаленно/гибрид.""",
    ),
    (
        "Data Engineer",
        "hh",
        """Data Engineer

<p>Строим и поддерживаем дата-платформу компании. Ищем инженера данных в команду DWH.</p>
<p><strong>Обязанности:</strong></p>
<ul>
<li>Разрабатывать ETL/ELT пайплайны на Airflow, dbt, Spark.</li>
<li>Проектировать витрины и модели данных в ClickHouse / Greenplum.</li>
<li>Настраивать стриминг на Kafka, CDC, data quality.</li>
<li>Оптимизировать запросы, партиционирование, стоимость хранилища.</li>
</ul>
<p><strong>Требования:</strong></p>
<ul>
<li>Сильный SQL, опыт Spark, Airflow, dbt.</li>
<li>Опыт построения DWH и потоковых пайплайнов.</li>
<li>Python для пайплайнов.</li>
</ul>
<p>Удаленно.</p>""",
    ),
    (
        "ML Researcher / Research Engineer",
        "telegram_channel",
        """Research Engineer / ML Researcher
#research #ml

Компания: AI-лаборатория
Чем предстоит заниматься:
- Исследовать и улучшать архитектуры моделей, проводить эксперименты.
- Обучать модели с нуля, дообучать, оптимизировать training throughput на multi-GPU.
- Работать над reasoning capabilities, alignment, дообучением языковых моделей.
- Публиковать результаты, воспроизводить SOTA, готовить датасеты для обучения.

Требования:
- Глубокое понимание DL, опыт обучения моделей (PyTorch, distributed training).
- Опыт research: эксперименты, benchmarking, error analysis.
- Желательно публикации.

Это исследовательская роль про создание и обучение моделей, не про продуктовую интеграцию API.""",
    ),
    (
        "MLOps Engineer",
        "career_site",
        """MLOps Engineer

О команде: Отвечаем за инфраструктуру обучения и деплоя ML-моделей в компании.

Обязанности:
- Строить CI/CD для ML, model registry, feature store.
- Развёртывать инференс на Kubernetes, Triton, оптимизировать GPU-кластеры.
- Настраивать мониторинг дрейфа, метрик и алертинг.
- Автоматизировать train/validate/deploy пайплайны на Airflow/Kubeflow.

Требования:
- Kubernetes, Docker, Terraform, CI/CD.
- MLflow, Kubeflow, Feast, Triton/BentoML.
- Python, понимание ML-жизненного цикла.

Это инфраструктурная роль, не продуктовая разработка на LLM API.""",
    ),
    (
        "Computer Vision Engineer",
        "hh",
        """Computer Vision Engineer

<p>Разрабатываем системы видеоаналитики для промышленности и ритейла.</p>
<p><strong>Обязанности:</strong></p>
<ul>
<li>Обучать модели детекции и сегментации (YOLO, Detectron2, U-Net).</li>
<li>Оптимизировать инференс под edge: TensorRT, ONNX, квантизация.</li>
<li>Строить трекинг объектов и видеоаналитику.</li>
<li>Собирать и размечать датасеты изображений.</li>
</ul>
<p><strong>Требования:</strong></p>
<ul>
<li>PyTorch, OpenCV, опыт CV-задач.</li>
<li>Знание архитектур детекции/сегментации.</li>
<li>Оптимизация моделей под устройства.</li>
</ul>""",
    ),
    (
        "NLP Engineer at ML Lab",
        "career_site",
        """NLP Engineer (Лаборатория машинного обучения)

О подразделении: Исследовательская лаборатория МО банка. Занимаемся обучением и
адаптацией языковых моделей под внутренние задачи.

Чем предстоит заниматься:
- Дообучать трансформерные модели (BERT, RoBERTa) под NER, классификацию, извлечение.
- Готовить датасеты, проводить эксперименты, error analysis, benchmarking.
- Оптимизировать гиперпараметры и качество моделей на русскоязычных корпусах.
- Готовить внутренние исследовательские отчёты.

Требования:
- PyTorch, HuggingFace Transformers, опыт обучения NLP-моделей.
- Понимание классических NLP-задач и метрик.
- Исследовательский подход.

Роль про обучение и адаптацию моделей в лаборатории, не про продуктовую интеграцию LLM API.""",
    ),
    (
        "AI Product Owner",
        "hh",
        """AI Product Owner

<p>Ищем владельца продукта для направления AI-фич. Роль продуктовая, не инженерная.</p>
<p><strong>Обязанности:</strong></p>
<ul>
<li>Формировать стратегию и роадмап AI-продукта.</li>
<li>Приоритизировать бэклог, писать требования, работать со стейкхолдерами.</li>
<li>Анализировать метрики, проводить customer development.</li>
<li>Координировать команду разработки и дизайна.</li>
</ul>
<p><strong>Требования:</strong></p>
<ul>
<li>Опыт продакт-менеджмента, желательно в AI/ML.</li>
<li>Понимание возможностей LLM на уровне продукта.</li>
<li>Сильные коммуникации и аналитика.</li>
</ul>
<p>Это управленческая роль, разработки кода не предполагает.</p>""",
    ),
    (
        "Analytics Engineer / BI",
        "telegram_channel",
        """Analytics Engineer (BI)
#удаленка #аналитика

Компания: SaaS-продукт
Задачи:
- Строить слой метрик на dbt, semantic layer, сертифицированные витрины.
- Разрабатывать дашборды (Tableau, Power BI, DataLens, Superset).
- Считать продуктовые метрики: retention, conversion, ARPU, cohort analysis.
- Поддерживать self-service аналитику.

Требования:
- Сильный SQL, dbt, опыт BI-инструментов.
- Продуктовая аналитика и метрики.
- Python на базовом уровне.

Роль аналитическая, не разработка AI-продуктов.""",
    ),
    (
        "Principal ML Engineer (recsys)",
        "getmatch",
        """Principal ML Engineer (Recommendations)

Локация: Remote
Зарплата: 400 000 - 550 000 ₽
Стек: Python, PyTorch, Spark, FAISS, CatBoost, Airflow

Компания: крупный маркетплейс, команда рекомендаций.

Задачи:
- Развивать рекомендательную систему: candidate generation, learning-to-rank.
- Обучать модели ранжирования (two-tower, DLRM) на больших кликовых логах.
- Строить user/item embeddings, ANN-поиск, онлайн-обучение.
- Вести A/B-тесты метрик CTR, конверсии, retention.
- Менторить команду, задавать технические стандарты.

Требования:
- Глубокий опыт recsys / ranking ML на больших данных.
- Сильный PyTorch, Spark, продакшн ML.
- Лидерский опыт.

Роль про recsys и ranking ML, не про LLM-продукты.""",
    ),
    (
        "LLM Training / Fine-tuning Engineer",
        "career_site",
        """LLM Training Engineer (Fine-tuning)

О команде: Обучаем и дообучаем большие языковые модели под доменные задачи.

Чем предстоит заниматься:
- Дообучать LLM: SFT, LoRA/QLoRA, DPO/RLHF, alignment.
- Готовить и чистить обучающие датасеты, дедупликация, contamination checks.
- Оптимизировать multi-GPU training throughput, distributed training.
- Проводить benchmarking чекпоинтов, preference datasets, экспертную оценку.
- Исследовать reasoning capabilities и способности к рассуждению моделей.

Требования:
- Глубокое понимание обучения трансформеров, PyTorch, DeepSpeed/FSDP.
- Опыт fine-tuning и distributed training на кластере GPU.
- Research-бэкграунд.

Роль про обучение и адаптацию моделей, НЕ про интеграцию готовых LLM API в продукт.""",
    ),
]


def main() -> None:
    out = Path("fixtures/shots/vacancy_negative.jsonl")
    with out.open("w", encoding="utf-8") as f:
        for i, (role, src, text) in enumerate(SHOTS):
            rec = {
                "id": f"vac_neg_{i:02d}",
                "kind": "vacancy",
                "label": "negative",
                "role": role,
                "source_style": src,
                "text": text.strip(),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"wrote {len(SHOTS)} negative vacancy shots to {out}")


if __name__ == "__main__":
    main()
