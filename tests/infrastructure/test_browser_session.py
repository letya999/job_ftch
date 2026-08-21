from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
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
    try:
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
    finally:
        await service.close_all()


@pytest.mark.asyncio
async def test_persistent_profile_opens_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    @asynccontextmanager
    async def fake_open_page(config: dict[str, Any], *, bypass_strategy: Any = None):
        del bypass_strategy
        assert config.get("persistent_context") is True
        assert config.get("_profile_dir")
        yield _FakePage()

    async def fake_navigate(opened: Any, url: str, config: dict[str, Any]) -> None:
        del opened, url, config

    async def fake_ssrf(url: str) -> None:
        del url

    monkeypatch.setattr(session_mod, "open_page", fake_open_page)
    monkeypatch.setattr(session_mod, "navigate", fake_navigate)
    monkeypatch.setattr(session_mod, "check_ssrf", fake_ssrf)
    monkeypatch.setattr(session_mod, "resolve_bypass", lambda name, config=None: object())
    monkeypatch.setattr(
        "job_ftch.config.get_settings",
        lambda: SimpleNamespace(browser_profile_root=tmp_path),
    )

    service = session_mod.OperatorBrowserSessionService()
    try:
        payload = await service.open(
            tenant_id="t1",
            url="https://example.com/jobs",
            engine="auto",
            headed=False,
            profile="persistent",
        )
        assert payload["status"] != "not_implemented"
        assert payload["ok"] is True
        assert payload["profile"] == "persistent"
        assert payload.get("profile_key")
        assert str(tmp_path) not in str(payload)
        assert "secret" not in str(payload)
        session_id = str(payload["session_id"])
        traced = await service.capture(session_id, "trace")
        assert traced["status"] != "not_implemented"
        assert traced.get("path")
        assert "secret" not in str(traced)
        extended = await service.continue_session(session_id, "extend")
        assert extended["status"] != "unsupported"
        waited = await service.continue_session(session_id, "wait_challenge")
        assert waited["status"] != "not_implemented"
        await service.close(session_id)

        domain = await service.open(
            tenant_id="t1",
            url="https://example.com/jobs",
            engine="auto",
            headed=False,
            profile="domain",
        )
        assert domain["ok"] is True
        assert domain["profile"] == "domain"
        assert domain.get("profile_key")
        assert str(tmp_path) not in str(domain)
        await service.close(str(domain["session_id"]))
    finally:
        await service.close_all()


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
    try:
        opened = await service.open(tenant_id="t1", url="https://example.com/jobs", engine="auto")
        session_id = str(opened["session_id"])
        payload = await service.continue_session(session_id, "click all the things")
        assert payload["status"] == "unsupported"
        await service.close(session_id)
    finally:
        await service.close_all()


_DUMMY_PAGE = object()


def _hand_built_session(*, tenant_id: str = "t1", page: Any | None = _DUMMY_PAGE) -> session_mod._LiveSession:
    session = session_mod._LiveSession(
        tenant_id=tenant_id,
        url="https://example.com/jobs",
        engine="auto",
        headed=False,
        bypass_config=None,
        manual_challenge=False,
    )
    session.page = page
    return session


@pytest.mark.asyncio
async def test_borrow_missing_wrong_tenant_and_gone_page() -> None:
    service = session_mod.OperatorBrowserSessionService()
    missing = await service.borrow("missing-id", "t1")
    assert missing["error"] == "session_not_found"

    session = _hand_built_session(tenant_id="t1")
    service._sessions[session.id] = session
    wrong = await service.borrow(session.id, "other-tenant")
    assert wrong["error"] == "session_wrong_tenant"

    gone = _hand_built_session(tenant_id="t1", page=None)
    service._sessions[gone.id] = gone
    gone_payload = await service.borrow(gone.id, "t1")
    assert gone_payload["error"] == "session_page_gone"


@pytest.mark.asyncio
async def test_borrow_and_release_hand_built_session() -> None:
    service = session_mod.OperatorBrowserSessionService()
    dummy = object()
    session = _hand_built_session(tenant_id="t1", page=dummy)
    service._sessions[session.id] = session
    borrowed = await service.borrow(session.id, "t1")
    assert borrowed is session
    assert session._borrowed is True
    assert session.page is dummy
    await service.release(session.id)
    assert session._borrowed is False


_CLOUDFLARE_HTML = (
    "<html><head><title>Just a moment...</title></head>"
    "<body>Checking your browser before accessing example.com. "
    "Performance and security by Cloudflare</body></html>"
)


@pytest.mark.asyncio
async def test_session_open_keeps_page_on_blocked_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ChallengePage(_FakePage):
        async def content(self) -> str:
            return _CLOUDFLARE_HTML

    @asynccontextmanager
    async def fake_open_page(config: dict[str, Any], *, bypass_strategy: Any = None):
        del config, bypass_strategy
        yield _ChallengePage()

    async def fake_navigate(opened: Any, url: str, config: dict[str, Any]) -> None:
        del opened, url, config
        raise RuntimeError("Browser navigation blocked with status 403")

    async def fake_ssrf(url: str) -> None:
        del url

    monkeypatch.setattr(session_mod, "open_page", fake_open_page)
    monkeypatch.setattr(session_mod, "navigate", fake_navigate)
    monkeypatch.setattr(session_mod, "check_ssrf", fake_ssrf)
    monkeypatch.setattr(session_mod, "resolve_bypass", lambda name, config=None: object())

    service = session_mod.OperatorBrowserSessionService()
    try:
        opened = await service.open(
            tenant_id="t1",
            url="https://example.com/jobs",
            engine="auto",
            headed=False,
        )
        assert opened.get("session_id")
        assert opened["status"] == "challenge"
        assert opened["challenge"] == "cloudflare_challenge"
        assert opened["error"] == "challenge_detected"
        session_id = str(opened["session_id"])
        waited = await service.continue_session(session_id, "wait")
        assert waited.get("session_id") == session_id
        closed = await service.close(session_id)
        assert closed["status"] == "closed"
    finally:
        await service.close_all()


