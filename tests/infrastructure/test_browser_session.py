from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from job_ftch.infrastructure import browser_session as session_mod


class _FakePage:
    url = "https://example.com/jobs"

    async def title(self) -> str:
        return "Jobs"

    async def content(self) -> str:
        return "<html><body><h1>Jobs</h1><p>Open roles</p></body></html>"

    async def evaluate(self, script: str, arg: object = None) -> object:
        if "innerText" in script:
            return "Open roles"
        if "h1" in script:
            return "Jobs"
        del arg
        return []

    async def reload(self) -> None:
        return None

    async def screenshot(self, type: str = "png") -> bytes:
        del type
        return b"png-bytes"

    @property
    def context(self) -> Any:
        class _Context:
            async def cookies(self) -> list[dict[str, str]]:
                return [{"name": "sessionid", "value": "secret"}]

        return _Context()


@pytest.mark.asyncio
async def test_session_open_capture_close(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage()

    @asynccontextmanager
    async def fake_open_page(config: dict[str, Any], *, bypass_strategy: Any = None):
        del config, bypass_strategy
        yield page

    async def fake_navigate(opened: Any, url: str, config: dict[str, Any]) -> None:
        del opened, url, config

    async def fake_ssrf(url: str) -> None:
        del url

    monkeypatch.setattr(session_mod, "open_page", fake_open_page)
    monkeypatch.setattr(session_mod, "navigate", fake_navigate)
    monkeypatch.setattr(session_mod, "check_ssrf", fake_ssrf)
    monkeypatch.setattr(session_mod, "resolve_bypass", lambda name, config=None: object())

    service = session_mod.OperatorBrowserSessionService()
    opened = await service.open(
        tenant_id="t1",
        url="https://example.com/jobs",
        engine="auto",
        headed=False,
    )
    assert opened["ok"] is True
    assert opened["session_id"]
    session_id = str(opened["session_id"])
    captured = await service.capture(session_id, "text")
    assert "Open roles" in str(captured.get("text") or "")
    cookies = await service.capture(session_id, "cookies_summary")
    assert cookies["cookies_summary"]["names"] == ["sessionid"]
    assert "secret" not in str(cookies)
    closed = await service.close(session_id)
    assert closed["status"] == "closed"


@pytest.mark.asyncio
async def test_persistent_profile_is_unsupported() -> None:
    service = session_mod.OperatorBrowserSessionService()
    payload = await service.open(
        tenant_id="t1",
        url="https://example.com/jobs",
        engine="auto",
        profile="persistent",
    )
    assert payload["status"] == "unsupported"
    assert payload["error"] == "persistent_profile_unsupported"


@pytest.mark.asyncio
async def test_unknown_instruction_is_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_open_page(config: dict[str, Any], *, bypass_strategy: Any = None):
        del config, bypass_strategy
        yield _FakePage()

    async def fake_navigate(opened: Any, url: str, config: dict[str, Any]) -> None:
        del opened, url, config

    async def fake_ssrf(url: str) -> None:
        del url

    monkeypatch.setattr(session_mod, "open_page", fake_open_page)
    monkeypatch.setattr(session_mod, "navigate", fake_navigate)
    monkeypatch.setattr(session_mod, "check_ssrf", fake_ssrf)
    monkeypatch.setattr(session_mod, "resolve_bypass", lambda name, config=None: object())

    service = session_mod.OperatorBrowserSessionService()
    opened = await service.open(tenant_id="t1", url="https://example.com/jobs", engine="auto")
    session_id = str(opened["session_id"])
    payload = await service.continue_session(session_id, "click all the things")
    assert payload["status"] == "unsupported"
    await service.close(session_id)
