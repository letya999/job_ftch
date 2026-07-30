from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_ftch.infrastructure.stores.in_memory import InMemoryStore


class NullAuth:
    def resolve(self, source_id: str) -> dict[str, str]:
        return {}


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
