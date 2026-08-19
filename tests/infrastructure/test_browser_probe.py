from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from job_ftch.infrastructure import browser_probe as probe_mod


class _FakePage:
    url = "https://example.com/jobs/list"
    html = '<a href="/jobs">All</a><a href="/jobs/1">Engineer</a>'

    async def title(self) -> str:
        return "Open roles"

    async def wait_for_selector(self, selector: str, timeout: int = 0) -> None:
        del selector, timeout

    async def content(self) -> str:
        return self.html

    async def evaluate(self, script: str, max_items: int) -> list[dict[str, str]]:
        del script
        return [
            {"url": "https://example.com/jobs/1", "title": "Engineer"},
            {"url": "https://example.com/jobs/2", "title": "Analyst"},
        ][:max_items]


@pytest.mark.asyncio
async def test_probe_listing_returns_public_previews(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage()

    @asynccontextmanager
    async def fake_open_page(config: dict[str, Any], *, bypass_strategy: Any = None):
        del config, bypass_strategy
        yield page

    async def fake_navigate(opened: Any, url: str, config: dict[str, Any]) -> None:
        del opened, url, config

    async def fake_ssrf(url: str) -> None:
        del url

    monkeypatch.setattr(probe_mod, "open_page", fake_open_page)
    monkeypatch.setattr(probe_mod, "navigate", fake_navigate)
    monkeypatch.setattr(probe_mod, "check_ssrf", fake_ssrf)
    monkeypatch.setattr(probe_mod, "resolve_bypass", lambda name, config=None: object())

    payload = await probe_mod.probe_listing(
        url="https://example.com/jobs/list",
        engine="auto",
        max_items=1,
    )
    assert payload["ok"] is True
    assert payload["executed"] is True
    assert payload["engine"] == "patchright_browser"
    assert payload["page_title"] == "Open roles"
    assert payload["item_count"] == 1
    assert payload["items"] == [{"url": "https://example.com/jobs/1", "title": "Engineer"}]


@pytest.mark.asyncio
async def test_probe_listing_unknown_engine_is_unavailable() -> None:
    payload = await probe_mod.probe_listing(url="https://example.com/jobs", engine="missing_engine")
    assert payload["status"] == "unavailable"
    assert payload["executed"] is False
    assert payload["error"] == "engine_unavailable"


@pytest.mark.asyncio
async def test_probe_listing_ssrf_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_ssrf(url: str) -> None:
        del url
        raise httpx.LocalProtocolError("SSRF guard blocked request to private host '127.0.0.1'")

    monkeypatch.setattr(probe_mod, "check_ssrf", fake_ssrf)
    monkeypatch.setattr(probe_mod, "resolve_bypass", lambda name, config=None: object())

    payload = await probe_mod.probe_listing(url="http://127.0.0.1/jobs", engine="patchright")
    assert payload["status"] == "error"
    assert payload["error"] == "ssrf_blocked"
    assert payload["executed"] is False


@pytest.mark.asyncio
async def test_probe_listing_missing_patchright_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ssrf(url: str) -> None:
        del url

    @asynccontextmanager
    async def fake_open_page(config: dict[str, Any], *, bypass_strategy: Any = None):
        del config, bypass_strategy
        raise RuntimeError("patchright is required for browser-backed scraping")
        yield None  # pragma: no cover

    monkeypatch.setattr(probe_mod, "check_ssrf", fake_ssrf)
    monkeypatch.setattr(probe_mod, "open_page", fake_open_page)
    monkeypatch.setattr(probe_mod, "resolve_bypass", lambda name, config=None: object())

    payload = await probe_mod.probe_listing(url="https://example.com/jobs", engine="patchright")
    assert payload["status"] == "unavailable"
    assert payload["error"] == "browser_runtime_missing"
    assert payload["executed"] is False


def test_html_extract_keeps_only_detail_urls() -> None:
    html = """
    <a href="/vacancies?s=menu">Вакансии</a>
    <a href="https://getmatch.ru/salaries">Зарплаты</a>
    <a href="/vacancies/12345-ml-engineer">ML Engineer</a>
    """
    items = probe_mod._items_from_html(html, page_url="https://getmatch.ru/vacancies", max_items=5)
    assert items == [{"url": "https://getmatch.ru/vacancies/12345-ml-engineer", "title": ""}]


@pytest.mark.asyncio
async def test_probe_listing_challenge_without_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _EmptyPage(_FakePage):
        async def evaluate(self, script: str, max_items: int) -> list[dict[str, str]]:
            del script, max_items
            return [{"url": "https://example.com/jobs", "title": "All jobs"}]

        async def content(self) -> str:
            return '<a href="/jobs">All jobs</a>'

    class _ChallengeBypass:
        observed_challenge_type = None

        def set_observed_challenge_type(self, challenge_type: str | None) -> None:
            self.observed_challenge_type = challenge_type

    bypass = _ChallengeBypass()

    @asynccontextmanager
    async def fake_open_page(config: dict[str, Any], *, bypass_strategy: Any = None):
        del config, bypass_strategy
        yield _EmptyPage()

    async def fake_navigate(opened: Any, url: str, config: dict[str, Any]) -> None:
        del opened, url
        setter = getattr(config.get("_bypass_strategy"), "set_observed_challenge_type", None)
        if callable(setter):
            setter("blocked_fingerprint")

    async def fake_ssrf(url: str) -> None:
        del url

    monkeypatch.setattr(probe_mod, "open_page", fake_open_page)
    monkeypatch.setattr(probe_mod, "navigate", fake_navigate)
    monkeypatch.setattr(probe_mod, "check_ssrf", fake_ssrf)
    monkeypatch.setattr(probe_mod, "resolve_bypass", lambda name, config=None: bypass)

    payload = await probe_mod.probe_listing(url="https://example.com/jobs", engine="patchright")
    assert payload["executed"] is True
    assert payload["ok"] is False
    assert payload["status"] == "challenge"
    assert payload["error"] == "challenge_detected"
    assert payload["challenge"] == "blocked_fingerprint"
    assert payload["items"] == []
