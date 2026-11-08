"""Provider deadlines are lower bounds; generic exhaustion is not a quota diagnosis."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from job_ftch.infrastructure.llm.dispatch_policy import classify_response, retry_time

NOW = datetime(2026, 11, 8, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", [False, True])
async def test_worker_connects_telemetry_only_for_cliproxy(monkeypatch, protected):
    from unittest.mock import AsyncMock, Mock

    pytest.importorskip("procrastinate")
    from job_ftch.config import LLMDispatchPolicy, LLMProviderBinding, LLMProviderProfile, Settings
    from job_ftch.infrastructure.llm.dispatch_worker import LLMWorker

    settings = Settings(
        store_dsn="postgresql://test@localhost/test",
        llm_dispatch_policies={"route": LLMDispatchPolicy()},
        llm_bindings={"extraction": LLMProviderBinding(provider="openai_main", model="test")},
        llm_provider_profiles={
            "openai_main": LLMProviderProfile(
                gateway="cliproxy" if protected else "direct",
                execution="queued",
                quota_scope="route",
            )
        },
    )
    configure = Mock()
    monkeypatch.setattr("job_ftch.infrastructure.observability.configure_observability", configure)
    monkeypatch.setattr("job_ftch.application.logging.configure_logging", Mock())
    worker = LLMWorker(settings)
    monkeypatch.setattr(worker.store, "open", AsyncMock())
    monkeypatch.setattr(worker.store.app, "run_worker_async", AsyncMock())
    monkeypatch.setattr(worker, "maintain", AsyncMock())
    monkeypatch.setattr(worker, "close", AsyncMock())
    await worker.run()
    if protected:
        configure.assert_called_once_with(settings)
    else:
        configure.assert_not_called()


@pytest.mark.parametrize("value", ["NaN", "inf", "-inf", "not-a-date", "1e999"])
def test_invalid_retry_headers_have_finite_fallback(value):
    assert retry_time(value, now=NOW, default_seconds=60) == NOW + timedelta(seconds=60)


def test_structured_quota_uses_longest_deadline_including_wrapped_metadata():
    response = httpx.Response(
        429,
        headers={"Retry-After": "120"},
        json={
            "error": {
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "QUOTA_EXHAUSTED",
                        "metadata": {
                            "quotaResetDelay": "108h48m20.5s",
                            "quotaResetTimeStamp": "2026-11-13T00:00:00Z",
                        },
                    },
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "391609.75s",
                    },
                ],
            }
        },
    )
    signal = classify_response(response, now=NOW, default_seconds=60)
    assert signal.reason == "quota"
    assert signal.retry_at == datetime(2026, 11, 13, tzinfo=UTC)
    assert not signal.operator


def test_plain_resource_exhausted_does_not_invent_thirty_minute_quota_wait():
    signal = classify_response(
        httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}}),
        now=NOW,
        default_seconds=60,
    )
    assert signal.reason == "rate_limit"
    assert signal.retry_at == NOW + timedelta(seconds=60)


def test_model_cooldown_preserves_multi_day_wait():
    signal = classify_response(
        httpx.Response(429, json={"error": {"code": "model_cooldown", "reset_seconds": 506177}}),
        now=NOW,
        default_seconds=60,
    )
    assert signal.reason == "quota"
    assert signal.retry_at == NOW + timedelta(seconds=506177)


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (403, {"details": [{"reason": "VALIDATION_REQUIRED"}]}, "verification_required"),
        (401, {}, "auth"),
        (402, {}, "billing_required"),
    ],
)
def test_account_refusal_requires_operator_instead_of_retry(status, body, reason):
    signal = classify_response(
        httpx.Response(status, json={"error": body}), now=NOW, default_seconds=60
    )
    assert signal.operator and signal.retry_at is None
    assert signal.reason == reason


@pytest.mark.asyncio
async def test_only_selected_cliproxy_text_routes_start_run_lifecycle(monkeypatch):
    pytest.importorskip("procrastinate")
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from job_ftch.application.tenant_runner import TenantRunner
    from job_ftch.config import Settings
    from job_ftch.infrastructure.llm.dispatch_session import CliproxyRun

    settings = Settings.model_validate(
        {
            "store_dsn": "postgresql://unused@127.0.0.1:1/unreachable",
            "llm_dispatch_policies": {"proxy": {}},
            "llm_provider_profiles": {
                "direct": {},
                "proxy": {"gateway": "cliproxy", "execution": "queued"},
            },
            "llm_bindings": {"extraction": {"provider": "direct", "model": "direct-model"}},
        }
    )
    runner = object.__new__(TenantRunner)
    monkeypatch.setattr(runner, "get_runtime", lambda _: SimpleNamespace(settings=settings))
    expected = object()
    call = AsyncMock(return_value=expected)
    monkeypatch.setattr(runner, "_run_tenant", call)

    def forbidden(*args, **kwargs):
        pytest.fail("unused CLIProxy profile must not connect PostgreSQL or delay direct API")

    monkeypatch.setattr(CliproxyRun, "__init__", forbidden)
    assert await runner.run_tenant("direct-tenant", max_items=7) is expected
    call.assert_awaited_once()
    assert call.call_args.kwargs["max_items"] == 7


@pytest.mark.asyncio
async def test_queue_connection_failure_preserves_work_as_deferred(monkeypatch):
    pytest.importorskip("procrastinate")
    from psycopg import OperationalError

    from job_ftch.config import Settings
    from job_ftch.infrastructure.llm.dispatch_transport import QueuedLLMTransport

    settings = Settings.model_validate(
        {
            "store_dsn": "postgresql://unused@127.0.0.1:1/unreachable",
            "llm_dispatch_policies": {"proxy": {}},
            "llm_provider_profiles": {"proxy": {"gateway": "cliproxy", "execution": "queued"}},
            "llm_bindings": {"extraction": {"provider": "proxy", "model": "test-model"}},
        }
    )
    transport = QueuedLLMTransport(settings, "proxy")

    async def unavailable(*args, **kwargs):
        raise OperationalError("unavailable")

    monkeypatch.setattr(transport.store, "submit", unavailable)
    response = await transport.handle_async_request(
        httpx.Request(
            "POST", "https://ignored.invalid/v1/chat/completions", json={"model": "test-model"}
        )
    )
    assert response.status_code == 425
    assert response.json()["error"]["reason"] == "queue_unavailable"
    assert not transport.store._opened
    await transport.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", [False, True])
async def test_external_deadline_preserves_only_durable_calls(protected):
    import asyncio
    from types import SimpleNamespace

    from job_ftch.application.llm_dispatch import LLMRequestDeferredError, llm_deadline

    expected = LLMRequestDeferredError if protected else TimeoutError
    with pytest.raises(expected):
        async with llm_deadline(0.01, SimpleNamespace(durable_dispatch=protected)):
            await asyncio.sleep(1)


@pytest.mark.asyncio
@pytest.mark.parametrize("valid_second", [False, True])
async def test_schema_correction_is_one_real_repair_not_camouflage(valid_second):
    import json

    pytest.importorskip("instructor")
    from pydantic import BaseModel

    from job_ftch.application.llm_dispatch import LLMRequestDeferredError
    from job_ftch.infrastructure.llm.openai_provider import OpenAIInstructorLLMProvider

    class Fields(BaseModel):
        title: str

    sent = []

    def upstream(request):
        sent.append(json.loads(request.content))
        valid = valid_second and len(sent) == 2
        return httpx.Response(
            200,
            json={
                "id": "completion",
                "object": "chat.completion",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"title":"Engineer"}' if valid else '{"wrong":true}',
                        },
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    provider = OpenAIInstructorLLMProvider(
        api_key="test-only",  # pragma: allowlist secret
        model="test-model",
        base_url="https://ignored.invalid/v1",
        timeout_seconds=3,
        max_retries=0,
        http_client=client,
        structured_mode="json",
        cliproxy_protection=True,
    )
    if valid_second:
        assert (await provider.extract("A genuine vacancy", Fields)).title == "Engineer"
    else:
        with pytest.raises(LLMRequestDeferredError) as held:
            await provider.extract("A genuine vacancy", Fields)
        assert held.value.reason == "schema_validation_failed"
        assert held.value.requires_operator
    assert len(sent) == 2
    assert "previous response failed schema validation" not in str(sent[0]).lower()
    assert "previous response failed schema validation" in str(sent[1]).lower()
    await client.aclose()
