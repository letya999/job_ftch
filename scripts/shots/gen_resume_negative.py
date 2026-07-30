"""Generate the 8 missing negative resume shots (profiles we do NOT target).

These mirror the structure of the existing DB resume shots (name, role,
contacts, Кратко, Опыт работы, Ключевые навыки, Проекты, Предпочитаемые
задачи, Не основной фокус, Интересующие роли). They represent the
"ML-but-not-applied-LLM-product" cluster: classic DS, MLOps, CV, Data Eng,
RecSys, research, analytics. Combined with the 2 existing negatives
(LLM Training/NLP Research, Senior Data Scientist) this yields 10 neg resume.
"""

from __future__ import annotations

import json
from pathlib import Path

SHOTS: list[tuple[str, str]] = [
    (
        "MLOps / ML Platform Engineer",
        """Артём Кузнецов
MLOps / ML Platform Engineer
Новосибирск / удаленно · Telegram: @akuznetsov_mlops · Email: artem.kuznetsov.mlops@example.com
Кратко
Инженер ML-платформы с фокусом на инфраструктуру обучения и деплоя моделей. Строю CI/CD для ML,
feature store, model registry, мониторинг дрейфа и автоскейлинг инференса. Основной интерес -
надёжность и воспроизводимость ML-инфраструктуры, а не продуктовая работа с LLM API.
Опыт работы
MLOps Engineer
Big Tech, удаленно · 2022 - 2026
• Развернул Kubeflow Pipelines и MLflow для трекинга экспериментов и model registry.
• Настроил CI/CD для моделей: автоматический train, валидация, canary-деплой, rollback.
• Построил feature store на Feast + Redis для онлайн/офлайн консистентности признаков.
• Внедрил мониторинг дрейфа данных и качества моделей (Evidently, Prometheus, Grafana).
• Оптимизировал GPU-кластер: scheduling, spot-инстансы, autoscaling инференса на Triton.
Platform Engineer
Fintech · 2019 - 2022
• Поддерживал Kubernetes-кластеры, Helm-чарты, Terraform для ML-нагрузок.
• Автоматизировал provisioning training-окружений и data pipelines на Airflow.
Ключевые навыки
Infra: Kubernetes, Docker, Terraform, Helm, ArgoCD, AWS/GCP
MLOps: MLflow, Kubeflow, Feast, Triton, BentoML, DVC, Airflow
Monitoring: Prometheus, Grafana, Evidently, drift detection
Предпочитаемые задачи
• Построение ML-платформы и инфраструктуры обучения.
• CI/CD, деплой и мониторинг моделей.
• Оптимизация GPU-кластеров и стоимости инференса.
Не основной фокус
• Прикладная разработка продуктов на LLM API.
• Prompt engineering, RAG, агентные системы как основная работа.
• Fullstack/frontend.
Интересующие роли
MLOps Engineer, ML Platform Engineer, ML Infrastructure Engineer, ML SRE, Model Deployment Engineer.""",
    ),
    (
        "Computer Vision Engineer",
        """Павел Соколов
Computer Vision Engineer
Москва / гибрид · Telegram: @psokolov_cv · Email: pavel.sokolov.cv@example.com
Кратко
CV-инженер с опытом детекции, сегментации и распознавания на изображениях и видео. Обучаю и
оптимизирую нейросети для камер, беспилотников и промышленного контроля качества. Основной фокус -
компьютерное зрение и edge-инференс, не LLM-продукты.
Опыт работы
Computer Vision Engineer
Industrial AI, гибрид · 2021 - 2026
• Разрабатывал модели детекции дефектов на производственных линиях (YOLO, Detectron2).
• Обучал сегментационные сети (U-Net, Mask R-CNN) для медицинских и спутниковых снимков.
• Оптимизировал инференс под edge: TensorRT, ONNX, квантизация, прунинг на Jetson.
• Собирал и размечал датасеты изображений, настраивал аугментации и active learning.
• Внедрял трекинг объектов (DeepSORT, ByteTrack) для видеоаналитики.
ML Engineer (CV)
Retail tech · 2018 - 2021
• Делал распознавание товаров на полках и подсчёт посетителей по видеопотоку.
• Работал с OpenCV, PyTorch, классическими дескрипторами и CNN.
Ключевые навыки
CV: PyTorch, OpenCV, YOLO, Detectron2, U-Net, Mask R-CNN, DeepSORT
Edge: TensorRT, ONNX, quantization, pruning, Jetson, CUDA
Data: image annotation, augmentation, active learning, COCO/VOC
Предпочитаемые задачи
• Детекция, сегментация, трекинг на изображениях и видео.
• Оптимизация CV-моделей под edge-устройства.
• Работу с датасетами изображений.
Не основной фокус
• Работа с текстом и LLM API.
• RAG, агенты, prompt engineering.
• Бизнес-автоматизации и интеграции.
Интересующие роли
Computer Vision Engineer, CV Research Engineer, Deep Learning Engineer (Vision), Perception Engineer.""",
    ),
    (
        "Data Engineer",
        """Михаил Орлов
Data Engineer
Екатеринбург / удаленно · Telegram: @morlov_de · Email: mikhail.orlov.de@example.com
Кратко
Дата-инженер с опытом построения батч и стриминг пайплайнов, хранилищ и витрин. Строю ETL/ELT,
оркестрацию и data quality для аналитики и ML. Основной интерес - надёжные данные в масштабе, а не
разработка AI-продуктов на LLM.
Опыт работы
Senior Data Engineer
E-commerce, удаленно · 2021 - 2026
• Построил DWH на ClickHouse и Greenplum, витрины для аналитики и отчётности.
• Разрабатывал ETL/ELT пайплайны на Airflow, dbt, Spark для терабайтных объёмов.
• Внедрил стриминг на Kafka + Flink для real-time событий и CDC из PostgreSQL.
• Настроил data quality (Great Expectations), каталог данных и lineage.
• Оптимизировал стоимость и производительность запросов, партиционирование, индексы.
Data Engineer
Telecom · 2018 - 2021
• Собирал пайплайны загрузки логов и биллинга, поддерживал Hadoop/Hive.
• Готовил данные для DS-команд и BI-дашбордов.
Ключевые навыки
Data: SQL, Spark, dbt, Airflow, Kafka, Flink, ClickHouse, Greenplum, Hadoop
Cloud: S3, Glue, BigQuery, Snowflake
Quality: Great Expectations, data catalog, lineage, partitioning
Предпочитаемые задачи
• Построение ETL/ELT и потоковых пайплайнов.
• Проектирование DWH, витрин и моделей данных.
• Data quality и оптимизация запросов.
Не основной фокус
• Разработка продуктов на LLM API.
• RAG, агенты, prompt engineering.
• Frontend и продуктовый fullstack.
Интересующие роли
Data Engineer, Senior Data Engineer, Analytics Engineer, DWH Engineer, Big Data Engineer.""",
    ),
    (
        "Recommendation Systems ML Engineer",
        """Денис Фролов
Recommendation Systems ML Engineer
Москва / гибрид · Telegram: @dfrolov_recsys · Email: denis.frolov.recsys@example.com
Кратко
ML-инженер рекомендательных систем. Строю кандидатогенерацию, ранжирование и embeddings для
ленты, поиска и персонализации. Основной фокус - recsys и ranking ML на больших данных, не LLM-продукты.
Опыт работы
RecSys ML Engineer
Marketplace, гибрид · 2021 - 2026
• Разрабатывал двухстадийную рекомендательную систему: candidate generation + ranking.
• Обучал модели ранжирования (gradient boosting, two-tower, DLRM) на кликовых логах.
• Строил user/item embeddings, ANN-поиск (HNSW, FAISS) для кандидатогенерации.
• Внедрял онлайн-обучение и A/B-тесты метрик CTR, конверсии, retention.
• Оптимизировал feature pipelines и латентность инференса ранжирования.
ML Engineer
Media / streaming · 2018 - 2021
• Делал персонализацию ленты и похожие айтемы, collaborative filtering.
• Работал с implicit feedback, matrix factorization, sequence models.
Ключевые навыки
RecSys: candidate generation, learning-to-rank, two-tower, DLRM, collaborative filtering
ML: PyTorch, CatBoost, FAISS, HNSW, feature engineering
Infra: Spark, Airflow, online inference, A/B testing
Предпочитаемые задачи
• Рекомендательные системы и ранжирование.
• Embeddings и ANN-поиск для персонализации.
• Оптимизация ranking-метрик и инференса.
Не основной фокус
• Разработка продуктов на LLM API как основная работа.
• Prompt engineering, агентные системы.
• Бизнес-автоматизации и no-code.
Интересующие роли
RecSys ML Engineer, Ranking Engineer, Personalization Engineer, Search ML Engineer (ranking).""",
    ),
    (
        "Classic ML Engineer (tabular)",
        """Ольга Северина
Machine Learning Engineer (tabular)
Казань / удаленно · Telegram: @oseverina_ml · Email: olga.severina.ml@example.com
Кратко
ML-инженер по табличным данным и классическому ML. Решаю задачи скоринга, прогнозирования и
классификации на структурированных данных. Основной интерес - tabular ML и production-модели, не
LLM-приложения.
Опыт работы
Machine Learning Engineer
Banking / scoring, удаленно · 2021 - 2026
• Строила кредитный скоринг и antifraud-модели (CatBoost, LightGBM, XGBoost).
• Делала feature engineering на табличных данных, отбор признаков, калибровку вероятностей.
• Оценивала модели: ROC-AUC, PR-AUC, KS, Gini, stability (PSI), fairness.
• Выкатывала модели в прод через REST-сервисы и батч-скоринг.
• Поддерживала мониторинг качества и переобучение по расписанию.
Data Scientist / ML Engineer
Insurance · 2018 - 2021
• Прогнозировала убыточность и склонность к оттоку.
• Работала с SQL, pandas, sklearn, статистическими тестами.
Ключевые навыки
ML: CatBoost, LightGBM, XGBoost, scikit-learn, feature engineering, calibration
Eval: ROC-AUC, PR-AUC, KS, Gini, PSI, cross-validation
Stack: Python, SQL, pandas, numpy, Flask/FastAPI, Docker
Предпочитаемые задачи
• Табличный ML: скоринг, antifraud, прогнозирование.
• Feature engineering и валидация моделей.
• Production-деплой классических ML-моделей.
Не основной фокус
• LLM API, RAG, агентные системы.
• Prompt engineering и генеративный AI.
• Fullstack и frontend.
Интересующие роли
Machine Learning Engineer, ML Engineer (tabular), Credit Scoring Engineer, Antifraud ML Engineer.""",
    ),
    (
        "BI / Analytics Engineer",
        """Светлана Гордеева
BI / Analytics Engineer
Санкт-Петербург / удаленно · Telegram: @sgordeeva_bi · Email: svetlana.gordeeva.bi@example.com
Кратко
Аналитик и BI-инженер. Строю витрины, дашборды и метрики для продуктовых и бизнес-команд.
Основной фокус - аналитика, SQL и визуализация, не разработка AI-продуктов.
Опыт работы
Analytics Engineer
SaaS, удаленно · 2021 - 2026
• Строила слой метрик на dbt, semantic layer, сертифицированные витрины.
• Разрабатывала дашборды в Tableau, Power BI, DataLens, Superset.
• Считала продуктовые метрики: retention, conversion, ARPU, cohort analysis.
• Поддерживала self-service аналитику и документацию метрик.
• Делала ad-hoc исследования и презентации для стейкхолдеров.
BI Analyst
Retail · 2017 - 2021
• Готовила регулярную отчётность по продажам, складам и маркетингу.
• Писала сложные SQL-выгрузки, оптимизировала запросы.
Ключевые навыки
BI: Tableau, Power BI, Superset, DataLens, dbt, semantic layer
Analytics: SQL, продуктовые метрики, cohort/funnel analysis, A/B basics
Stack: PostgreSQL, ClickHouse, Excel/Google Sheets, Python basics
Предпочитаемые задачи
• Построение витрин, метрик и дашбордов.
• Продуктовую и бизнес-аналитику.
• Self-service BI и отчётность.
Не основной фокус
• Разработка на LLM API, RAG, агенты.
• Backend-heavy и fullstack-разработка.
• ML-инжиниринг и обучение моделей.
Интересующие роли
Analytics Engineer, BI Engineer, BI Analyst, Product Analyst, Data Analyst.""",
    ),
    (
        "NLP Engineer at ML Lab",
        """Григорий Лебедев
NLP Engineer / ML Lab
Москва / офис · Telegram: @glebedev_nlp · Email: grigory.lebedev.nlp@example.com
Кратко
NLP-инженер исследовательской лаборатории машинного обучения банка. Занимаюсь обучением и
адаптацией языковых моделей, классическими NLP-задачами и экспериментами. Основной фокус -
исследования и обучение моделей, не продуктовая интеграция LLM API.
Опыт работы
NLP Engineer
Лаборатория машинного обучения, банк · 2021 - 2026
• Обучал и дообучал трансформерные модели (BERT, RoBERTa) под внутренние задачи.
• Решал NER, классификацию, извлечение отношений на русскоязычных корпусах.
• Проводил эксперименты, готовил датасеты, публиковал внутренние отчёты и метрики.
• Сравнивал архитектуры, оптимизировал гиперпараметры, делал error analysis.
• Участвовал в исследовательских прототипах распознавания и суммаризации.
Research Engineer (NLP)
Academic spin-off · 2018 - 2021
• Работал над embeddings, sequence labeling, языковыми моделями с нуля.
• Писал статьи и воспроизводил research-результаты.
Ключевые навыки
NLP: BERT, RoBERTa, NER, classification, sequence labeling, summarization
ML: PyTorch, HuggingFace Transformers, datasets, experiment tracking
Methods: fine-tuning, evaluation, error analysis, research prototyping
Предпочитаемые задачи
• Обучение и адаптация языковых моделей.
• Классические NLP-задачи и эксперименты.
• Исследовательские прототипы и benchmarking.
Не основной фокус
• Продуктовая интеграция LLM API в сервисы.
• Агентные системы, бизнес-автоматизации.
• Fullstack/frontend.
Интересующие роли
NLP Engineer, NLP Research Engineer, ML Researcher (NLP), Applied Scientist (NLP).""",
    ),
    (
        "Speech / Audio ML Engineer",
        """Виктория Зайцева
Speech / Audio ML Engineer
Москва / удаленно · Telegram: @vzaytseva_speech · Email: viktoria.zaytseva.speech@example.com
Кратко
ML-инженер по обработке речи и аудио. Обучаю ASR/TTS, диаризацию и audio-классификацию.
Основной фокус - speech-технологии и DSP, не LLM-продукты и не текстовые агенты.
Опыт работы
Speech ML Engineer
Voice tech, удаленно · 2021 - 2026
• Обучала и дообучала ASR-модели (Whisper, Conformer, wav2vec 2.0) для русского языка.
• Разрабатывала TTS-пайплайны (Tacotron, VITS) и голосовые ассистенты.
• Делала диаризацию спикеров, VAD, шумоподавление, audio augmentation.
• Оптимизировала инференс распознавания под стриминг и низкую латентность.
• Собирала и чистила аудиодатасеты, считала WER/CER.
ML Engineer (Audio)
Call-center analytics · 2018 - 2021
• Строила audio-классификацию эмоций и событий, keyword spotting.
• Работала с librosa, torchaudio, спектрограммами и DSP.
Ключевые навыки
Speech: Whisper, Conformer, wav2vec, Tacotron, VITS, diarization, VAD
Audio: torchaudio, librosa, DSP, spectrograms, augmentation
Eval: WER, CER, MOS, streaming inference
Предпочитаемые задачи
• ASR/TTS и обработку речи.
• Диаризацию, шумоподавление, audio-классификацию.
• Оптимизацию speech-инференса.
Не основной фокус
• Текстовые LLM-продукты и RAG.
• Агентные системы и бизнес-автоматизации.
• Fullstack/frontend.
Интересующие роли
Speech ML Engineer, ASR Engineer, TTS Engineer, Audio Deep Learning Engineer.""",
    ),
]


def main() -> None:
    out = Path("fixtures/shots/resume_negative_authored.jsonl")
    with out.open("w", encoding="utf-8") as f:
        for i, (role, text) in enumerate(SHOTS, start=2):  # start=2: 0,1 are existing
            rec = {
                "id": f"res_neg_{i:02d}",
                "kind": "resume",
                "label": "negative",
                "role": role,
                "text": text.strip(),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"wrote {len(SHOTS)} authored negative resume shots to {out}")


if __name__ == "__main__":
    main()
