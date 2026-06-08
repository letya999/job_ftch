# E2E Dataset & Test Plan — AI Jobs RU/KZ

Date: 2026-06-08
Branch: phase-17-21 (already checked out)

## Goal

Implement a 4-level E2E test suite covering real AI-hiring sources in Russia and Kazakhstan.
No code to be written inline — all files listed below must be created/modified.

## Architecture Context

- Hexagonal: domain/ <- application/ <- infrastructure/ (no reverse imports)
- Source types: rss_feed, rest_api (SuperJob), telegram_realtime
- Store: InMemoryStore for tests (no external DB required)
- Test markers: @pytest.mark.e2e, @pytest.mark.network — must be skippable in CI
- Existing fixtures: fixtures/feeds/sample_feed.xml, fixtures/api/greenhouse_sample.json
- Existing tests: tests/test_phase21_rss.py, tests/test_phase17_scheduler.py (do not regress)
- pytest-asyncio mode: asyncio (configured in pyproject.toml)

## Rate Limits & Safety Rules (embed as comments in test code)

### RSS (habr.com)
- 1 HTTP request per feed URL
- No pagination — single fetch returns 20-50 items
- Minimum interval between fetches: 300s (enforced by scheduler, not tested here)
- Timeout per request: 20s
- Test: fetch once, assert >= 1 RawItem

### SuperJob API
- Base URL: https://api.superjob.ru/2.0
- Rate limit: use max 1 req/2s (official allows 5/s, we stay conservative)
- Items per page: 20, max pages in test: 1
- Requires env var: SUPERJOB_API_KEY (skip test if not set via pytest.importorskip pattern)
- Timeout per request: 15s

### Telegram
- Use Telethon iter_messages(limit=50) — do NOT use get_messages in a loop
- asyncio.sleep(1.5) between channels
- Catch FloodWaitError: sleep(e.seconds + 10), log, skip that channel
- Do NOT join channels in tests — read-only access to public channels only
- One TelegramClient instance shared across all TG source tests
- Requires env vars: TG_API_ID, TG_API_HASH, TG_SESSION_STRING (skip if absent)
- Timeout: 10s per channel, stop_event after 5s for realtime source

## Files to Create

### 1. Fixture: fixtures/specs/rss_habr_ml.yaml
RSS spec for Habr Career ML jobs query.
Content:
```yaml
type: rss_feed
feed_url: "https://career.habr.com/vacancies/rss?q=machine+learning&type=1"
incremental: true
source_name: habr_ml
```

### 2. Fixture: fixtures/specs/rss_habr_ds.yaml
```yaml
type: rss_feed
feed_url: "https://career.habr.com/vacancies/rss?q=data+scientist&type=1"
incremental: true
source_name: habr_ds
```

### 3. Fixture: fixtures/specs/rss_habr_ai.yaml
```yaml
type: rss_feed
feed_url: "https://career.habr.com/vacancies/rss?q=%D0%B8%D1%81%D0%BA%D1%83%D1%81%D1%81%D1%82%D0%B2%D0%B5%D0%BD%D0%BD%D1%8B%D0%B9+%D0%B8%D0%BD%D1%82%D0%B5%D0%BB%D0%BB%D0%B5%D0%BA%D1%82&type=1"
incremental: true
source_name: habr_ai
```

### 4. Fixture: fixtures/specs/superjob_ml.yaml
REST API spec for SuperJob ML query. auth_source_id references env provider.
```yaml
type: rest_api
base_url: "https://api.superjob.ru/2.0"
jobs_endpoint: "/vacancies/"
params:
  keyword: "machine learning"
  count: "20"
  page: "0"
headers:
  X-Api-App-Id: "__from_auth__"
field_map:
  title: profession
  url: link
  company: firm_name
  location: town.title
  description: candidat
incremental_cursor_field: id
auth_source_id: superjob
source_name: superjob_ml
```

### 5. Fixture: fixtures/specs/telegram_getmatch.yaml
```yaml
type: telegram_realtime
entity: "@getmatch"
source_name: tg_getmatch
auth_source_id: telegram
```

