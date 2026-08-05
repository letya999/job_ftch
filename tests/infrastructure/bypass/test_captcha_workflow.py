from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from job_ftch.infrastructure.bypass.captcha_providers import (
    CapSolverProvider,
    extract_recaptcha_action,
    extract_sitekey,
    extract_turnstile_metadata,
    get_captcha_provider_capability,
    list_captcha_providers,
    normalize_challenge_type,
    register_captcha_provider,
)
from job_ftch.infrastructure.bypass.captcha_solver import (
    CaptchaFailureReason,
    CaptchaSolverBypass,
    CaptchaSolveResult,
    _create_captcha_solver,
    _normalize_provider_routes,
)
from job_ftch.infrastructure.bypass.failure_signal import _detect_captcha_type
from job_ftch.infrastructure.sources.browser_utils import (
    install_challenge_response_detector,
    navigate,
)
from job_ftch.infrastructure.sources.monitors.shared import (
    BrowserChallengeError,
    raise_if_browser_challenge,
)
from job_ftch.infrastructure.sources.source_deadline import (
    reset_source_deadline,
    set_source_deadline,
)


class _FixturePage:
    def __init__(self, html: str = "") -> None:
        self._html = html
        self.url = "https://example.test/jobs"
        self.evaluate = AsyncMock(return_value=True)

    async def content(self) -> str:
        return self._html


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ('<div class="cf-turnstile" data-sitekey="0x1"></div>', "turnstile"),
        ('<div class="h-captcha" data-sitekey="h1"></div>', "hcaptcha"),
        ('<div class="g-recaptcha" data-sitekey="r1"></div>', "recaptcha"),
        (
            '<script src="https://www.google.com/recaptcha/api.js?render=site-public-key"></script>',
            "recaptcha_v3",
        ),
        ('<script>window.dd="datadome"</script>', "datadome"),
        ('<div id="px-captcha">perimeterx</div>', "perimeterx"),
        ('<img class="captcha-image" src="/captcha.png">', "image"),
    ],
)
@pytest.mark.asyncio
async def test_offline_challenge_fixtures_are_detected(html: str, expected: str) -> None:
    solver = CaptchaSolverBypass(wait_seconds=0.01)
    assert await solver._detect_challenge(_FixturePage(html)) == expected


@pytest.mark.asyncio
async def test_recaptcha_v3_sitekey_is_extracted_from_render_param() -> None:
    page = SimpleNamespace(
        evaluate=AsyncMock(
            side_effect=[
                "",
                "site-public-key",
            ]
        )
    )

    assert await extract_sitekey(page) == "site-public-key"


@pytest.mark.asyncio
async def test_recaptcha_v3_action_prefers_captured_execute_call() -> None:
    page = SimpleNamespace(
        evaluate=AsyncMock(
            side_effect=[
                "career_submit",
            ]
        )
    )

    assert await extract_recaptcha_action(page) == "career_submit"


@pytest.mark.asyncio
async def test_turnstile_metadata_reads_public_widget_attributes() -> None:
    page = SimpleNamespace(
        evaluate=AsyncMock(return_value={"action": "career", "cdata": "opaque-public-data"})
    )

    assert await extract_turnstile_metadata(page) == {
        "action": "career",
        "cdata": "opaque-public-data",
    }


@pytest.mark.asyncio
async def test_capsolver_v3_payload_omits_unknown_action_and_sets_min_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import job_ftch.infrastructure.bypass.captcha_providers as providers
    from job_ftch.infrastructure.bypass.captcha_providers import CapSolverProvider

    captured: dict[str, object] = {}

    class _Response:
        def json(self) -> dict[str, object]:
            return {"errorId": 1, "errorDescription": "fixture reject"}

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        async def post(self, url: str, *, json: dict[str, object]) -> _Response:
            del url
            captured.update(json)
            return _Response()

    page = SimpleNamespace(evaluate=AsyncMock(side_effect=["site-key", "", ""]))
    monkeypatch.setattr(providers.httpx, "AsyncClient", _Client)

    provider = CapSolverProvider("offline-key")  # pragma: allowlist secret
    await provider.solve(page, challenge_type="recaptcha_v3", url="https://example.test")

    task = captured["task"]
    assert isinstance(task, dict)
    assert task["minScore"] == 0.3
    assert "pageAction" not in task


