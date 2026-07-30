from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

# These micro-benchmarks depend on optional dev extras. Skip the whole module
# (rather than breaking suite collection) when any are missing.
pytest.importorskip("pytest_benchmark")
Selector = pytest.importorskip("parsel").Selector
from lxml import html  # noqa: E402  (guarded optional dependency)

from job_ftch.infrastructure.sources.career_site_source import (  # noqa: E402
    _parse_source_datetime,
)
from job_ftch.nodes.job_normalization import _parse_compensation_text  # noqa: E402

_HAS_DATEPARSER = importlib.util.find_spec("dateparser") is not None

_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "extraction"
_HTML = (_FIXTURE_DIR / "vacancy_xpath.html").read_text(encoding="utf-8")
_VALUES = (_FIXTURE_DIR / "ingest_values.json").read_text(encoding="utf-8")


def test_lxml_selector_baseline(benchmark) -> None:
    def extract() -> str:
        tree = html.fromstring(_HTML)
        return " ".join(tree.xpath("//article[contains(@class, 'job-description')]//text()"))

    assert "ingestion pipeline" in benchmark(extract)


def test_parsel_selector_baseline(benchmark) -> None:
    def extract() -> str:
        selector = Selector(text=_HTML)
        return " ".join(
            selector.xpath("//article[contains(@class, 'job-description')]//text()").getall()
        )

    assert "ingestion pipeline" in benchmark(extract)


@pytest.mark.skipif(not _HAS_DATEPARSER, reason="dateparser not installed")
def test_multilingual_dateparser_baseline(benchmark) -> None:
    base = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    result = benchmark(_parse_source_datetime, "вчера", relative_base=base)
    assert result == datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def test_compensation_parser_baseline(benchmark) -> None:
    assert benchmark(_parse_compensation_text, "$50k-70k") == (
        "USD",
        50000,
        70000,
    )


def test_stdlib_json_bounded_payload_baseline(benchmark) -> None:
    result = benchmark(json.loads, _VALUES)
    assert result["api_payload"]["data"]["jobs"][0]["title"]
