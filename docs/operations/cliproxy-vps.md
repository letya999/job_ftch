---
title: "CLIProxyAPI на VPS"
description: "Безопасная настройка optional CLIProxyAPI sidecar для dev/prod Compose и Google OAuth."
updated: 2026-09-17
---
# CLIProxyAPI на VPS

CLIProxyAPI запускается отдельным Compose-сервисом с профилем `cliproxy`.
Он не встраивается в Python-образ job_ftch: так сохраняются независимое
обновление прокси и rollback. В обычном запуске dev/prod сервис выключен.

## Подготовка

На VPS из корня checkout:

```bash
cp deploy/cliproxy/config.yaml.example deploy/cliproxy/config.yaml
mkdir -p deploy/cliproxy/auth deploy/cliproxy/logs
chmod 700 deploy/cliproxy/auth
```

В `deploy/cliproxy/config.yaml` задайте случайный client key. Тот же ключ
передайте job_ftch через `JOB_FTCH_OPENAI_API_KEY`. Секреты и файлы в
`deploy/cliproxy/config.yaml`, `auth/` и `logs/` не коммитятся.

В production закрепите согласованный release tag или image digest вместо
`eceasy/cli-proxy-api:latest`:

```bash
export JOB_FTCH_CLIPROXY_IMAGE='eceasy/cli-proxy-api:<approved-tag-or-digest>'
```

## Запуск и проверка

Порты публикуются только на loopback VPS:

```bash
docker compose --env-file .env.prod \
  --profile cliproxy \
  -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml \
  up -d cliproxy
curl -fsS http://127.0.0.1:8317/v1/models
```

Не публикуйте `8317` или OAuth callback `8085` наружу через firewall/reverse
proxy. `/v1/models` проверяет, что сервис поднялся и какие model id реально
доступны; в runtime job_ftch указывайте только id из этого ответа.

Для dev используется тот же профиль с `docker-compose.dev.yml` и
`--env-file .env.dev`.

## Google OAuth

Сохранённые OAuth-файлы находятся в `deploy/cliproxy/auth`, поэтому переживают
перезапуск контейнера. Для интерактивного Google login прокиньте callback через
SSH-туннель на рабочую машину:

```bash
ssh -N -L 8085:127.0.0.1:8085 -i <ssh-key> root@<vps-host>
docker compose --env-file .env.prod \
  --profile cliproxy \
  -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml \
  exec cliproxy /CLIProxyAPI/CLIProxyAPI -login -no-browser
```

Откройте напечатанный Google URL в локальном браузере и завершите вход. Не
выставляйте callback-порт в интернет. Если версия образа требует другой
вариант login-флага, используйте help именно этой закреплённой версии:

```bash
docker compose --profile cliproxy -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml \
  run --rm cliproxy --help
```

После OAuth снова проверьте `/v1/models` и только затем включайте потребителя.
Google OAuth/доступ к Gemini может требовать разрешений аккаунта или проекта;
успешный запуск контейнера сам по себе этого не гарантирует.

## Подключение job_ftch

Для основного extraction/relevance LLM через sidecar в env контейнера bot:

```dotenv
JOB_FTCH_LLM_BACKEND=openai
JOB_FTCH_LLM_GATEWAY=cliproxy
JOB_FTCH_OPENAI_BASE_URL=http://cliproxy:8317/v1
JOB_FTCH_OPENAI_API_KEY=<same-client-key-as-config.yaml>
```

`config/runtime.cliproxy.yaml` подключится автоматически и содержит только
пример model id. Перед запуском сверяйте его с `/v1/models`; при необходимости
переопределите `JOB_FTCH_OPENAI_MODEL` и
`JOB_FTCH_RELEVANCE_LLM_MODEL` в env.

Для одного только image CAPTCHA OCR оставьте основной LLM на OpenAI и задайте:

```dotenv
JOB_FTCH_CAPTCHA_VISION_BASE_URL=http://cliproxy:8317/v1
JOB_FTCH_CAPTCHA_VISION_MODEL=<model-id-from-v1-models>
```

В этом случае route `cliproxy_image` должен быть явно включён в runtime; один
только `JOB_FTCH_CAPTCHA_AUTHORIZED_DOMAINS=*` не переключает OCR на CLIProxy.

## Остановка и rollback

```bash
docker compose --profile cliproxy \
  -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml stop cliproxy
```

Чтобы вернуть основной OpenAI путь, уберите `JOB_FTCH_LLM_GATEWAY=cliproxy` и
верните обычный `JOB_FTCH_OPENAI_BASE_URL`, затем перезапустите bot. Не удаляйте
`deploy/cliproxy/auth`, если планируете восстановить CLIProxy без повторного
OAuth.
