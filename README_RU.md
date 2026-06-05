# job_ftch

![Python](https://img.shields.io/badge/python-3.12+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-early%20development-orange.svg)

**job_ftch** — это open-source асинхронный конвейер (pipeline), который собирает вакансии из Telegram-каналов, групп, комментариев к постам и карьерных сайтов компаний. Он нормализует их в единую схему Job и выдает структурированный JSON.

## Почему?
Вакансии на AI-роли (LLM Engineer, AI PM, MLOps, AgentOps, AI Infra) разбросаны по десяткам Telegram-чатов. Единого структурированного источника для этих нишевых, но быстрорастущих ролей не существует.

## Быстрый старт
```bash
git clone https://github.com/[owner]/job_ftch
cd job_ftch
uv sync
cp .env.dev.example .env
# отредактируйте .env, указав свои учетные данные Telegram API
uv run python app.py
```

## Архитектура
Гексагональная архитектура (Ports & Adapters) — подробности см. в [docs/architecture.md](docs/architecture.md).

## Документация
Документация по архитектуре, видению, правилам и технологическому стеку находится в директории [docs/](docs/).

## Участие в разработке
Мы приветствуем вклад в проект! Пожалуйста, ознакомьтесь с [CONTRIBUTING.md](CONTRIBUTING.md) для получения инструкций.

## Лицензия
MIT
