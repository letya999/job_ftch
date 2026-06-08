from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from domain.source_spec import (
    RestAPISourceSpec,
    RSSFeedSourceSpec,
    SourceSpec,
)


@pytest.fixture
def specs_dir() -> Path:
    return Path(__file__).parent.parent.parent / "fixtures" / "specs"


def test_rss_habr_ml_spec_parses(specs_dir: Path) -> None:
    path = specs_dir / "rss_habr_ml.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    spec = RSSFeedSourceSpec(**data)
    assert spec.type == "rss_feed"
    assert "habr.com" in str(spec.feed_url)
    assert spec.source_name == "habr_ml"


def test_rss_habr_ds_spec_parses(specs_dir: Path) -> None:
    path = specs_dir / "rss_habr_ds.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    spec = RSSFeedSourceSpec(**data)
    assert spec.type == "rss_feed"
    assert "habr.com" in str(spec.feed_url)
    assert spec.source_name == "habr_ds"


def test_rss_habr_ai_spec_parses(specs_dir: Path) -> None:
    path = specs_dir / "rss_habr_ai.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    spec = RSSFeedSourceSpec(**data)
    assert spec.type == "rss_feed"
    assert "habr.com" in str(spec.feed_url)
    assert spec.source_name == "habr_ai"


def test_superjob_spec_parses(specs_dir: Path) -> None:
    path = specs_dir / "superjob_ml.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    spec = RestAPISourceSpec(**data)
    assert spec.type == "rest_api"
    assert "superjob" in str(spec.base_url)
    assert spec.source_name == "superjob_ml"


def test_telegram_getmatch_spec_parses(specs_dir: Path) -> None:
    path = specs_dir / "telegram_getmatch.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    from domain.source_spec import TelegramChannelSpec

    spec = TelegramChannelSpec(**data)
    assert spec.type == "telegram_channel"
    assert spec.entity == "@getmatch"
    assert spec.source_name == "tg_getmatch"


@pytest.mark.parametrize(
    "spec_file",
    [
        "rss_habr_ml.yaml",
        "rss_habr_ds.yaml",
        "rss_habr_ai.yaml",
        "superjob_ml.yaml",
        "telegram_getmatch.yaml",
        "telegram_habr_career.yaml",
    ],
)
def test_all_spec_types_in_union(specs_dir: Path, spec_file: str) -> None:
    path = specs_dir / spec_file
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # Round-trip through SourceSpec union

    # Since SourceSpec is likely a TypeAlias or Union, we might need to use TypeAdapter in Pydantic v2
    # or just parse_obj if it's a RootModel or similar.
    # Assuming SourceSpec is a Union of Pydantic models.
    from pydantic import TypeAdapter

    adapter = TypeAdapter(SourceSpec)
    spec = adapter.validate_python(data)

    assert spec.type == data["type"]
    assert spec.source_name == data["source_name"]

    # Check serialization
    dumped = adapter.dump_python(spec)
    assert dumped["type"] == data["type"]
    assert dumped["source_name"] == data["source_name"]