@pytest.mark.asyncio
async def test_session_provider_solve_skips_without_challenge(
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

    monkeypatch.setattr(session_mod, "open_page", fake_open_page)
    monkeypatch.setattr(session_mod, "navigate", fake_navigate)
    monkeypatch.setattr(session_mod, "check_ssrf", fake_ssrf)
    monkeypatch.setattr(session_mod, "resolve_bypass", lambda name, config=None: object())
    monkeypatch.setattr("job_ftch.infrastructure.browser_probe._make_solver", boom_solver)

    service = session_mod.OperatorBrowserSessionService()
    try:
        opened = await service.open(
            tenant_id="t1",
            url="https://example.com/jobs",
            engine="auto",
            headed=False,
        )
        session_id = str(opened["session_id"])
        payload = await service.continue_session(session_id, "solve:provider")
        assert payload["captcha"]["solved"] is False
        assert payload["captcha"]["error"] == "no_challenge"
        assert payload["error"] == "no_challenge"
        assert "proxy" not in payload
        assert "proxy_url" not in payload
        await service.close(session_id)
    finally:
        await service.close_all()


def test_solve_command_deadline_exceeds_solver_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_mod, "_solver_timeout_budget_seconds", lambda: 40.0)
    assert session_mod._command_deadline_seconds("wait") == 30.0
    assert session_mod._command_deadline_seconds("solve") > 30.0
    assert session_mod._command_deadline_seconds("solve") == 55.0
    assert session_mod._command_deadline_seconds("solve") <= session_mod.SOLVE_DEADLINE_CAP_SECONDS


@pytest.mark.asyncio
async def test_ask_solve_timeout_exceeds_thirty_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, float] = {}

    async def record_wait_for(awaitable: Any, timeout: object = None) -> dict[str, Any]:
        del awaitable
        seen["timeout"] = float(timeout or 0)
        return {"ok": True, "status": "ok", "captcha": {"solved": False}}

    monkeypatch.setattr(session_mod, "_solver_timeout_budget_seconds", lambda: 40.0)
    monkeypatch.setattr(session_mod.asyncio, "wait_for", record_wait_for)
    service = session_mod.OperatorBrowserSessionService()
    session = _hand_built_session()
    payload = await service._ask(session, "solve", {"solve": "provider"})
    assert seen["timeout"] > 30
    assert payload["status"] == "ok"


@pytest.mark.asyncio
async def test_session_provider_solve_attaches_proxy_privately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ChallengePage(_FakePage):
        async def content(self) -> str:
            return _CLOUDFLARE_HTML

    attached_proxy = "http://proxy.example.invalid:8080"
    captured: dict[str, Any] = {}

    class _DummyResult:
        solved = False
        method = "capsolver"
        error = None
        failure_reason = None
        challenge_type = "cloudflare_challenge"
        result_kind = None
        elapsed_seconds = 1.0

    class _DummySolver:
        async def solve(self, page: Any, challenge_type: str = "", url: str = "") -> _DummyResult:
            del page, challenge_type, url
            return _DummyResult()

    def fake_make_solver(solve: str, bypass_config: dict[str, Any] | None) -> _DummySolver:
        captured["solve"] = solve
        captured["config"] = dict(bypass_config or {})
        return _DummySolver()

    @asynccontextmanager
    async def fake_open_page(config: dict[str, Any], *, bypass_strategy: Any = None):
        del config, bypass_strategy
        yield _ChallengePage()

    async def fake_navigate(opened: Any, url: str, config: dict[str, Any]) -> None:
        del opened, url, config
        raise RuntimeError("Browser navigation blocked with status 403")

    async def fake_ssrf(url: str) -> None:
        del url

    monkeypatch.setattr(session_mod, "open_page", fake_open_page)
    monkeypatch.setattr(session_mod, "navigate", fake_navigate)
    monkeypatch.setattr(session_mod, "check_ssrf", fake_ssrf)
    monkeypatch.setattr(session_mod, "resolve_bypass", lambda name, config=None: object())
    monkeypatch.setattr(session_mod, "_operator_captcha_proxy_url", lambda: attached_proxy)
    monkeypatch.setattr("job_ftch.infrastructure.browser_probe._make_solver", fake_make_solver)

    service = session_mod.OperatorBrowserSessionService()
    try:
        opened = await service.open(
            tenant_id="t1",
            url="https://example.com/jobs",
            engine="auto",
            headed=False,
        )
        session_id = str(opened["session_id"])
        assert opened["challenge"] == "cloudflare_challenge"
        payload = await service.continue_session(session_id, "solve:provider")
        assert captured["solve"] == "provider"
        assert captured["config"].get("proxy_url") == attached_proxy
        dumped = str(payload)
        assert "proxy_url" not in payload
        assert "proxy" not in payload
        assert attached_proxy not in dumped
        assert "proxy.example.invalid" not in dumped
        await service.close(session_id)
    finally:
        await service.close_all()
