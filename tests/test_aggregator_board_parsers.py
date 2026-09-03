from __future__ import annotations

from dataclasses import dataclass

import pytest

from job_ftch.application.registry import resolve_site_parser, resolve_site_parser_by_name
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.aggregator_boards import (
    AgileFluentParser,
    AIJobsAiParser,
    AIJobsComParser,
    AIJobsParser,
    QuickOfferParser,
)


@dataclass
class _Response:
    text: str
    url: str
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


@dataclass
class _JsonResponse(_Response):
    payload: object = None

    def json(self) -> object:
        return self.payload


class _Client:
    def __init__(self, responses: dict[str, _Response], *, fail: set[str] = ()) -> None:
        self.responses = responses
        self.fail = fail

    async def post(self, url: str, *, json: object, follow_redirects: bool = True) -> _JsonResponse:
        del json, follow_redirects
        return self.responses[url]  # type: ignore[return-value]

    async def get(self, url: str, **_: object) -> _Response:
        if url in self.fail:
            raise RuntimeError("origin unavailable")
        return self.responses[url]


def test_all_requested_aggregators_are_registered() -> None:
    for name, url in {
        "agilefluent": "https://jobboard.agilefluent.ru/",
        "quick_offer": "https://quick-offer.ru/jobs/saved/ai-engineering",
        "djinni": "https://djinni.co/jobs/keyword-ml_ai/",
        "aijobs": "https://aijobs.net/",
        "aijobs_com": "https://www.aijobs.com/jobs",
        "aijobs_ai": "https://aijobs.ai/jobs",
        "ai_engineer": "https://ai.engineer/",
        "remote_rocketship": "https://www.remoterocketship.com/jobs/llm-engineer/",
    }.items():
        assert resolve_site_parser_by_name(name) is not None
        assert resolve_site_parser(url) is not None
    assert isinstance(resolve_site_parser("https://foorilla.com/hiring/"), AIJobsParser)
    assert isinstance(resolve_site_parser("https://www.aijobs.com/jobs"), AIJobsComParser)
    assert isinstance(resolve_site_parser("https://aijobs.ai/jobs"), AIJobsAiParser)


def test_search_builders_keep_target_role_on_supported_aggregators() -> None:
    assert AgileFluentParser().build_search_urls(
        "https://jobboard.agilefluent.ru/", ["AI Engineer"]
    ) == ["https://jobboard.agilefluent.ru/"]
    assert AIJobsParser().build_search_urls("https://aijobs.net/", ["LLM Engineer"]) == [
        "https://foorilla.com/hiring/jobs/"
    ]
    assert "q=LLM+Engineer" in AIJobsComParser().build_search_urls(
        "https://www.aijobs.com/jobs", ["LLM Engineer"]
    )[0]
    assert "keyword=LLM+Engineer" in AIJobsAiParser().build_search_urls(
        "https://aijobs.ai/jobs", ["LLM Engineer"]
    )[0]
    assert (
        QuickOfferParser().build_search_urls("https://quick-offer.ru/jobs", ["AI Engineer"]) == []
    )


@pytest.mark.asyncio
async def test_aggregator_detail_follows_external_apply_link() -> None:
    listing = "<a href='/job/role-1'>AI Engineer</a>"
    detail = "<main><h1>AI Engineer</h1><a class='apply' href='https://jobs.example.com/role-1'>Apply</a><p>Build models.</p></main>"
    origin = "<main><h1>AI Engineer</h1><p>Original vacancy text.</p></main>"
    client = _Client(
        {
            "https://quick-offer.ru/jobs": _Response(listing, "https://quick-offer.ru/jobs"),
            "https://quick-offer.ru/job/role-1": _Response(
                detail, "https://quick-offer.ru/job/role-1"
            ),
            "https://jobs.example.com/role-1": _Response(origin, "https://jobs.example.com/role-1"),
        }
    )
    spec = CareerSiteSpec(url="https://quick-offer.ru/jobs", source_name="quick", limit=1)

    items = [item async for item in QuickOfferParser().parse(spec, client)]

    assert len(items) == 1
    assert str(items[0].url) == "https://jobs.example.com/role-1"
    assert items[0].metadata["aggregator_url"] == "https://quick-offer.ru/job/role-1"
    assert items[0].metadata["origin_fetched"] is True
    assert "Original vacancy text." in items[0].text