### 6. Fixture: fixtures/specs/telegram_habr_career.yaml
```yaml
type: telegram_realtime
entity: "@habr_career"
source_name: tg_habr_career
auth_source_id: telegram
```

### 7. Fixture: fixtures/feeds/habr_ml_sample.xml
A realistic multi-item RSS XML fixture representing Habr Career ML vacancy feed.
Must have 5 items, each with:
- <title> containing realistic Russian AI/ML job titles
- <link> as full https://career.habr.com URL
- <description> with job summary in Russian
- <guid> unique per item
- <pubDate> in RFC 822 format

Example titles to use:
- "ML Engineer (NLP) в Сбер"
- "Data Scientist — рекомендательные системы — Яндекс"
- "Python Developer / ML в СберТех"
- "AI Product Manager — VK"
- "Исследователь ML / Deep Learning — Тинькофф"

### 8. Fixture: fixtures/api/superjob_sample.json
Realistic SuperJob API response JSON for vacancies endpoint.
Structure matches actual SuperJob v2 API:
```json
{
  "objects": [
    {
      "id": 12345678,
      "profession": "ML Engineer",
      "firm_name": "ООО Технологии",
      "link": "https://www.superjob.ru/vakansii/ml-engineer-12345678.html",
      "town": {"id": 4, "title": "Москва"},
      "candidat": "Разработка ML-моделей для рекомендательных систем...",
      "date_published": 1749340800
    },
    {
      "id": 12345679,
      "profession": "Data Scientist",
      "firm_name": "AI Solutions",
      "link": "https://www.superjob.ru/vakansii/data-scientist-12345679.html",
      "town": {"id": 4, "title": "Москва"},
      "candidat": "Построение предсказательных моделей на Python, sklearn, torch.",
      "date_published": 1749254400
    },
    {
      "id": 12345680,
      "profession": "MLOps инженер",
      "firm_name": "FinTech Corp",
      "link": "https://www.superjob.ru/vakansii/mlops-12345680.html",
      "town": {"id": 4, "title": "Санкт-Петербург"},
      "candidat": "Поддержка ML-платформы: Kubernetes, MLflow, Airflow.",
      "date_published": 1749168000
    }
  ],
  "total": 3,
  "more": false,
  "request_id": "test-request-id-001"
}
```

### 9. Fixture: fixtures/tg_messages/getmatch_sample.json
Array of 5 realistic Telegram message dicts for unit testing TelegramRealtimeSource
without real Telegram connection. Each message:
```json
[
  {
    "id": 10001,
    "message": "ML Engineer | Яндекс | Москва | до 400k\nPython, PyTorch, рекомендательные системы\nhttps://getmatch.ru/v/12345",
    "date": "2026-06-08T10:00:00+00:00",
    "peer_id": {"channel_id": 123456789}
  },
  ... 4 more with different AI/ML roles, salaries, companies
]
```
Companies to include: Яндекс, Сбер, VK, Тинькофф, Озон
Roles: ML Engineer, Data Scientist, NLP Engineer, CV Engineer, AI Product Manager

### 10. tests/e2e/__init__.py
Empty file.

### 11. tests/e2e/conftest.py
Pytest configuration for e2e tests:
- Register custom markers: e2e, network, telegram, superjob
- Add CLI option: --run-network (default: False). Network tests skip unless this flag passed.
- Add CLI option: --run-telegram. Telegram tests skip unless this flag AND TG env vars set.
- Fixture: `in_memory_store` — returns InMemoryStore instance (loaded from registry)
- Fixture: `habr_ml_rss_xml` — reads fixtures/feeds/habr_ml_sample.xml as string
- Fixture: `superjob_json` — reads fixtures/api/superjob_sample.json as dict
- Fixture: `tg_messages_json` — reads fixtures/tg_messages/getmatch_sample.json as list
- All fixtures must be async-safe (use anyio_backend = "asyncio")

