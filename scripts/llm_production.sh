#!/bin/sh
# Convenience commands for the optional production queue and Android route.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
dc() {
  docker compose -p "${JOB_FTCH_COMPOSE_PROJECT:-telegram_bot}" \
    --env-file job_ftch/adapters/telegram_bot/.env.prod \
    -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml \
    -f job_ftch/adapters/telegram_bot/docker-compose.llm-queue.yml \
    -f job_ftch/adapters/telegram_bot/docker-compose.home-egress.yml \
    --profile llm-queue --profile home-egress --profile cliproxy --profile public-api "$@"
}
ready() {
  dc exec -T llm-home-egress python -c \
    'import urllib.request; urllib.request.urlopen("http://127.0.0.1:1054/ready", timeout=3).close()'
}
credentials() {
  dc exec -T cliproxy sh -c 'find /root/.cli-proxy-api -maxdepth 1 -type f -name "*.json" | wc -l'
}
case "${1:-status}" in
  status)
    dc ps
    dc exec -T llm-worker python -m job_ftch.infrastructure.llm.dispatch_worker status
    ;;
  phone-link)
    dc logs --no-color llm-home-egress 2>&1 | grep -Eo 'https://login.tailscale.com/a/[a-zA-Z0-9]+' | tail -n 1
    ;;
  callback-target)
    docker inspect "$(dc ps -q cliproxy)" \
      --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}:8085'
    ;;
  login)
    ready
    if [ "$(credentials | tr -d '[:space:]')" != 0 ]; then
      echo 'Expected an empty credential directory before enrolling the replacement account.' >&2
      exit 1
    fi
    echo 'Forward phone localhost:8085 to the callback-target address through SSH.'
    dc exec cliproxy /CLIProxyAPI/CLIProxyAPI -config /CLIProxyAPI/config.yaml \
      -antigravity-login -no-browser -oauth-callback-port 8085
    ;;
  activate)
    ready
    if [ "$(credentials | tr -d '[:space:]')" != 1 ]; then
      echo 'Exactly one authorized account is required.' >&2
      exit 1
    fi
    dc exec -T llm-worker python -m job_ftch.infrastructure.llm.dispatch_worker resume google_primary
    echo 'Account scope resumed; scheduled preflight will validate access.'
    ;;
  compose)
    shift
    dc "$@"
    ;;
  *) echo 'Usage: llm_production.sh {status|phone-link|callback-target|login|activate|compose ...}' >&2; exit 2 ;;
esac
