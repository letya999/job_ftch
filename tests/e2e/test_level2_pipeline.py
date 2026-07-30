from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from job_ftch.application.pipeline import Pipeline
from job_ftch.domain.source_spec import RSSFeedSourceSpec
from job_ftch.infrastructure.llm.heuristic import HeuristicLLMProvider
from job_ftch.infrastructure.ontology.normalizer import get_default_normalizer
from job_ftch.infrastructure.sources.realtime.rss import RSSFeedSource
from job_ftch.nodes.extraction import ExtractionNode
from job_ftch.nodes.job_normalization import TitleCompanyNormalizationNode
from job_ftch.nodes.sanitize import SanitizeNode
from job_ftch.sinks.json_file import JsonFileSink


@pytest.fixture
def temp_json_path(tmp_path: Path) -> Path:
    return tmp_path / "output.json"


@pytest.fixture
def pipeline_setup(
    habr_ml_spec: RSSFeedSourceSpec, in_memory_store: Any, temp_json_path: Path, null_auth: Any
) -> tuple[Pipeline[Any, Any], Any, Path]:
    source = RSSFeedSource(spec=habr_ml_spec, auth=null_auth, store=in_memory_store)
    sanitize = SanitizeNode(allowed_career_site_hosts=("career.habr.com",))
    extraction = ExtractionNode(llm=HeuristicLLMProvider())
    normalization = TitleCompanyNormalizationNode(get_default_normalizer())
    sink = JsonFileSink(output_path=temp_json_path)

    pipeline = Pipeline(
        source=source,
        sanitize_node=sanitize,
        nodes=[extraction, normalization],
        sink=sink,
        store=in_memory_store,
    )
    return pipeline, in_memory_store, temp_json_path


@pytest.mark.anyio
async def test_pipeline_rss_to_json_sink(
    pipeline_setup: tuple[Pipeline[Any, Any], Any, Path],
    habr_ml_rss_xml: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("feedparser")
    pipeline, _, temp_json_path = pipeline_setup

    # Mock httpx.AsyncClient.get
    class MockResponse:
        def __init__(self, text: str):
            self.text = text
            self.status_code = 200

        def raise_for_status(self) -> None:
            pass

    async def mock_get(*args: object, **kwargs: object) -> MockResponse:
        del args, kwargs
        return MockResponse(habr_ml_rss_xml)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    summary = await pipeline.run()

    assert summary.fetched == 5
    assert summary.emitted == 5
    assert temp_json_path.exists()

    with temp_json_path.open(encoding="utf-8") as f:
        data = json.load(f)

    assert len(data) == 5
    assert all("title" in job for job in data)
    assert all(job["stable_id"] for job in data)


@pytest.mark.anyio
async def test_pipeline_dedup_second_run(
    pipeline_setup: tuple[Pipeline[Any, Any], Any, Path],
    habr_ml_rss_xml: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, store, _ = pipeline_setup

    class MockResponse:
        def __init__(self, text: str):
            self.text = text
            self.status_code = 200

        def raise_for_status(self) -> None:
            pass

    async def mock_get(*args: object, **kwargs: object) -> MockResponse:
        del args, kwargs
        return MockResponse(habr_ml_rss_xml)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    # First run
    await pipeline.run()

    # Second run
    summary = await pipeline.run()
    # RSS source uses its own incremental state tracking and will skip the seen IDs
    assert summary.fetched == 0
    assert summary.emitted == 0


def test_pipeline_sanitize_node_is_first() -> None:
    with pytest.raises(TypeError, match="must be a SanitizeNode"):
        Pipeline(object(), object(), [], object(), object())


@pytest.fixture
def habr_ml_spec() -> RSSFeedSourceSpec:
    path = Path(__file__).parent.parent.parent / "fixtures" / "specs" / "rss_habr_ml.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return RSSFeedSourceSpec(**data)