### 12. tests/e2e/test_level0_specs.py
Level 0 — zero network, pure validation.
Tests (all sync or trivially async):
1. test_rss_habr_ml_spec_parses — load fixtures/specs/rss_habr_ml.yaml, parse via RSSFeedSourceSpec, assert type=="rss_feed", feed_url contains "habr.com"
2. test_rss_habr_ds_spec_parses — same for ds
3. test_rss_habr_ai_spec_parses — same for ai
4. test_superjob_spec_parses — load superjob_ml.yaml, parse via RestAPISourceSpec, assert base_url contains "superjob"
5. test_telegram_getmatch_spec_parses — load telegram_getmatch.yaml, parse via TelegramRealtimeSourceSpec, assert entity=="@getmatch"
6. test_all_spec_types_in_union — instantiate one of each source spec type, serialize to dict, deserialize back, assert type field survives round-trip (parametrized over all 10 spec types using Literal values from SourceSpec union)

Import path for specs: from domain.source_spec import RSSFeedSourceSpec, RestAPISourceSpec, TelegramRealtimeSourceSpec, SourceSpec

### 13. tests/e2e/test_level1_rss.py
Level 1 — RSS sources. Marked @pytest.mark.network, skipped unless --run-network.
Uses httpx to fetch real URLs. Uses in_memory_store fixture.

Tests:
1. test_habr_ml_rss_returns_items
   - Create RSSFeedSource(spec=RSSFeedSourceSpec from rss_habr_ml.yaml, auth=NullAuth, store=in_memory_store)
   - Call fetch() and collect all items (stop after 30 items max)
   - Assert len(items) >= 1
   - Assert all items are RawItem instances
   - Assert item.url is not None for each
   - Assert item.external_id is not None and non-empty
   - Assert all external_ids are unique within batch
   - Timeout: 20s via asyncio.wait_for

2. test_habr_ds_rss_returns_items
   - Same for ds feed

3. test_habr_rss_incremental_dedup
   - Fetch habr_ml twice with same store
   - Second fetch: assert 0 new items (all seen_ids already in store)
   - Check store.get_run_state("rss_feed:habr_ml:seen_ids") is not None after first fetch

4. test_habr_rss_fixture_roundtrip (no network)
   - Use habr_ml_rss_xml fixture (local XML)
   - Mock httpx.AsyncClient.get to return fixture content
   - Assert RSSFeedSource.fetch() yields >= 5 items from fixture
   - Assert item.text is not empty
   - This test runs WITHOUT --run-network flag

Import: from infrastructure.sources.realtime.rss import RSSFeedSource
        from domain.source_spec import RSSFeedSourceSpec

### 14. tests/e2e/test_level1_superjob.py
Level 1 — SuperJob API. Marked @pytest.mark.network @pytest.mark.superjob.
Skip unless --run-network AND env var SUPERJOB_API_KEY is set.

Tests:
1. test_superjob_fixture_field_mapping (no network)
   - Use superjob_json fixture
   - Mock httpx response with fixture data
   - Create OfficialAPISource with superjob_ml.yaml spec
   - Assert items have title, url, company from field_map
   - Assert item.external_id == str(json_item["id"])

2. test_superjob_live_fetch (network, requires SUPERJOB_API_KEY)
   - Skip if os.environ.get("SUPERJOB_API_KEY") is None
   - Create real OfficialAPISource with auth from env
   - Fetch 1 page (20 items max)
   - Assert len(items) >= 1
   - Assert each item.title is not empty
   - Timeout: 15s

Import: from infrastructure.sources.api.base import OfficialAPISource
        from domain.source_spec import RestAPISourceSpec

### 15. tests/e2e/test_level1_telegram.py
Level 1 — Telegram. Marked @pytest.mark.telegram.
Skip unless --run-telegram AND TG_API_ID + TG_API_HASH + TG_SESSION_STRING are set.

Tests:
1. test_telegram_fixture_unit (no network)
   - Use tg_messages_json fixture
   - Mock Telethon client iter_messages to return fixture messages
   - Create TelegramRealtimeSource, call fetch(), stop after all fixture messages consumed
   - Assert len(items) >= 5
   - Assert each item.text is not empty
   - Assert each item.source_name == "tg_getmatch"

2. test_telegram_getmatch_live (network+telegram)
   - Skip if TG env vars not set
   - Create real TelegramClient from session string
   - Fetch last 30 messages from @getmatch
   - Assert len(items) >= 1
   - Assert no FloodWaitError was raised
   - Timeout: 10s via asyncio.wait_for
   - asyncio.sleep(1.5) between any multi-channel test runs

