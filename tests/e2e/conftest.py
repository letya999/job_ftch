from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_ftch.infrastructure.stores.in_memory import InMemoryStore


class NullAuth:
    def resolve(self, source_id: str) -> dict[str, str]:
        return {}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help="run tests requiring real network access",
    )
    parser.addoption(
        "--run-telegram",
        action="store_true",
        default=False,
        help="run tests requiring Telegram credentials",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "e2e: marks tests as end-to-end")
    config.addinivalue_line("markers", "network: marks tests requiring real network access")
    config.addinivalue_line("markers", "telegram: marks tests requiring Telegram credentials")
    config.addinivalue_line("markers", "superjob: marks tests requiring SUPERJOB_API_KEY")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_network = config.getoption("--run-network")
    run_telegram = config.getoption("--run-telegram")

    skip_network = pytest.mark.skip(reason="need --run-network option to run")
    skip_telegram = pytest.mark.skip(reason="need --run-telegram option to run")

    for item in items:
        if "network" in item.keywords and not run_network:
            item.add_marker(skip_network)
        if "telegram" in item.keywords and not run_telegram:
            item.add_marker(skip_telegram)


@pytest.fixture
def null_auth() -> NullAuth:
    return NullAuth()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def in_memory_store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def habr_ml_rss_xml() -> str:
    path = Path(__file__).parent.parent.parent / "fixtures" / "feeds" / "habr_ml_sample.xml"
    return path.read_text(encoding="utf-8")


@pytest.fixture
def superjob_json() -> dict:
    path = Path(__file__).parent.parent.parent / "fixtures" / "api" / "superjob_sample.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def tg_messages_json() -> list:
    path = Path(__file__).parent.parent.parent / "fixtures" / "tg_messages" / "getmatch_sample.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)