@pytest.mark.asyncio
async def test_aggregator_falls_back_to_aggregator_detail_when_origin_fails() -> None:
    listing = "<a href='/job/role-1'>AI Engineer</a>"
    detail = "<main><h1>AI Engineer</h1><p>Aggregator copy.</p><a href='https://jobs.example.com/role-1'>Apply</a></main>"
    client = _Client(
        {
            "https://quick-offer.ru/jobs": _Response(listing, "https://quick-offer.ru/jobs"),
            "https://quick-offer.ru/job/role-1": _Response(
                detail, "https://quick-offer.ru/job/role-1"
            ),
        },
        fail={"https://jobs.example.com/role-1"},
    )
    spec = CareerSiteSpec(url="https://quick-offer.ru/jobs", source_name="quick", limit=1)

    items = [item async for item in QuickOfferParser().parse(spec, client)]

    assert len(items) == 1
    assert str(items[0].url) == "https://quick-offer.ru/job/role-1"
    assert items[0].metadata["origin_url"] == "https://jobs.example.com/role-1"
    assert items[0].metadata["origin_fetched"] is False
    assert "Aggregator copy." in items[0].text


@pytest.mark.asyncio
async def test_agilefluent_uses_api_rows_without_following_origin() -> None:
    row = {
        "id": 42,
        "title": "AI Engineer",
        "description": "Build models.",
        "url": "https://jobs.example.com/role-42",
    }
    client = _Client(
        {
            "https://jobboard.agilefluent.ru/api/jobs/search": _JsonResponse(
                "",
                "https://jobboard.agilefluent.ru/api/jobs/search",
                payload={"data": [row], "hasMore": False},
            ),
        }
    )
    spec = CareerSiteSpec(
        url="https://jobboard.agilefluent.ru/",
        source_name="agile",
        limit=1,
        monitor_config={"_search_keywords": ["AI Engineer"]},
    )

    items = [item async for item in AgileFluentParser().parse(spec, client)]

    assert len(items) == 1
    assert str(items[0].url).endswith("/api/jobs/42")
    assert items[0].metadata["origin_fetched"] is False
    assert "Build models." in items[0].text


@pytest.mark.asyncio
async def test_foorilla_parses_htmx_job_cards() -> None:
    listing = """
    <li class="list-group-item">
      <a class="stretched-link" hx-get="/hiring/jobs/llm-engineer-remote-3580634/">
        LLM Engineer
      </a>
    </li>
    """
    client = _Client(
        {"https://foorilla.com/hiring/jobs/?job_search=LLM+Engineer": _Response(listing, "")}
    )
    spec = CareerSiteSpec(
        url="https://foorilla.com/hiring/",
        source_name="foorilla",
        limit=5,
        monitor_config={"_search_keywords": ["LLM Engineer"]},
    )

    items = [item async for item in AIJobsParser().parse(spec, client)]

    assert len(items) == 1
    assert str(items[0].url) == "https://foorilla.com/hiring/jobs/llm-engineer-remote-3580634/"
    assert items[0].external_id == "3580634"


@pytest.mark.asyncio
async def test_aijobs_com_parses_numeric_job_cards() -> None:
    listing = '<a href="/jobs/609998104-applied-ai-engineer">Applied AI Engineer</a>'
    client = _Client({"https://www.aijobs.com/jobs?q=LLM+Engineer": _Response(listing, "")})
    spec = CareerSiteSpec(
        url="https://www.aijobs.com/jobs?q=LLM+Engineer",
        source_name="aijobs_com",
        limit=5,
        monitor_config={"_search_keywords": ["LLM Engineer"]},
    )

    items = [item async for item in AIJobsComParser().parse(spec, client)]

    assert [item.external_id for item in items] == ["609998104"]


@pytest.mark.asyncio
async def test_aijobs_ai_parses_job_slug_cards() -> None:
    listing = '<a href="/job/ai-agent-engineer-18">AI Agent Engineer</a>'
    client = _Client({"https://aijobs.ai/jobs?keyword=LLM+Engineer": _Response(listing, "")})
    spec = CareerSiteSpec(
        url="https://aijobs.ai/jobs?keyword=LLM+Engineer",
        source_name="aijobs_ai",
        limit=5,
        monitor_config={"_search_keywords": ["LLM Engineer"]},
    )

    items = [item async for item in AIJobsAiParser().parse(spec, client)]

    assert [item.external_id for item in items] == ["ai-agent-engineer-18"]
