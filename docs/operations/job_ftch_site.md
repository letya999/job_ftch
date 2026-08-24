---
title: "job_ftch_site"
description: "Отдельный Next.js landing/deploy для публичной витрины job_ftch."
updated: 2026-08-24
---

# job_ftch_site

`job_ftch_site/` — самостоятельный Next.js landing/deploy. Он не входит в
общий runtime-контейнер pipeline или Telegram-бота.

Публичная структура:

- `/` — короткий лендинг open-source проекта, pipeline и архитектура;
- `/jobs` — вакансии из durable ledger публикаций Telegram-канала;
- `/sources` — полный реестр настроенных источников с типами.

## Локальный запуск

```powershell
Copy-Item job_ftch_site/.env.dev.example job_ftch_site/.env.dev
just site-install
just site-dev
```

## Docker

```powershell
docker compose --env-file job_ftch_site/.env.dev \
  -f job_ftch_site/docker-compose.dev.yml up --build

docker compose --env-file job_ftch_site/.env.prod \
  -f job_ftch_site/docker-compose.prod.yml up --build -d
```

Production site принимает `JOB_FTCH_PUBLIC_API_BASE_URL` — URL публичного
read-only API Telegram bridge. Без него каталоги остаются пустыми: сайт не
подменяет runtime-данные демонстрационными карточками.

## Atomic structure

- `src/components/atoms/` — theme toggle;
- `src/components/molecules/` — human/agent projection dock;
- `src/components/organisms/` — consent и analytics;
- `src/components/layouts/` — site shell;
- `src/app/` — страницы, маршруты API и SEO-поверхность.

## Legal / machine surface

Legal routes, `robots.txt`, sitemap и `llms.txt` описаны в
[site API](job_ftch_site_api.md).