3. test_telegram_habr_career_live (network+telegram)
   - Same for @habr_career channel
   - asyncio.sleep(1.5) after previous test (conftest session-level sleep via autouse fixture)

Import: from infrastructure.sources.telegram_realtime import TelegramRealtimeSource
        from domain.source_spec import TelegramRealtimeSourceSpec

### 16. tests/e2e/test_level2_pipeline.py
Level 2 — full pipeline integration on RSS fixture data (no network).
Uses mocked httpx returning habr_ml_sample.xml.

Tests:
1. test_pipeline_rss_to_json_sink
   - Config: RSSFeedSource -> [SanitizeNode, GroupByTitleNode] -> JSONFileSink (tempfile)
   - Run full pipeline via application.pipeline.run_pipeline() or equivalent
   - Assert output JSON file created and non-empty
   - Assert >= 1 job in output
   - Assert job["title"] is not empty string
   - Assert job["stable_id"] present and unique per job

2. test_pipeline_dedup_second_run
   - Run same pipeline twice with same InMemoryStore
   - First run: N jobs output
   - Second run: 0 jobs output (all deduped via seen_ids)
   - Assert store contains seen_ids after first run

3. test_pipeline_sanitize_node_is_first
   - Build a pipeline with SanitizeNode not first
   - Assert ValueError or similar is raised during construction/validation
   - This enforces the invariant from AGENTS.md

Import existing pipeline runner from application/ — check what exists in app.py or application/pipeline.py
Use load_extensions() to ensure registry is populated before test

### 17. tests/e2e/test_level3_scheduler.py
Level 3 — scheduler smoke. No network.
Uses local_fixture source.

Tests:
1. test_scheduler_ticks_and_stops
   - Create Settings with source_backend="local_fixture", schedule_interval_seconds=1
   - Create Scheduler instance
   - Run scheduler.run_forever() as asyncio background task
   - Wait 3s
   - Call scheduler.stop()
   - Assert scheduler ran >= 2 pipeline cycles (count via mock/counter on pipeline call)
   - Assert PID file created during run and deleted after stop (check .runtime/.pid)
   - Timeout: 10s total

2. test_scheduler_signal_stop
   - Start scheduler
   - Send SIGINT via os.kill(os.getpid(), signal.SIGINT)
   - Assert scheduler stops gracefully within 3s
   - Skip on Windows if SIGINT not supported the same way (use scheduler.stop() as fallback)

Import: from application.scheduler import Scheduler

## Files to Modify

### pyproject.toml
Add under [tool.pytest.ini_options] markers section:
```
"e2e: marks tests as end-to-end (deselect with -m 'not e2e')",
"network: marks tests requiring real network access",
"telegram: marks tests requiring Telegram credentials",
"superjob: marks tests requiring SUPERJOB_API_KEY",
```

If markers section does not exist, add it.

## Constraints

1. All new test files: use `from __future__ import annotations`
2. All async tests: decorated with @pytest.mark.asyncio
3. Type hints on all functions: required (mypy strict)
4. No hardcoded credentials anywhere — use os.environ.get() with pytest.skip()
5. Fixture YAML files: valid YAML, parseable by pydantic SourceSpec union
6. Fixture JSON/XML files: realistic but fake data (no real personal data)
7. NullAuth for tests: use the _NullAuthProvider already in application/registry.py (import or replicate as local class)
8. Do not modify existing test files (test_phase*.py, test_*.py in tests/)
9. Run quality gates after implementation:
   - uv run ruff format .
   - uv run ruff check .
   - uv run mypy .
   - uv run pytest tests/e2e/test_level0_specs.py tests/e2e/test_level1_rss.py::test_habr_rss_fixture_roundtrip tests/e2e/test_level1_superjob.py::test_superjob_fixture_field_mapping tests/e2e/test_level1_telegram.py::test_telegram_fixture_unit tests/e2e/test_level2_pipeline.py tests/e2e/test_level3_scheduler.py -v
   - uv run pytest tests/ -v --ignore=tests/e2e (full regression, no network)
10. Network tests must be skipped when run without --run-network flag (not just xfail — actual skip)
