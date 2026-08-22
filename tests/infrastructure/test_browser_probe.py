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
    assert any("listing_extract=generic" in note for note in payload["notes"])


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
    assert not any("listing_extract=dom_" in note for note in payload["notes"])


@pytest.mark.asyncio
async def test_probe_fingerprint_returns_http_class(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Profile:
        site_class = "SSR"
        recommended_monitors = ["dom"]
        detected_config = {"render": False, "challenge": False, "unsafe_key": "drop-me"}
        canonical_url = None

    async def fake_http(url: str, client: object = None) -> _Profile:
        del url, client
        return _Profile()

    async def fail_live(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        raise AssertionError("headless fingerprint must not open a live page")

    def boom_dns(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("example.com must not be DNS-resolved")

    monkeypatch.setattr(
        "job_ftch.infrastructure.sources.site_fingerprinter.fingerprint",
        fake_http,
    )
    monkeypatch.setattr(probe_mod, "_run_live_probe", fail_live)
    monkeypatch.setattr("job_ftch.infrastructure.network.ssrf_guard.socket.getaddrinfo", boom_dns)

    payload = await probe_mod.probe_fingerprint(url="https://example.com/jobs", engine="auto")
    assert payload["status"] != "not_implemented"
    assert payload["executed"] is True
    assert payload["fingerprint"]["site_class"] == "SSR"
    assert "drop-me" not in str(payload["fingerprint"])
    assert "unsafe_key" not in str(payload["fingerprint"])


@pytest.mark.asyncio
async def test_probe_custom_safe_uses_listing(monkeypatch: pytest.MonkeyPatch) -> None:
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

    payload = await probe_mod.probe_custom_safe(url="https://example.com/jobs/list", engine="auto")
    assert payload["status"] != "not_implemented"
    assert payload["executed"] is True
    assert payload["probe"] == "custom_safe"
    assert any("custom_safe" in note for note in payload["notes"])


@pytest.mark.asyncio
async def test_probe_detail_returns_text_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DetailPage(_FakePage):
        async def evaluate(self, script: str, arg: object = None) -> object:
            del arg
            if "innerText" in script:
                return "We are hiring an engineer"
            if "h1" in script:
                return "Engineer"
            return [{"url": "https://example.com/jobs/1", "title": "Engineer"}]

    @asynccontextmanager
    async def fake_open_page(config: dict[str, Any], *, bypass_strategy: Any = None):
        del config, bypass_strategy
        yield _DetailPage()

    async def fake_navigate(opened: Any, url: str, config: dict[str, Any]) -> None:
        del opened, url, config

    async def fake_ssrf(url: str) -> None:
        del url

    monkeypatch.setattr(probe_mod, "open_page", fake_open_page)
    monkeypatch.setattr(probe_mod, "navigate", fake_navigate)
    monkeypatch.setattr(probe_mod, "check_ssrf", fake_ssrf)
    monkeypatch.setattr(probe_mod, "resolve_bypass", lambda name, config=None: object())

    payload = await probe_mod.probe_detail(url="https://example.com/jobs/1", engine="auto")
    assert payload["ok"] is True
    assert payload["heading"] == "Engineer"
    assert "hiring" in payload["text_preview"]


@pytest.mark.asyncio
async def test_probe_challenge_without_solve(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ChallengePage(_FakePage):
        html = "<html><body>Cloudflare challenge</body></html>"

        async def evaluate(self, script: str, arg: object = None) -> object:
            del script, arg
            return []

        async def content(self) -> str:
            return self.html

    class _Bypass:
        observed_challenge_type = "cloudflare_challenge"

        def set_observed_challenge_type(self, challenge_type: str | None) -> None:
            if challenge_type:
                self.observed_challenge_type = str(challenge_type)

    @asynccontextmanager
    async def fake_open_page(config: dict[str, Any], *, bypass_strategy: Any = None):
        del config, bypass_strategy
        yield _ChallengePage()

    async def fake_navigate(opened: Any, url: str, config: dict[str, Any]) -> None:
        del opened, url, config

    async def fake_ssrf(url: str) -> None:
        del url

    monkeypatch.setattr(probe_mod, "open_page", fake_open_page)
    monkeypatch.setattr(probe_mod, "navigate", fake_navigate)
    monkeypatch.setattr(probe_mod, "check_ssrf", fake_ssrf)
    monkeypatch.setattr(probe_mod, "resolve_bypass", lambda name, config=None: _Bypass())

    payload = await probe_mod.probe_challenge(url="https://example.com/jobs", engine="auto")
    assert payload["status"] == "challenge"
    assert payload["challenge"] == "cloudflare_challenge"
    assert payload["captcha"] is None


_CLOUDFLARE_HTML = (
    "<html><head><title>Just a moment...</title></head>"
    "<body>Checking your browser before accessing example.com. "
    "Performance and security by Cloudflare</body></html>"
)


@pytest.mark.asyncio
async def test_probe_listing_blocked_navigation_is_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BlockedPage(_FakePage):
        html = _CLOUDFLARE_HTML

        async def evaluate(self, script: str, max_items: int = 0) -> list[dict[str, str]]:
            del script, max_items
            return []

        async def content(self) -> str:
            return self.html

    @asynccontextmanager
    async def fake_open_page(config: dict[str, Any], *, bypass_strategy: Any = None):
        del config, bypass_strategy
        yield _BlockedPage()

    async def fake_navigate(opened: Any, url: str, config: dict[str, Any]) -> None:
        del opened, url, config
        raise RuntimeError("Browser navigation blocked with status 403")

    async def fake_ssrf(url: str) -> None:
        del url

    monkeypatch.setattr(probe_mod, "open_page", fake_open_page)
    monkeypatch.setattr(probe_mod, "navigate", fake_navigate)
    monkeypatch.setattr(probe_mod, "check_ssrf", fake_ssrf)
    monkeypatch.setattr(probe_mod, "resolve_bypass", lambda name, config=None: object())

    payload = await probe_mod.probe_listing(url="https://example.com/jobs", engine="patchright")
    assert payload["executed"] is True
    assert payload["ok"] is False
    assert payload["status"] == "challenge"
    assert payload["error"] == "challenge_detected"
    assert payload["challenge"] == "cloudflare_challenge"
    assert payload["error"] != "RuntimeError"
    assert "RuntimeError" not in str(payload.get("notes") or [])
    assert payload["items"] == []
    assert not any("listing_extract=dom_" in note for note in payload["notes"])


@pytest.mark.asyncio
async def test_probe_challenge_provider_skips_without_widget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def fake_open_page(config: dict[str, Any], *, bypass_strategy: Any = None):
        del config, bypass_strategy
        yield _FakePage()

    async def fake_navigate(opened: Any, url: str, config: dict[str, Any]) -> None:
        del opened, url, config

    async def fake_ssrf(url: str) -> None:
        del url

    def boom_solver(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("paid solver must not be constructed without a classified challenge")

    monkeypatch.setattr(probe_mod, "open_page", fake_open_page)
    monkeypatch.setattr(probe_mod, "navigate", fake_navigate)
    monkeypatch.setattr(probe_mod, "check_ssrf", fake_ssrf)
    monkeypatch.setattr(probe_mod, "resolve_bypass", lambda name, config=None: object())
    monkeypatch.setattr(probe_mod, "_make_solver", boom_solver)

    payload = await probe_mod.probe_challenge(
        url="https://example.com/jobs",
        engine="auto",
        solve="provider",
    )
    assert payload["captcha"]["solved"] is False
    assert payload["captcha"]["error"] == "no_challenge"
    assert payload["error"] == "no_challenge"


_CAREER_HTML = """
<html><body>
  <a href="/jobs">All jobs</a>
  <a href="/career/ml-engineer">ML Engineer</a>
  <a href="/job/senior-python">Senior Python</a>
  <a href="/ru/career/analitik-dannyh">Analyst</a>
</body></html>
"""


def _patch_live_probe(
    monkeypatch: pytest.MonkeyPatch, page: Any
) -> None:
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


@pytest.mark.asyncio
async def test_probe_listing_dom_static_when_generic_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CareerPage(_FakePage):
        url = "https://example.com/ru/career"
        html = _CAREER_HTML

        async def evaluate(self, script: str, max_items: int = 0) -> list[dict[str, str]]:
            del script, max_items
            return []

        async def content(self) -> str:
            return self.html

    _patch_live_probe(monkeypatch, _CareerPage())
    payload = await probe_mod.probe_listing(
        url="https://example.com/ru/career",
        engine="auto",
        max_items=5,
    )
    assert payload["ok"] is True
    assert payload["status"] == "ok"
    assert payload["item_count"] >= 1
    urls = {item["url"] for item in payload["items"]}
    assert "https://example.com/career/ml-engineer" in urls
    assert any("listing_extract=dom_static" in note for note in payload["notes"])
    assert not any("listing_extract=generic" in note for note in payload["notes"])


@pytest.mark.asyncio
async def test_probe_listing_dom_rendered_when_static_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RenderedPage(_FakePage):
        url = "https://example.com/jobs"
        html = '<a href="/jobs">All jobs</a><a href="/about">About</a>'

        async def evaluate(self, script: str, arg: object = None) -> object:
            if "isVisible" in script or "job-card" in script:
                return ["https://example.com/career/ml-engineer"]
            return []

        async def content(self) -> str:
            return self.html

    _patch_live_probe(monkeypatch, _RenderedPage())
    payload = await probe_mod.probe_listing(url="https://example.com/jobs", engine="auto")
    assert payload["ok"] is True
    assert payload["item_count"] >= 1
    assert payload["items"][0]["url"] == "https://example.com/career/ml-engineer"
    assert any("listing_extract=dom_rendered" in note for note in payload["notes"])


@pytest.mark.asyncio
async def test_probe_listing_challenge_does_not_use_dom_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ChallengeCareerPage(_FakePage):
        url = "https://example.com/jobs"
        html = (
            _CLOUDFLARE_HTML.replace(
                "</body>",
                '<a href="/career/ml-engineer">Careers</a></body>',
            )
        )

        async def evaluate(self, script: str, arg: object = None) -> object:
            del script, arg
            return []

        async def content(self) -> str:
            return self.html

    class _Bypass:
        observed_challenge_type = "cloudflare_challenge"

        def set_observed_challenge_type(self, challenge_type: str | None) -> None:
            if challenge_type:
                self.observed_challenge_type = str(challenge_type)

    @asynccontextmanager
    async def fake_open_page(config: dict[str, Any], *, bypass_strategy: Any = None):
        del config, bypass_strategy
        yield _ChallengeCareerPage()

    async def fake_navigate(opened: Any, url: str, config: dict[str, Any]) -> None:
        del opened, url, config

    async def fake_ssrf(url: str) -> None:
        del url

    monkeypatch.setattr(probe_mod, "open_page", fake_open_page)
    monkeypatch.setattr(probe_mod, "navigate", fake_navigate)
    monkeypatch.setattr(probe_mod, "check_ssrf", fake_ssrf)
    monkeypatch.setattr(probe_mod, "resolve_bypass", lambda name, config=None: _Bypass())

    payload = await probe_mod.probe_listing(url="https://example.com/jobs", engine="auto")
    assert payload["status"] == "challenge"
    assert payload["error"] == "challenge_detected"
    assert payload["items"] == []
    assert payload["item_count"] == 0
    assert not any("listing_extract=dom_" in note for note in payload["notes"])


@pytest.mark.asyncio
async def test_probe_listing_dom_static_rejects_weak_nav_chrome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ChromePage(_FakePage):
        url = "https://example.com/ru/career"
        html = """
        <html><body>
          <a href="/kk/common/mobsvyaz-altel">Altel</a>
          <a href="/about">About</a>
        </body></html>
        """

        async def evaluate(self, script: str, arg: object = None) -> object:
            if "isVisible" in script or "job-card" in script:
                return ["https://example.com/kk/common/mobsvyaz-altel"]
            return []

        async def content(self) -> str:
            return self.html

    _patch_live_probe(monkeypatch, _ChromePage())
    payload = await probe_mod.probe_listing(
        url="https://example.com/ru/career",
        engine="auto",
        max_items=5,
    )
    assert payload["status"] == "empty"
    assert payload["item_count"] == 0
    assert payload["items"] == []
    assert not any("listing_extract=dom_static" in note for note in payload["notes"])
    assert not any("listing_extract=dom_rendered" in note for note in payload["notes"])


@pytest.mark.asyncio
async def test_probe_listing_dom_keeps_strong_detail_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _StrongPage(_FakePage):
        url = "https://example.com/jobs"
        html = """
        <html><body>
          <a href="/kk/common/mobsvyaz-altel">Altel</a>
          <a href="/jobs/12345-ml-engineer">ML Engineer</a>
        </body></html>
        """

        async def evaluate(self, script: str, arg: object = None) -> object:
            del script, arg
            return []

        async def content(self) -> str:
            return self.html

    _patch_live_probe(monkeypatch, _StrongPage())
    payload = await probe_mod.probe_listing(
        url="https://example.com/jobs",
        engine="auto",
        max_items=5,
    )
    assert payload["ok"] is True
    assert payload["item_count"] >= 1
    urls = {item["url"] for item in payload["items"]}
    assert "https://example.com/jobs/12345-ml-engineer" in urls
    assert "https://example.com/kk/common/mobsvyaz-altel" not in urls
