# Local command surface. GitHub Actions may stay explicit; these targets are for
# humans and agents running checks from the workstation.

scripts := "scripts"
docs_scripts := "docs_scripts"

export PYTHONUTF8 := "1"

default:
    @just --list --unsorted

setup-docs:
    uv pip install --reinstall-package markupsafe markupsafe
    uv pip install -r {{docs_scripts}}/requirements.txt

docs-verify:
    uv run python {{scripts}}/build_index_docs.py --check
    uv run python {{scripts}}/lint_docs.py
    uv run python {{scripts}}/check_docs_generated.py
    uv run mkdocs build --strict -f {{docs_scripts}}/mkdocs.yml

docs-build:
    uv run mkdocs build --strict -f {{docs_scripts}}/mkdocs.yml

code-verify:
    uv run python {{scripts}}/run_ci_checks.py lint
    uv run python {{scripts}}/run_ci_checks.py type

architecture-verify:
    uv run python {{scripts}}/run_ci_checks.py architecture

security-verify:
    uv run python {{scripts}}/run_ci_checks.py security
    uv run python {{scripts}}/run_ci_checks.py repo-safety

tests-smoke:
    uv run python {{scripts}}/run_ci_checks.py test-smoke

tests-all:
    uv run python {{scripts}}/run_ci_checks.py test

tests-path PATH:
    uv run pytest {{PATH}} -q -o addopts="" --tb=short

eval-filtering:
    uv run python {{scripts}}/run_ci_checks.py eval-filtering

eval-ingest:
    python -c "from pathlib import Path; Path('.runtime/runs').mkdir(parents=True, exist_ok=True)"
    uv run python {{scripts}}/run_ingest_batch.py --input fixtures/sources/career_sites_cis_303.yaml --out-json .runtime/runs/ingest_batch_303_direct_urls.json --resume --timeout 120 --hard-cancel-grace 15 --max-items 1 --concurrency 10 --gate --min-success-rate 0.65

eval-publishing:
    uv run python {{scripts}}/run_ci_checks.py eval-publishing

release-checklist:
    git status --short
    uv run python {{scripts}}/run_ci_checks.py lint
    uv run python {{scripts}}/run_ci_checks.py type
    uv run python {{scripts}}/run_ci_checks.py test
    uv run python {{scripts}}/run_ci_checks.py security
    uv run python {{scripts}}/run_ci_checks.py repo-safety
    uv run python {{scripts}}/run_ci_checks.py core-import
    uv run python {{scripts}}/run_ci_checks.py release-contract
    uv run python {{scripts}}/check_docs_generated.py
    uv run mkdocs build --strict -f {{docs_scripts}}/mkdocs.yml

release-tag VERSION:
    just release-checklist
    git tag -a v{{VERSION}} -m "v{{VERSION}}"
    git push origin v{{VERSION}}

docker-dev-verify:
    docker build -f docker/runtime/Dockerfile.dev -t job-ftch-runtime:dev .
    docker compose --env-file .env.dev -f job_ftch/adapters/telegram_bot/docker-compose.dev.yml config
    docker compose --env-file .env.dev -f job_ftch/adapters/telegram_bot/docker-compose.dev.yml up -d --build
    docker compose --env-file .env.dev -f job_ftch/adapters/telegram_bot/docker-compose.dev.yml ps
    docker compose --env-file .env.dev -f job_ftch/adapters/telegram_bot/docker-compose.dev.yml down

docker-prod-verify:
    docker build -f docker/runtime/Dockerfile.prod -t job-ftch-runtime:prod .
    docker compose --env-file job_ftch/adapters/telegram_bot/.env.prod -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml config
    docker compose --env-file job_ftch/adapters/telegram_bot/.env.prod -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml up -d --build
    docker compose --env-file job_ftch/adapters/telegram_bot/.env.prod -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml ps
    docker compose --env-file job_ftch/adapters/telegram_bot/.env.prod -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml down

site-install:
    bun --cwd job_ftch_site install

site-dev:
    bun --cwd job_ftch_site run dev

site-build:
    bun --cwd job_ftch_site run type-check
    bun --cwd job_ftch_site run build

site-docker-dev:
    docker compose --env-file job_ftch_site/.env.dev -f job_ftch_site/docker-compose.dev.yml up --build

site-docker-prod:
    docker compose --env-file job_ftch_site/.env.prod -f job_ftch_site/docker-compose.prod.yml up --build -d

bot-public-api-dev:
    docker compose --env-file job_ftch/adapters/telegram_bot/.env.dev -f job_ftch/adapters/telegram_bot/docker-compose.dev.yml --profile public-api up -d --build postgres qdrant public-api

bot-public-api-prod:
    docker compose --env-file job_ftch/adapters/telegram_bot/.env.prod -f job_ftch/adapters/telegram_bot/docker-compose.prod.yml --profile public-api up -d --build postgres qdrant public-api

site-stack-dev:
    just bot-public-api-dev
    just site-docker-dev