@pytest.mark.asyncio
async def test_capsolver_cloudflare_task_uses_proxy_without_sitekey(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import job_ftch.infrastructure.bypass.captcha_providers as providers

    captured: dict[str, object] = {}
    responses = iter(
        [
            {"errorId": 0, "taskId": "task-1"},
            {
                "errorId": 0,
                "status": "ready",
                "solution": {"cf_clearance": "clearance-cookie"},
            },
        ]
    )

    class _Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        async def post(self, url: str, *, json: dict[str, object]) -> _Response:
            del url
            if "task" in json:
                captured.update(json)
            return _Response(next(responses))

    page = SimpleNamespace(
        evaluate=AsyncMock(return_value="Chrome UA"),
        content=AsyncMock(return_value="<html>Just a moment</html>"),
    )
    monkeypatch.setattr(providers.httpx, "AsyncClient", _Client)

    provider = CapSolverProvider(
        "offline-key",  # pragma: allowlist secret
        proxy_url="http://" + "user" + ":" + "pass" + "@127.0.0.1:9000",
    )
    result = await provider.solve(
        page,
        challenge_type="cloudflare_challenge",
        url="https://example.test/jobs",
    )

    task = captured["task"]
    assert isinstance(task, dict)
    assert task["type"] == "AntiCloudflareTask"
    assert task["proxy"] == "127.0.0.1:9000:user:pass"
    assert task["userAgent"] == "Chrome UA"
    assert task["html"] == "<html>Just a moment</html>"
    assert "websiteKey" not in task
    assert result.solved
    assert result.cookies == {"cf_clearance": "clearance-cookie"}


@pytest.mark.asyncio
async def test_capsolver_cloudflare_resolves_proxy_hostname_before_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import job_ftch.infrastructure.bypass.captcha_providers as providers

    captured: dict[str, object] = {}
    responses = iter(
        [
            {"errorId": 0, "taskId": "task-1"},
            {"errorId": 0, "status": "ready", "solution": {"cf_clearance": "ok"}},
        ]
    )

    class _Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        async def post(self, url: str, *, json: dict[str, object]) -> _Response:
            del url
            if "task" in json:
                captured.update(json)
            return _Response(next(responses))

    def fake_getaddrinfo(host: str, *args: object, **kwargs: object) -> list[tuple[object, ...]]:
        del args, kwargs
        assert host == "residential-gateway.example"
        return [
            (
                providers.socket.AF_INET,
                providers.socket.SOCK_STREAM,
                0,
                "",
                ("203.0.113.7", 0),
            )
        ]

    monkeypatch.setattr(providers.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(providers.httpx, "AsyncClient", _Client)

    provider = CapSolverProvider(
        "offline-key",  # pragma: allowlist secret
        proxy_url="http://" + "user" + ":" + "pass" + "@residential-gateway.example:9000",
    )
    result = await provider.solve(
        SimpleNamespace(evaluate=AsyncMock(return_value="Chrome UA")),
        challenge_type="cloudflare_challenge",
        url="https://example.test/jobs",
    )

    task = captured["task"]
    assert isinstance(task, dict)
    assert task["proxy"] == "203.0.113.7:9000:user:pass"
    assert result.solved


@pytest.mark.asyncio
async def test_capsolver_cloudflare_requires_proxy() -> None:
    page = SimpleNamespace(evaluate=AsyncMock())
    provider = CapSolverProvider("offline-key")  # pragma: allowlist secret

    result = await provider.solve(
        page,
        challenge_type="cloudflare_challenge",
        url="https://example.test/jobs",
    )

    assert not result.solved
    assert result.failure_reason is CaptchaFailureReason.UNSUPPORTED_CHALLENGE
    page.evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_waits_for_sitekey_marker_before_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import job_ftch.infrastructure.bypass.captcha_providers as providers
    from job_ftch.infrastructure.bypass.captcha_providers import CapSolverProvider

    class _Response:
        def json(self) -> dict[str, object]:
            return {"errorId": 1, "errorDescription": "fixture reject"}

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        async def post(self, url: str, *, json: dict[str, object]) -> _Response:
            del url, json
            return _Response()

    page = SimpleNamespace(
        wait_for_selector=AsyncMock(),
        evaluate=AsyncMock(side_effect=["site-key"]),
    )
    monkeypatch.setattr(providers.httpx, "AsyncClient", _Client)

    provider = CapSolverProvider("offline-key")
    await provider.solve(page, challenge_type="turnstile", url="https://example.test")

    page.wait_for_selector.assert_awaited()


@pytest.mark.asyncio
async def test_solver_backoff_prevents_repeated_provider_attempts() -> None:
    page = SimpleNamespace(url="https://example.test/jobs")
    solver = CaptchaSolverBypass(
        provider="capsolver",
        api_key="offline-key",  # pragma: allowlist secret
        max_attempts=3,
        max_paid_attempts=3,
        min_provider_seconds=0,
        backoff_seconds=60,
        enabled_providers=frozenset({"capsolver"}),
    )
    calls = 0

    async def fake_external(*args: object, **kwargs: object) -> CaptchaSolveResult:
        nonlocal calls
        del args, kwargs
        calls += 1
        return CaptchaSolveResult(
            solved=False,
            method="capsolver",
            failure_reason=CaptchaFailureReason.PROVIDER_REJECTED,
        )

    solver._solve_external_api = fake_external  # type: ignore[method-assign]

    first = await solver.solve(page, challenge_type="recaptcha", url="https://example.test/jobs")
    second = await solver.solve(page, challenge_type="recaptcha", url="https://example.test/jobs")

    assert first.failure_reason is CaptchaFailureReason.PROVIDER_REJECTED
    assert second.failure_reason is CaptchaFailureReason.BACKOFF_ACTIVE
    assert calls == 1


@pytest.mark.asyncio
async def test_solver_applies_provider_clearance_cookies_to_browser_context() -> None:
    context = SimpleNamespace(add_cookies=AsyncMock())
    page = SimpleNamespace(url="https://jobs.example.test/list", context=context)
    solver = CaptchaSolverBypass(
        provider="capsolver",
        api_key="offline-key",  # pragma: allowlist secret
        max_attempts=1,
        max_paid_attempts=1,
        min_provider_seconds=0,
        enabled_providers=frozenset({"capsolver"}),
        authorized_domains=frozenset({"example.test"}),
    )

    async def fake_external(*args: object, **kwargs: object) -> CaptchaSolveResult:
        del args, kwargs
        return CaptchaSolveResult(
            solved=True,
            method="capsolver",
            cookies={"cf_clearance": "clearance-cookie"},
        )

    solver._solve_external_api = fake_external  # type: ignore[method-assign]

    result = await solver.solve(
        page,
        challenge_type="cloudflare_challenge",
        url="https://jobs.example.test/list",
    )

    assert result.solved
    context.add_cookies.assert_awaited_once_with(
        [
            {
                "name": "cf_clearance",
                "value": "clearance-cookie",
                "domain": "jobs.example.test",
                "path": "/",
                "secure": True,
            }
        ]
    )


@pytest.mark.asyncio
async def test_response_detector_sets_observed_challenge_type() -> None:
    callbacks: dict[str, object] = {}
    controller = SimpleNamespace(set_observed_challenge_type=AsyncMock())

    class _Page:
        def on(self, event: str, callback: object) -> None:
            callbacks[event] = callback

    response = SimpleNamespace(
        status=403,
        headers={"cf-mitigated": "challenge"},
        url="https://example.test/cdn-cgi/challenge",
    )

    await install_challenge_response_detector(
        _Page(),
        url="https://example.test/jobs",
        controller=controller,
        surface="monitor",
    )
    callbacks["response"](response)  # type: ignore[operator]
    await asyncio.sleep(0)

    controller.set_observed_challenge_type.assert_called_once()


@pytest.mark.asyncio
async def test_recaptcha_v3_token_application_invokes_callbacks() -> None:
    page = SimpleNamespace(evaluate=AsyncMock(return_value=True))
    solver = CaptchaSolverBypass(wait_seconds=0.01)

    assert await solver._inject_token(page, "recaptcha_v3", "provider-token")

    script = page.evaluate.await_args.args[0]
    assert "___grecaptcha_cfg" in script
    assert "data-callback" in script
    assert "g-recaptcha-response" in script


@pytest.mark.asyncio
async def test_recaptcha_v3_clear_check_requires_visible_non_blocked_content() -> None:
    page = SimpleNamespace(
        evaluate=AsyncMock(
            side_effect=[
                "complete",
                "",
                "provider-token",
                "Open roles Senior Python Engineer Apply now "
                "Remote backend developer vacancy with team description and benefits. " * 2,
            ]
        )
    )
    solver = CaptchaSolverBypass(wait_seconds=0.01)

    assert await solver._check_challenge_cleared(page, "recaptcha_v3")


@pytest.mark.asyncio
async def test_cloudflare_challenge_alias_uses_cloudflare_clear_check() -> None:
    page = SimpleNamespace(
        evaluate=AsyncMock(
            side_effect=[
                "complete",
                "Just a moment Checking your browser",
            ]
        )
    )
    solver = CaptchaSolverBypass(wait_seconds=0.01)

    assert not await solver._check_challenge_cleared(page, "cloudflare_challenge")


@pytest.mark.asyncio
async def test_cloudflare_clear_check_rejects_classified_challenge_html() -> None:
    page = SimpleNamespace(
        evaluate=AsyncMock(
            side_effect=[
                "complete",
                "Enable cookies and reload this page",
                "<html><script src='/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1'></script></html>",
            ]
        )
    )
    solver = CaptchaSolverBypass(wait_seconds=0.01)

    assert not await solver._check_challenge_cleared(page, "cloudflare_challenge")


@pytest.mark.asyncio
async def test_cloudflare_clear_check_rejects_visible_security_verification_text() -> None:
    page = SimpleNamespace(
        evaluate=AsyncMock(
            side_effect=[
                "complete",
                "Performing security verification. "
                "This website uses a security service to protect against malicious bots. "
                "Performance and Security by Cloudflare",
            ]
        )
    )
    solver = CaptchaSolverBypass(wait_seconds=0.01)

    assert not await solver._check_challenge_cleared(page, "cloudflare_challenge")


@pytest.mark.asyncio
async def test_cloudflare_clear_check_requires_clearance_cookie() -> None:
    page = SimpleNamespace(
        evaluate=AsyncMock(
            side_effect=[
                "complete",
                "Open roles Senior Python Engineer Apply now "
                "Remote backend developer vacancy with team description and benefits. " * 2,
                "<html><body><main>Open roles Senior Python Engineer</main></body></html>",
                "",
            ]
        )
    )
    solver = CaptchaSolverBypass(wait_seconds=0.01)

    assert not await solver._check_challenge_cleared(page, "cloudflare_challenge")


def test_failure_signal_labels_recaptcha_v3_separately() -> None:
    html = '<script src="https://www.google.com/recaptcha/api.js?render=site-public-key"></script>'

    assert _detect_captcha_type(html) == "recaptcha_v3"


def test_monitor_challenge_html_raises_typed_error() -> None:
    html = '<html><body><div class="cf-turnstile"></div></body></html>'

    with pytest.raises(BrowserChallengeError) as raised:
        raise_if_browser_challenge(html, url="https://example.test/jobs")

    assert raised.value.status_code == 200
    assert raised.value.body
    assert raised.value.challenge_type == "turnstile"


@pytest.mark.asyncio
async def test_missing_paid_credential_is_graceful() -> None:
    solver = CaptchaSolverBypass(provider="capsolver", api_key="")
    result = await solver.solve(_FixturePage(), challenge_type="hcaptcha")
    assert not result.solved
    assert result.failure_reason is CaptchaFailureReason.MISSING_CREDENTIAL


@pytest.mark.asyncio
async def test_solver_attempt_budget_is_enforced() -> None:
    solver = CaptchaSolverBypass(provider="capsolver", api_key="", max_attempts=1)
    await solver.solve(_FixturePage(), challenge_type="hcaptcha")
    result = await solver.solve(_FixturePage(), challenge_type="hcaptcha")
    assert result.failure_reason is CaptchaFailureReason.BUDGET_EXHAUSTED


@pytest.mark.asyncio
async def test_paid_provider_is_rejected_when_deadline_is_too_short() -> None:
    solver = CaptchaSolverBypass(
        provider="capsolver",
        api_key="secret-for-test",  # pragma: allowlist secret
    )  # pragma: allowlist secret
    token = set_source_deadline(asyncio.get_running_loop().time() + 0.1)
    try:
        result = await solver.solve(_FixturePage(), challenge_type="hcaptcha")
    finally:
        reset_source_deadline(token)
    assert result.failure_reason is CaptchaFailureReason.DEADLINE_INSUFFICIENT


@pytest.mark.asyncio
async def test_paid_calls_are_serialized_per_solver() -> None:
    solver = CaptchaSolverBypass(
        provider="capsolver",
        api_key="secret-for-test",  # pragma: allowlist secret
        max_attempts=2,
        max_paid_attempts=2,
    )
    concurrent = 0
    peak = 0

    async def fake_external(*args, **kwargs):
        nonlocal concurrent, peak
        del args, kwargs
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0)
        concurrent -= 1
        return CaptchaSolveResult(solved=False, method="capsolver")

    solver._solve_external_api = fake_external  # type: ignore[method-assign]
    await asyncio.gather(
        solver.solve(_FixturePage(), challenge_type="hcaptcha"),
        solver.solve(_FixturePage(), challenge_type="hcaptcha"),
    )
    assert peak == 1


@pytest.mark.parametrize(
    ("provider_name", "provider_result", "expected_reason"),
    [
        (
            "fake-success",
            CaptchaSolveResult(solved=True, method="fake-success"),
            None,
        ),
        (
            "fake-timeout",
            CaptchaSolveResult(
                solved=False,
                method="fake-timeout",
                failure_reason=CaptchaFailureReason.PROVIDER_TIMEOUT,
            ),
            CaptchaFailureReason.PROVIDER_TIMEOUT,
        ),
        (
            "fake-rejection",
            CaptchaSolveResult(
                solved=False,
                method="fake-rejection",
                failure_reason=CaptchaFailureReason.PROVIDER_REJECTED,
            ),
            CaptchaFailureReason.PROVIDER_REJECTED,
        ),
        (
            "fake-malformed",
            {"token": "not-a-typed-result"},
            CaptchaFailureReason.PROVIDER_UNAVAILABLE,
        ),
    ],
)
@pytest.mark.asyncio
async def test_fake_provider_outcome_matrix(
    provider_name: str,
    provider_result: object,
    expected_reason: CaptchaFailureReason | None,
) -> None:
    class FakeProvider:
        def __init__(self, api_key: str) -> None:
            assert api_key == "offline-test-key"  # pragma: allowlist secret

        async def solve(self, page, *, challenge_type: str, url: str):
            del page, challenge_type, url
            return provider_result

    register_captcha_provider(provider_name)(FakeProvider)  # type: ignore[arg-type]
    solver = CaptchaSolverBypass(
        provider=provider_name,
        api_key="offline-test-key",  # pragma: allowlist secret
        min_provider_seconds=0,
    )
    result = await solver.solve(_FixturePage(), challenge_type="hcaptcha")
    assert result.failure_reason is expected_reason
    assert result.solved is (expected_reason is None)


@pytest.mark.asyncio
async def test_disabled_provider_skips_external_call() -> None:
    calls = 0

    class SpyProvider:
        def __init__(self, api_key: str) -> None:
            del api_key

        async def solve(self, page, *, challenge_type: str, url: str):
            nonlocal calls
            calls += 1
            return CaptchaSolveResult(solved=True, method="spy")

    register_captcha_provider("spy_disabled")(SpyProvider)  # type: ignore[arg-type]
    solver = CaptchaSolverBypass(
        provider="spy_disabled",
        api_key="offline-test-key",  # pragma: allowlist secret
        min_provider_seconds=0,
        enabled_providers=frozenset({"browser_wait", "nopecha"}),
    )
    result = await solver.solve(_FixturePage(), challenge_type="hcaptcha")
    assert result.failure_reason is CaptchaFailureReason.PROVIDER_DISABLED
    assert result.solved is False
    assert calls == 0


@pytest.mark.asyncio
async def test_enabled_provider_still_fires() -> None:
    class OkProvider:
        def __init__(self, api_key: str) -> None:
            del api_key

        async def solve(self, page, *, challenge_type: str, url: str):
            return CaptchaSolveResult(solved=True, method="ok_enabled")

    register_captcha_provider("ok_enabled")(OkProvider)  # type: ignore[arg-type]
    solver = CaptchaSolverBypass(
        provider="ok_enabled",
        api_key="offline-test-key",  # pragma: allowlist secret
        min_provider_seconds=0,
        enabled_providers=frozenset({"browser_wait", "ok_enabled"}),
    )
    result = await solver.solve(_FixturePage(), challenge_type="hcaptcha")
    assert result.solved is True
    assert result.method == "ok_enabled"


@pytest.mark.asyncio
async def test_provider_chain_falls_back_to_second_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FirstProvider:
        def __init__(self, api_key: str, *, proxy_url: str = "") -> None:
            del api_key, proxy_url

        async def solve(self, page, *, challenge_type: str, url: str):
            del page, challenge_type, url
            return CaptchaSolveResult(
                solved=False,
                method="chain_first",
                failure_reason=CaptchaFailureReason.PROVIDER_REJECTED,
            )

    class SecondProvider:
        def __init__(self, api_key: str, *, proxy_url: str = "") -> None:
            assert api_key == "second-provider-key"  # pragma: allowlist secret
            del proxy_url

        async def solve(self, page, *, challenge_type: str, url: str):
            del page, challenge_type, url
            return CaptchaSolveResult(solved=True, method="chain_second")

    register_captcha_provider("chain_first")(FirstProvider)  # type: ignore[arg-type]
    register_captcha_provider("chain_second")(SecondProvider)  # type: ignore[arg-type]
    monkeypatch.setitem(
        __import__(
            "job_ftch.infrastructure.bypass.captcha_solver",
            fromlist=["CAPTCHA_PROVIDER_ENV_KEYS"],
        ).CAPTCHA_PROVIDER_ENV_KEYS,
        "chain_second",
        "CHAIN_SECOND_API_KEY",
    )
    monkeypatch.setenv("CHAIN_SECOND_API_KEY", "second-provider-key")

    solver = CaptchaSolverBypass(
        provider_routes={"recaptcha": ("chain_first", "chain_second")},
        enabled_providers=frozenset({"chain_first", "chain_second"}),
        max_paid_attempts=2,
        min_provider_seconds=0,
    )

    result = await solver.solve(_FixturePage(), challenge_type="recaptcha")
    assert result.solved is True
    assert result.method == "chain_second"


@pytest.mark.asyncio
async def test_provider_chain_stops_at_manual_required() -> None:
    solver = CaptchaSolverBypass(
        provider_routes={"recaptcha": ("manual_required",)},
        enabled_providers=frozenset({"browser_wait"}),
        min_provider_seconds=0,
    )

    result = await solver.solve(_FixturePage(), challenge_type="recaptcha")
    assert result.solved is False
    assert result.method == "manual_required"
    assert result.failure_reason is CaptchaFailureReason.UNSUPPORTED_CHALLENGE


@pytest.mark.asyncio
async def test_unknown_provider_is_graceful() -> None:
    solver = CaptchaSolverBypass(
        provider="not-registered",
        api_key="offline-test-key",  # pragma: allowlist secret
        min_provider_seconds=0,
    )
    result = await solver.solve(_FixturePage(), challenge_type="hcaptcha")
    assert result.failure_reason is CaptchaFailureReason.PROVIDER_UNAVAILABLE


@pytest.mark.asyncio
async def test_solver_logs_never_include_token_cookie_or_provider_secret() -> None:
    solver = CaptchaSolverBypass(
        provider="capsolver",
        api_key="provider-secret",  # pragma: allowlist secret
    )  # pragma: allowlist secret
    solver._detect_challenge = AsyncMock(return_value="hcaptcha")  # type: ignore[method-assign]
    solver.solve = AsyncMock(  # type: ignore[method-assign]
        return_value=CaptchaSolveResult(
            solved=True,
            method="capsolver",
            tokens={"captcha_token": "token-secret"},
            cookies={"cf_clearance": "cookie-secret"},
        )
    )
    with capture_logs() as logs:
        await solver.apply_page(_FixturePage())
    rendered = str(logs)
    assert "provider-secret" not in rendered
    assert "token-secret" not in rendered
    assert "cookie-secret" not in rendered


def test_factory_reads_provider_key_from_environment_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPSOLVER_API_KEY", "env-test-key")
    solver = _create_captcha_solver({"provider": "capsolver", "api_key": "must-not-win"})
    assert solver._api_key == "env-test-key"  # pragma: allowlist secret


def test_new_captcha_providers_self_register_with_capabilities() -> None:
    providers = set(list_captcha_providers())
    assert {"capsolver", "capmonster", "nextcaptcha", "nopecha"} <= providers

    capsolver = get_captcha_provider_capability("capsolver")
    capmonster = get_captcha_provider_capability("capmonster")
    nextcaptcha = get_captcha_provider_capability("nextcaptcha")
    nopecha = get_captcha_provider_capability("nopecha")

    assert capsolver is not None and capsolver.production_candidate
    assert capmonster is not None and capmonster.production_candidate
    assert nextcaptcha is not None and nextcaptcha.benchmark_candidate
    assert "recaptcha_v3" in capsolver.supported_challenge_types
    assert "turnstile" in capsolver.supported_challenge_types
    assert "recaptcha_v3" in capmonster.supported_challenge_types
    assert nextcaptcha.supported_challenge_types == frozenset(
        {"recaptcha", "recaptcha_v3", "turnstile"}
    )
    assert nopecha is not None and nopecha.free_or_dev


def test_challenge_type_aliases_match_observe_labels() -> None:
    assert normalize_challenge_type("cloudflare") == "cloudflare_challenge"
    assert normalize_challenge_type("cloudflare_challenge") == "cloudflare_challenge"
    assert normalize_challenge_type("cf_turnstile") == "turnstile"
    assert normalize_challenge_type("recaptcha-v3") == "recaptcha_v3"


def test_provider_routes_normalize_strings_and_lists() -> None:
    assert _normalize_provider_routes(
        {
            "cloudflare": "browser_wait,manual_required",
            "recaptcha": ["capsolver", "capmonster"],
            "recaptcha-v3": ["capmonster"],
        }
    ) == {
        "cloudflare_challenge": ("browser_wait", "manual_required"),
        "recaptcha": ("capsolver", "capmonster"),
        "recaptcha_v3": ("capmonster",),
    }


def test_factory_reads_new_provider_keys_from_environment_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPMONSTER_API_KEY", "capmonster-env-test-key")
    capmonster = _create_captcha_solver(
        {"provider": "capmonster", "api_key": "must-not-win"}  # pragma: allowlist secret
    )
    assert capmonster._api_key == "capmonster-env-test-key"  # pragma: allowlist secret

    monkeypatch.setenv("NEXTCAPTCHA_API_KEY", "nextcaptcha-env-test-key")
    nextcaptcha = _create_captcha_solver(
        {"provider": "nextcaptcha", "api_key": "must-not-win"}  # pragma: allowlist secret
    )
    assert nextcaptcha._api_key == "nextcaptcha-env-test-key"  # pragma: allowlist secret


@pytest.mark.asyncio
async def test_navigate_solves_in_current_page_before_reloading() -> None:
    responses = iter([SimpleNamespace(status=503), SimpleNamespace(status=200)])
    page = SimpleNamespace(
        goto=AsyncMock(side_effect=lambda *args, **kwargs: next(responses)),
    )
    controller = SimpleNamespace(solve_page_challenge=AsyncMock(return_value=True))

    await navigate(
        page,
        "https://example.test/jobs",
        {"challenge_retries": 0, "_bypass_strategy": controller},
    )

    controller.solve_page_challenge.assert_awaited_once_with(
        page,
        url="https://example.test/jobs",
    )
    assert page.goto.await_count == 2


@pytest.mark.asyncio
async def test_navigate_solves_embedded_captcha_on_success_status() -> None:
    from job_ftch.infrastructure.sources.browser_utils import navigate

    responses = iter([SimpleNamespace(status=200), SimpleNamespace(status=200)])
    page = SimpleNamespace(
        goto=AsyncMock(side_effect=lambda *args, **kwargs: next(responses)),
        evaluate=AsyncMock(return_value=True),
    )
    controller = SimpleNamespace(solve_page_challenge=AsyncMock(return_value=True))

    await navigate(
        page,
        "https://example.test/jobs",
        {"challenge_retries": 0, "_bypass_strategy": controller},
    )

    controller.solve_page_challenge.assert_awaited_once_with(
        page,
        url="https://example.test/jobs",
    )
    assert page.goto.await_count == 2


@pytest.mark.asyncio
async def test_navigate_preserves_page_after_token_solution() -> None:
    from job_ftch.infrastructure.sources.browser_utils import navigate

    page = SimpleNamespace(
        goto=AsyncMock(return_value=SimpleNamespace(status=200)),
        evaluate=AsyncMock(return_value=True),
    )
    controller = SimpleNamespace(
        challenge_solution_requires_reload=False,
        solve_page_challenge=AsyncMock(return_value=True),
    )

    await navigate(
        page,
        "https://example.test/jobs",
        {"challenge_retries": 0, "_bypass_strategy": controller},
    )

    controller.solve_page_challenge.assert_awaited_once()
    page.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_navigate_skips_terminal_solver_outcome() -> None:
    from job_ftch.infrastructure.sources.browser_utils import navigate

    page = SimpleNamespace(
        goto=AsyncMock(return_value=SimpleNamespace(status=200)),
        evaluate=AsyncMock(return_value=True),
    )
    controller = SimpleNamespace(
        challenge_solver_terminal=True,
        solve_page_challenge=AsyncMock(return_value=False),
    )

    await navigate(
        page,
        "https://example.test/jobs",
        {"challenge_retries": 0, "_bypass_strategy": controller},
    )

    controller.solve_page_challenge.assert_not_awaited()


@pytest.mark.asyncio
async def test_navigate_solves_observed_challenge_without_response_object() -> None:
    page = SimpleNamespace(
        goto=AsyncMock(return_value=None),
        evaluate=AsyncMock(return_value=False),
    )
    controller = SimpleNamespace(
        observed_challenge_type="cloudflare_challenge",
        solve_page_challenge=AsyncMock(return_value=True),
    )

    await navigate(
        page,
        "https://example.test/jobs",
        {"challenge_retries": 0, "_bypass_strategy": controller},
    )

    controller.solve_page_challenge.assert_awaited_once_with(
        page,
        url="https://example.test/jobs",
    )
    assert page.goto.await_count == 2


@pytest.mark.asyncio
async def test_navigate_does_not_solve_success_status_without_captcha_marker() -> None:
    from job_ftch.infrastructure.sources.browser_utils import navigate

    page = SimpleNamespace(
        goto=AsyncMock(return_value=SimpleNamespace(status=200)),
        evaluate=AsyncMock(return_value=False),
    )
    controller = SimpleNamespace(solve_page_challenge=AsyncMock(return_value=True))

    await navigate(
        page,
        "https://example.test/jobs",
        {"challenge_retries": 0, "_bypass_strategy": controller},
    )

    controller.solve_page_challenge.assert_not_awaited()
    page.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_provider_solving_blocked_on_unauthorized_domain() -> None:
    # Owner scope: provider-backed solving is refused off the allowlist, while
    # the free browser_wait tier stays available everywhere.
    solver = CaptchaSolverBypass(
        provider="capsolver",
        api_key="k",
        authorized_domains=frozenset({"example.com"}),
    )
    page = SimpleNamespace(url="https://evil.test/checkout")
    with capture_logs() as logs:
        result = await solver.solve(page, challenge_type="recaptcha", url="https://evil.test/x")
    assert not result.solved
    assert result.failure_reason is CaptchaFailureReason.UNAUTHORIZED_DOMAIN
    assert any(entry["event"] == "captcha_provider_blocked_unauthorized" for entry in logs)


@pytest.mark.asyncio
async def test_provider_solving_allowed_on_authorized_subdomain() -> None:
    # A parent-suffix allowlist entry authorizes its subdomains; the gate must
    # not be the reason a solve fails here.
    solver = CaptchaSolverBypass(
        provider="capsolver",
        api_key="",  # empty key -> fails downstream, but NOT at the auth gate
        authorized_domains=frozenset({"example.com"}),
    )
    page = SimpleNamespace(url="https://jobs.example.com/apply")
    result = await solver.solve(
        page, challenge_type="recaptcha", url="https://jobs.example.com/apply"
    )
    assert result.failure_reason is not CaptchaFailureReason.UNAUTHORIZED_DOMAIN


def test_domain_authorization_suffix_matching() -> None:
    solver = CaptchaSolverBypass(authorized_domains=frozenset({"example.com", "acme.io"}))
    assert solver._domain_authorized("example.com")
    assert solver._domain_authorized("jobs.example.com")
    assert solver._domain_authorized("deep.sub.acme.io")
    assert not solver._domain_authorized("notexample.com")
    assert not solver._domain_authorized("example.org")
    assert not solver._domain_authorized("")


def test_empty_allowlist_authorizes_nothing() -> None:
    solver = CaptchaSolverBypass(authorized_domains=frozenset())
    assert not solver._domain_authorized("example.com")
