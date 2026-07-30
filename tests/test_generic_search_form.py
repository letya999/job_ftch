"""Tier-1 generic search-form detection, URL building and probing."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from job_ftch.infrastructure.sources.site_parsers.generic_search import (
    build_generic_search_url,
    count_candidate_job_links,
    detect_search_form,
    discover_working_search_url,
)

BASE = "https://acme.io/vacancies"


def _links(n: int) -> str:
    return "".join(f'<a href="/vacancy/{i}">Job {i}</a>' for i in range(n))


def _page(
    links: int,
    *,
    form: str = '<form method="get" action="/vacancies"><input type="search" name="q"></form>',
) -> str:
    return f"<html><body>{form}{_links(links)}</body></html>"


def test_detect_get_search_form() -> None:
    form = detect_search_form(_page(3), BASE)
    assert form is not None
    assert form.method == "get"
    assert form.query_param == "q"
    assert form.action == "https://acme.io/vacancies"
    assert form.usable_via_url is True


def test_detect_prefers_get_over_post_and_search_input() -> None:
    html = (
        '<form method="post" action="/p"><input type="text" name="email"></form>'
        '<form method="get" action="/search">'
        '<input type="hidden" name="area" value="113">'
        '<input type="text" name="q" placeholder="Search jobs"></form>'
    )
    form = detect_search_form(html, BASE)
    assert form is not None
    assert form.method == "get"
    assert form.query_param == "q"
    assert form.hidden == {"area": "113"}


def test_detect_returns_none_without_search_input() -> None:
    html = '<form method="get" action="/x"><input type="checkbox" name="remote"></form>'
    assert detect_search_form(html, BASE) is None
    assert detect_search_form("<html><body>no form</body></html>", BASE) is None


def test_build_generic_search_url_get_only() -> None:
    form = detect_search_form(_page(0), BASE)
    assert form is not None
    url = build_generic_search_url(form, "AI engineer OR LLM")
    assert url is not None
    assert parse_qs(urlparse(url).query)["q"] == ["AI engineer OR LLM"]
    assert build_generic_search_url(form, "  ") is None  # empty query


def test_build_generic_search_url_rejects_post() -> None:
    html = '<form method="post" action="/s"><input type="search" name="q"></form>'
    form = detect_search_form(html, BASE)
    assert form is not None and form.usable_via_url is False
    assert build_generic_search_url(form, "x") is None


def test_count_candidate_job_links_same_host_only() -> None:
    html = (
        '<a href="/vacancy/100">a</a>'
        '<a href="https://acme.io/jobs/200">b</a>'
        '<a href="https://other.com/vacancy/1">off-host</a>'
        '<a href="/about">not a job</a>'
    )
    assert count_candidate_job_links(html, BASE) == 2


def _make_fetch(*, nonsense: int, combined: int, base: int = 10, form: str | None = None):
    default_form = '<form method="get" action="/vacancies"><input type="search" name="q"></form>'

    async def fetch(url: str) -> str:
        query = parse_qs(urlparse(url).query).get("q", [None])[0]
        f = default_form if form is None else form
        if query is None:
            return _page(base, form=f)
        if "zzqxnonsense" in query:
            return _page(nonsense, form=f)
        return _page(combined, form=f)

    return fetch


@pytest.mark.asyncio
async def test_discover_adopts_filtered_combined_query() -> None:
    fetch = _make_fetch(nonsense=0, combined=3, base=10)
    url = await discover_working_search_url(fetch, BASE, ["AI engineer", "LLM engineer"])
    assert url is not None
    assert parse_qs(urlparse(url).query)["q"] == ["AI engineer OR LLM engineer"]


@pytest.mark.asyncio
async def test_discover_rejects_ignored_query() -> None:
    # nonsense returns ~the full listing => the query param is ignored.
    fetch = _make_fetch(nonsense=10, combined=10, base=10)
    assert await discover_working_search_url(fetch, BASE, ["AI engineer"]) is None


@pytest.mark.asyncio
async def test_discover_returns_none_without_form() -> None:
    async def fetch(url: str) -> str:
        return "<html><body>no form here</body></html>"

    assert await discover_working_search_url(fetch, BASE, ["AI"]) is None


@pytest.mark.asyncio
async def test_discover_skips_post_only_form() -> None:
    post_form = '<form method="post" action="/s"><input type="search" name="q"></form>'
    fetch = _make_fetch(nonsense=0, combined=3, form=post_form)
    assert await discover_working_search_url(fetch, BASE, ["AI"]) is None


@pytest.mark.asyncio
async def test_discover_none_for_empty_keywords() -> None:
    fetch = _make_fetch(nonsense=0, combined=3)
    assert await discover_working_search_url(fetch, BASE, []) is None
