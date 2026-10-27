# JobFetch

**Превращает разрозненные сигналы о найме в вакансии, которые удобно читать.**

[Сайт и вакансии](https://job-ftch.aiguild.space/ru/jobs) · [Telegram-канал](https://t.me/ai_engineer_jobs) · [Документация](https://letya999.github.io/job_ftch/) · [English](README.md)

JobFetch — открытый асинхронный конвейер для сбора вакансий из Telegram, карьерных сайтов, RSS и API. Он извлекает данные из разных форматов, приводит их к типизированной записи, объединяет дубли, оценивает релевантность и публикует подходящие результаты. Система предназначена для сообществ и команд, которым нужен собственный поток вакансий и контроль над его источниками.

```text
источники → сбор → извлечение → нормализация → дедупликация → оценка → публикация
```

## Посмотреть

- [Вакансии](https://job-ftch.aiguild.space/ru/jobs) и [состояние источников](https://job-ftch.aiguild.space/ru/sources).
- [Публичный Telegram-канал](https://t.me/ai_engineer_jobs).
- [Архитектура](docs/architecture.md) и [настройка источников](docs/sources/setup.md).

## Запустить локальный пример

Нужны Python 3.12+ и [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/letya999/job_ftch.git
cd job_ftch
uv sync
uv run job_ftch eval --fixture classification
```

Команда проверяет локальный тестовый набор: она не обращается к живым сайтам и ничего не публикует. Для запуска своего набора источников начните с [руководства](docs/quickstart.md) и проверьте конфигурацию командой `uv run job_ftch validate --config tenant.yaml`.

## Похожие решения

Это самостоятельные проекты с пересекающимися задачами, а не части JobFetch:

| Проект | Основная задача |
| --- | --- |
| [Job Seek](https://github.com/colophon-group/jobseek) | Поиск по карьерным сайтам и ATS, отслеживание откликов |
| [freehire](https://github.com/strelov1/freehire) | Открытый агрегатор и поиск IT-вакансий |
| [JobSpy](https://github.com/speedyapply/JobSpy) | Python-библиотека для сбора вакансий с крупных площадок |
| [career-ops](https://github.com/career-ops-hq/career-ops) | Локальный помощник кандидата: оценка вакансий, CV и учёт откликов |

Фокус JobFetch — на настраиваемом пути **источник → данные и подтверждения → оценка → публикация**, в том числе для Telegram-сообщества. Основное production-развёртывание — Telegram-бот; надёжность отдельных сайтов проверяется отдельно и не следует автоматически из зелёного CI.

Правила участия — в [CONTRIBUTING.md](CONTRIBUTING.md), политика безопасности — в [SECURITY.md](SECURITY.md). Лицензия: [AGPL-3.0-only](LICENSE).
