"""Optional dispatch contracts, including real PostgreSQL durability when configured."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from job_ftch.application.llm_dispatch import (
    LLMRequestDeferredError,
    deferred_from_error,
    llm_item_session,
    llm_session,
)
from job_ftch.config import Settings


@pytest.fixture(scope="session")
def event_loop_policy():
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


def test_inline_profiles_need_no_queue_configuration() -> None:
    settings = Settings.model_validate({"llm_backend": "heuristic"})
    assert not settings.llm_dispatch_policies
    assert all(p.execution == "inline" for p in settings.llm_provider_profiles.values())


@pytest.mark.parametrize("home", [False, True])
def test_production_overlays_preserve_endpoint_credentials_and_capabilities(monkeypatch, home):
    paths = [
        "config/runtime.yaml",
        "config/runtime.prod.yaml",
        "job_ftch/adapters/telegram_bot/runtime.prod.yaml",
        "config/runtime.llm_queue.yaml",
    ]
    if home:
        paths.append("config/runtime.home_egress.yaml")
    monkeypatch.setenv("JOB_FTCH_RUNTIME_CONFIG_PATH", ";".join(paths))
    settings = Settings(_env_file=None, store_dsn="postgresql://test@localhost/test")
    for name in ("cliproxy_main", "cliproxy_captcha"):
        profile = settings.llm_provider_profiles[name]
        assert profile.base_url == "http://cliproxy:8317/v1"
        assert profile.credential_ref == "JOB_FTCH_CLIPROXY_API_KEY"
        assert (profile.execution, profile.quota_scope, profile.session_affinity) == (
            "queued",
            "google_primary",
            True,
        )
        assert bool(profile.egress_ready_url) == home
        assert profile.cliproxy_dispatch
    assert "vision" in settings.llm_provider_profiles["cliproxy_captcha"].capabilities
    assert settings.llm_provider_profiles["openai_main"].execution == "inline"
    assert not settings.llm_provider_profiles["openai_main"].cliproxy_dispatch


def test_queued_profile_requires_explicit_policy_and_postgres() -> None:
    with pytest.raises(ValidationError, match="dispatch policy"):
        Settings.model_validate(
            {
                "llm_provider_profiles": {
                    "test": {"execution": "queued"},
                }
            }
        )
    with pytest.raises(ValidationError, match="PostgreSQL"):
        Settings.model_validate(
            {
                "store_dsn": "sqlite:///test.db",
                "llm_dispatch_policies": {"test": {}},
                "llm_provider_profiles": {"test": {"execution": "queued"}},
            }
        )


def test_invalid_settings_do_not_log_credential_inputs() -> None:
    with pytest.raises(ValidationError) as invalid:
        Settings.model_validate(
            {
                "openai_api_key": "private-placeholder",  # pragma: allowlist secret
                "pipeline_item_concurrency": -1,
            }
        )
    assert "private-placeholder" not in str(invalid.value)


def test_sdk_pending_signal_and_session_context_do_not_leak() -> None:
    due = datetime(2026, 11, 8, tzinfo=UTC)
    error = RuntimeError("SDK wrapper")
    cause = RuntimeError("HTTP 425")
    cause.body = {"code": "llm_pending", "request_id": "request-a", "retry_at": due.isoformat()}
    error.__cause__ = cause
    pending = deferred_from_error(error)
    assert isinstance(pending, LLMRequestDeferredError)
    assert (pending.request_id, pending.retry_at) == ("request-a", due)
    with llm_item_session("tenant-a:item-7"):
        assert llm_session.get() == "tenant-a:item-7"
    assert llm_session.get() == ""


def test_retry_after_accepts_seconds_and_http_date() -> None:
    pytest.importorskip("procrastinate")
    from job_ftch.infrastructure.llm.dispatch_worker import retry_time

    now = datetime(2026, 11, 8, tzinfo=UTC)
    assert retry_time("120", now=now, default_seconds=3) == now + timedelta(seconds=120)
    assert retry_time(
        "Sun, 08 Nov 2026 00:02:00 GMT", now=now, default_seconds=3
    ) == now + timedelta(seconds=120)
    assert retry_time("invalid", now=now, default_seconds=3) == now + timedelta(seconds=3)


@pytest.fixture
async def dispatch_settings(monkeypatch):
    pytest.importorskip("procrastinate")
    dsn = os.environ.get("JOB_FTCH_TEST_LLM_QUEUE_DSN")
    if not dsn:
        pytest.skip("Set JOB_FTCH_TEST_LLM_QUEUE_DSN to a disposable PostgreSQL database")
    from job_ftch.infrastructure.llm.dispatch_store import DispatchStore

    name = f"test_{uuid.uuid4().hex}"
    monkeypatch.setenv("JOB_FTCH_TEST_DISPATCH_CREDENTIAL", "test-upstream-credential")
    settings = Settings.model_validate(
        {
            "tenant_id": name,
            "llm_backend": "openai",
            "llm_queue_dsn": dsn,
            "llm_dispatch_policies": {name: {"wait_seconds": 0.1, "min_interval_seconds": 10}},
            "llm_provider_profiles": {
                name: {
                    "execution": "queued",
                    "base_url": "https://upstream.invalid/v1",
                    "credential_ref": "JOB_FTCH_TEST_DISPATCH_CREDENTIAL",
                    "models": ["test-model"],
                    "session_affinity": True,
                }
            },
            "llm_bindings": {"extraction": {"provider": name, "model": "test-model"}},
        }
    )
    store = DispatchStore(settings)
    await store.initialize()
    yield settings, name
    async with store.pool.connection() as conn:
        await conn.execute("DELETE FROM jf_llm_requests WHERE tenant_id=%s", (name,))
        await conn.execute("DELETE FROM jf_llm_scopes WHERE scope=%s", (name,))
        await conn.execute("DELETE FROM jf_llm_model_state WHERE scope=%s", (name,))
        await conn.execute("DELETE FROM jf_llm_runs WHERE tenant_id=%s", (name,))
        await conn.execute("DELETE FROM procrastinate_jobs WHERE lock=%s", (f"llm:{name}",))
    await store.close()


def payload(text: str = "A") -> dict:
    return {"model": "test-model", "messages": [{"role": "user", "content": text}], "max_tokens": 8}


def protect(settings, name):
    settings.llm_provider_profiles[name].gateway = "cliproxy"
    policy = settings.llm_dispatch_policies[name]
    policy.jitter_seconds = 0
    policy.run_jitter_seconds = 0
    return policy


@pytest.mark.asyncio
async def test_inline_affinity_needs_no_queue_and_keeps_item_session(monkeypatch):
    from job_ftch.infrastructure.llm import openai_provider

    settings = Settings.model_validate(
        {
            "openai_api_key": "test-placeholder",  # pragma: allowlist secret
            "llm_queue_profile": "inline",
            "llm_provider_profiles": {"inline": {"session_affinity": True}},
            "llm_bindings": {"extraction": {"provider": "inline", "model": "test-model"}},
        }
    )
    monkeypatch.setattr(openai_provider, "OpenAIInstructorLLMProvider", lambda **kwargs: kwargs)
    client = openai_provider._build_openai_llm(settings)["http_client"]
    sessions = []

    async def upstream(request):
        sessions.append(request.headers["x-session-id"])
        return httpx.Response(200)

    client._transport = httpx.MockTransport(upstream)
    async with client:
        with llm_item_session("tenant:item-one"):
            await client.post("https://inline.invalid", json={"step": 1})
            await client.post("https://inline.invalid", json={"step": 2})
        with llm_item_session("tenant:item-two"):
            await client.post("https://inline.invalid", json={"step": 1})
    assert sessions[0] == sessions[1]
    assert sessions[0] != sessions[2]


@pytest.mark.asyncio
async def test_offline_egress_reserves_no_budget_or_attempt(dispatch_settings, monkeypatch):
    from job_ftch.infrastructure.llm.dispatch_worker import DispatchWait, LLMWorker

    settings, name = dispatch_settings
    settings.llm_provider_profiles[name].egress_ready_url = "http://home.invalid/ready"
    worker = LLMWorker(settings)
    request_id = await worker.store.submit(name, name, "offline", payload())
    real_client = httpx.AsyncClient
    seen = []

    def offline(request):
        seen.append(str(request.url))
        return httpx.Response(503)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(offline)),
    )
    try:
        with pytest.raises(DispatchWait):
            await worker.execute(request_id)
        row = await worker.store.get(request_id)
        assert (row["state"], row["attempts"], row["reserved_tokens"]) == ("pending", 0, 0)
        assert seen == ["http://home.invalid/ready"]
        assert not (await worker.store.stats())["scopes"] or all(
            scope["scope"] != name for scope in (await worker.store.stats())["scopes"]
        )
    finally:
        await worker.store.close()


@pytest.mark.asyncio
async def test_lost_broker_outcome_pauses_scope_without_resending(dispatch_settings):
    from job_ftch.infrastructure.llm.dispatch_worker import LLMWorker

    settings, name = dispatch_settings
    worker = LLMWorker(settings)
    request_id = await worker.store.submit(name, name, "lost", payload())
    async with worker.store.pool.connection() as conn:
        await conn.execute("INSERT INTO jf_llm_scopes(scope) VALUES (%s)", (name,))
        await conn.execute(
            "UPDATE jf_llm_requests SET state='running',attempts=1 WHERE request_id=%s",
            (request_id,),
        )
        await conn.execute(
            "UPDATE procrastinate_jobs SET status='failed' WHERE id="
            "(SELECT job_id FROM jf_llm_requests WHERE request_id=%s)",
            (request_id,),
        )
    try:
        await worker.maintain()
        await worker.execute(request_id)
        row = await worker.store.get(request_id)
        assert (row["state"], row["attempts"]) == ("uncertain", 1)
        scopes = (await worker.store.stats())["scopes"]
        assert (
            next(scope for scope in scopes if scope["scope"] == name)["paused_reason"]
            == "uncertain"
        )
    finally:
        await worker.store.close()


@pytest.mark.asyncio
async def test_atomic_enqueue_idempotency_tenant_isolation_and_saved_response(dispatch_settings):
    from job_ftch.infrastructure.llm.dispatch_store import DispatchStore
    from job_ftch.infrastructure.llm.dispatch_worker import LLMWorker

    settings, name = dispatch_settings
    store = DispatchStore(settings)
    ids = await asyncio.gather(
        *(store.submit(name, name, "session-1", payload()) for _ in range(8))
    )
    assert len(set(ids)) == 1
    row = await store.get(ids[0])
    assert row["job_id"] is not None
    assert "Authorization" not in row["payload"]
    sent = []

    async def upstream(request):
        sent.append(request)
        assert request.headers["authorization"] == "Bearer test-upstream-credential"
        assert request.headers["x-session-id"] == "session-1"
        return httpx.Response(200, json={"id": "completion-7", "choices": []})

    worker = LLMWorker(settings, transport=httpx.MockTransport(upstream))
    await worker.execute(ids[0])
    await worker.execute(ids[0])
    assert len(sent) == 1
    assert (await store.get(ids[0]))["response"]["id"] == "completion-7"
    other = await store.submit(name, name + "_other", "session-1", payload())
    assert other != ids[0]
    async with store.pool.connection() as conn:
        await conn.execute("DELETE FROM jf_llm_requests WHERE tenant_id=%s", (name + "_other",))
    await worker.store.close()
    await store.close()


@pytest.mark.asyncio
async def test_retention_removes_completed_requests_and_broker_history_only(dispatch_settings):
    from job_ftch.infrastructure.llm.dispatch_worker import LLMWorker

    settings, name = dispatch_settings
    worker = LLMWorker(settings)
    old = await worker.store.submit(name, name, "old", payload("old"))
    pending = await worker.store.submit(name, name, "pending", payload("pending"))
    job_id = (await worker.store.get(old))["job_id"]
    async with worker.store.pool.connection() as conn:
        await conn.execute(
            "UPDATE jf_llm_requests SET state='succeeded',updated_at=now()-interval '8 days' "
            "WHERE request_id=%s",
            (old,),
        )
        await conn.execute(
            "UPDATE procrastinate_jobs SET status='succeeded' WHERE id=%s", (job_id,)
        )
        await conn.execute(
            "UPDATE procrastinate_events SET at=now()-interval '8 days' WHERE job_id=%s",
            (job_id,),
        )
    try:
        await worker.maintain()
        assert (await worker.store.get(pending))["state"] == "pending"
        async with worker.store.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT count(*) AS count FROM procrastinate_jobs WHERE id=%s",
                    (job_id,),
                )
            ).fetchone()
            assert row["count"] == 0
            row = await (
                await conn.execute(
                    "SELECT count(*) AS count FROM jf_llm_requests WHERE request_id=%s",
                    (old,),
                )
            ).fetchone()
            assert row["count"] == 0
    finally:
        await worker.store.close()


@pytest.mark.asyncio
async def test_429_persists_shared_cooldown_across_worker_restart(dispatch_settings):
    from job_ftch.infrastructure.llm.dispatch_worker import DispatchWait, LLMWorker

    settings, name = dispatch_settings
    requests = []

    async def limited(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"}, json={"error": "limited"})

    worker = LLMWorker(settings, transport=httpx.MockTransport(limited))
    first = await worker.store.submit(name, name, "s1", payload("one"))
    second = await worker.store.submit(name, name, "s2", payload("two"))
    before = datetime.now(UTC)
    with pytest.raises(DispatchWait) as retry:
        await worker.execute(first)
    assert retry.value.retry_at >= before + timedelta(seconds=120)
    await worker.store.close()
    restarted = LLMWorker(settings, transport=httpx.MockTransport(limited))
    with pytest.raises(DispatchWait) as blocked:
        await restarted.execute(second)
    assert blocked.value.retry_at >= retry.value.retry_at
    assert len(requests) == 1
    assert (await restarted.store.get(first))["attempts"] == 1
    assert (await restarted.store.get(second))["attempts"] == 0
    await restarted.store.close()


@pytest.mark.asyncio
async def test_auth_failure_pauses_account_and_unknown_outcome_is_not_replayed(dispatch_settings):
    from job_ftch.infrastructure.llm.dispatch_worker import DispatchWait, LLMWorker

    settings, name = dispatch_settings
    sent = []

    async def forbidden(request):
        sent.append(request)
        return httpx.Response(403, json={"error": "forbidden"})

    worker = LLMWorker(settings, transport=httpx.MockTransport(forbidden))
    first = await worker.store.submit(name, name, "s1", payload("one"))
    second = await worker.store.submit(name, name, "s2", payload("two"))
    await worker.execute(first)
    with pytest.raises(DispatchWait):
        await worker.execute(second)
    assert len(sent) == 1
    async with worker.store.pool.connection() as conn:
        await conn.execute(
            "UPDATE jf_llm_scopes SET paused_reason=NULL,not_before=now() WHERE scope=%s", (name,)
        )
        await conn.execute(
            "UPDATE jf_llm_requests SET next_run_at=now() WHERE request_id=%s", (second,)
        )
    await worker.store.close()

    async def lost(request):
        raise httpx.ReadTimeout("response lost", request=request)

    restarted = LLMWorker(settings, transport=httpx.MockTransport(lost))
    await restarted.execute(second)
    assert (await restarted.store.get(second))["state"] == "uncertain"
    await restarted.execute(second)
    assert (await restarted.store.get(second))["attempts"] == 1
    await restarted.store.close()


@pytest.mark.asyncio
async def test_pending_transport_returns_handle_and_replays_saved_response(dispatch_settings):
    from job_ftch.infrastructure.llm.dispatch_transport import QueuedLLMTransport
    from job_ftch.infrastructure.llm.dispatch_worker import LLMWorker

    settings, name = dispatch_settings
    transport = QueuedLLMTransport(settings, name)
    request = httpx.Request("POST", "https://ignored.invalid/v1/chat/completions", json=payload())
    response = await transport.handle_async_request(request)
    assert response.status_code == 425
    request_id = response.json()["error"]["request_id"]
    worker = LLMWorker(
        settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"id": "saved"}))
    )
    await worker.execute(request_id)
    replay = await transport.handle_async_request(request)
    assert replay.status_code == 200
    assert replay.json() == {"id": "saved"}
    await worker.store.close()
    await transport.aclose()


@pytest.mark.asyncio
async def test_real_procrastinate_workers_serialize_shared_account(dispatch_settings):
    from job_ftch.infrastructure.llm.dispatch_worker import LLMWorker

    settings, name = dispatch_settings
    settings.llm_dispatch_policies[name].min_interval_seconds = 0
    active = 0
    maximum = 0
    seen = []

    async def upstream(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        seen.append(request.content)
        await asyncio.sleep(0.02)
        active -= 1
        return httpx.Response(200, json={"id": "broker-result"})

    first = LLMWorker(settings, transport=httpx.MockTransport(upstream))
    second = LLMWorker(settings, transport=httpx.MockTransport(upstream))
    ids = [await first.store.submit(name, name, f"s{i}", payload(str(i))) for i in range(3)]
    await second.store.open()
    try:
        tasks = [
            asyncio.create_task(
                worker.store.app.run_worker_async(
                    queues=["llm"],
                    concurrency=3,
                    install_signal_handlers=False,
                    fetch_job_polling_interval=0.05,
                )
            )
            for worker in (first, second)
        ]

        async def completed():
            while True:
                rows = [await first.store.get(request_id) for request_id in ids]
                if all(row["state"] == "succeeded" for row in rows):
                    return rows
                await asyncio.sleep(0.05)

        try:
            rows = await asyncio.wait_for(completed(), 30)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert [row["state"] for row in rows] == ["succeeded"] * 3
        assert len(seen) == 3
        assert maximum == 1
    finally:
        await first.store.close()
        await second.store.close()


@pytest.mark.asyncio
async def test_real_instructor_propagates_pending_instead_of_retrying(
    dispatch_settings, monkeypatch
):
    real_instructor = pytest.importorskip("instructor")
    real_openai = pytest.importorskip("openai")
    from pydantic import BaseModel

    from job_ftch.infrastructure.llm import openai_provider
    from job_ftch.infrastructure.llm.dispatch_transport import QueuedLLMTransport

    settings, name = dispatch_settings
    monkeypatch.setattr(openai_provider, "instructor", real_instructor)
    monkeypatch.setattr(openai_provider, "AsyncOpenAI", real_openai.AsyncOpenAI)
    monkeypatch.setattr(openai_provider, "_INSTRUCTOR_AVAILABLE", True)
    monkeypatch.setattr(openai_provider, "_OPENAI_AVAILABLE", True)
    client = httpx.AsyncClient(transport=QueuedLLMTransport(settings, name))
    provider = openai_provider.OpenAIInstructorLLMProvider(
        api_key="test-placeholder",  # pragma: allowlist secret
        model="test-model",
        base_url="https://queue.invalid/v1",
        timeout_seconds=10,
        max_retries=0,
        http_client=client,
    )

    class Result(BaseModel):
        title: str

    try:
        with pytest.raises(LLMRequestDeferredError):
            await provider.extract("one vacancy", Result)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_cliproxy_sessions_resume_then_rotate_and_keep_item_requests_distinct(
    dispatch_settings,
):
    from job_ftch.application.llm_dispatch import llm_run
    from job_ftch.infrastructure.llm.dispatch_session import CliproxyRun
    from job_ftch.infrastructure.llm.dispatch_store import profile_revision
    from job_ftch.infrastructure.llm.dispatch_transport import QueuedLLMTransport
    from job_ftch.infrastructure.llm.dispatch_worker import LLMWorker

    settings, name = dispatch_settings
    policy = protect(settings, name)
    transport = QueuedLLMTransport(settings, name)
    request = httpx.Request("POST", "https://ignored.invalid/v1/chat/completions", json=payload())
    async with CliproxyRun(settings, name) as session:
        first_run = session.run_id
        ids = []
        for item in ("one", "two"):
            with llm_item_session(item):
                reply = await transport.handle_async_request(request)
                ids.append(reply.json()["error"]["request_id"])
        assert ids[0] != ids[1]
        rows = [await transport.store.get(key) for key in ids]
        assert rows[0]["session_id"] == rows[1]["session_id"]
        assert rows[0]["logical_run_id"] == first_run
        await session.finish({"completion_state": "waiting_llm"})
    assert not llm_run.get()
    original_revision = profile_revision(settings, name)
    policy.min_interval_seconds = 40
    policy.jitter_seconds = 4
    assert profile_revision(settings, name) == original_revision
    async with CliproxyRun(settings, name) as restarted:
        assert restarted.run_id == first_run
        with llm_item_session("one"):
            reply = await transport.handle_async_request(request)
        assert reply.json()["error"]["request_id"] == ids[0]
        worker = LLMWorker(
            settings,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"id": "done"})),
        )
        for key in ids:
            async with transport.store.pool.connection() as conn:
                await conn.execute(
                    "UPDATE jf_llm_scopes SET not_before=now() WHERE scope=%s", (name,)
                )
                await conn.execute(
                    "UPDATE jf_llm_requests SET next_run_at=now() WHERE request_id=%s", (key,)
                )
            await worker.execute(key)
        await worker.close()
        await restarted.finish({"completion_state": "completed"})
    async with CliproxyRun(settings, name) as next_run:
        assert next_run.run_id != first_run
        await next_run.finish({"completion_state": "completed"})
    await transport.aclose()


@pytest.mark.asyncio
async def test_cliproxy_pacing_is_shared_by_two_workers_and_survives_restart(dispatch_settings):
    from job_ftch.infrastructure.llm.dispatch_worker import DispatchWait, LLMWorker

    settings, name = dispatch_settings
    policy = protect(settings, name)
    policy.min_interval_seconds = 0
    policy.jitter_seconds = 10
    policy.max_tokens_per_hour = 100_000_000
    sent = []

    async def upstream(request):
        sent.append(request)
        await asyncio.sleep(0.02)
        return httpx.Response(200, json={"id": "ok", "usage": {"total_tokens": 7}})

    workers = [LLMWorker(settings, transport=httpx.MockTransport(upstream)) for _ in range(2)]
    ids = [await workers[0].store.submit(name, name, f"s{i}", payload(str(i))) for i in range(2)]
    before = datetime.now(UTC)
    results = await asyncio.gather(
        *(worker.execute(key) for worker, key in zip(workers, ids, strict=True)),
        return_exceptions=True,
    )
    assert len(sent) == 1
    assert sum(isinstance(result, DispatchWait) for result in results) == 1
    scope = next(s for s in (await workers[0].store.stats())["scopes"] if s["scope"] == name)
    assert 30 <= (scope["not_before"] - before).total_seconds() <= 41
    assert scope["calls"] == 1 and scope["tokens"] == 7
    for worker in workers:
        await worker.close()
    restarted = LLMWorker(settings, transport=httpx.MockTransport(upstream))
    states = [(key, await restarted.store.get(key)) for key in ids]
    second = next(key for key, state in states if state["state"] == "pending")
    with pytest.raises(DispatchWait):
        await restarted.execute(second)
    assert len(sent) == 1
    assert (await restarted.store.get(second))["attempts"] == 0
    await restarted.close()


@pytest.mark.asyncio
async def test_cliproxy_capacity_and_operator_state_are_deferred_not_partial(dispatch_settings):
    from job_ftch.domain import RawItem, SourceKind
    from job_ftch.infrastructure.llm.dispatch_transport import QueuedLLMTransport
    from job_ftch.infrastructure.llm.dispatch_worker import LLMWorker
    from job_ftch.infrastructure.llm.openai_provider import OpenAIInstructorLLMProvider
    from job_ftch.nodes.extraction import ExtractionNode

    settings, name = dispatch_settings
    policy = protect(settings, name)
    policy.max_pending_per_tenant = 1
    transport = QueuedLLMTransport(settings, name)
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAIInstructorLLMProvider(
        api_key="test-only",  # pragma: allowlist secret
        model="test-model",
        base_url="https://ignored.invalid/v1",
        timeout_seconds=5,
        max_retries=0,
        http_client=client,
        structured_mode="json",
        cliproxy_protection=True,
    )
    node = ExtractionNode(provider)
    item = RawItem(
        source_kind=SourceKind.DEBUG,
        source_name="fixture",
        external_id="held",
        url="https://example.invalid/job",
        text="ML Engineer\nBuild evaluation systems",
    )
    with pytest.raises(LLMRequestDeferredError):
        await node.process(item)
    overflow = await transport.handle_async_request(
        httpx.Request(
            "POST", "https://ignored.invalid/v1/chat/completions", json=payload("different")
        )
    )
    assert overflow.status_code == 425 and overflow.json()["error"]["reason"] == "queue_capacity"
    async with transport.store.pool.connection() as conn:
        rows = await (
            await conn.execute("SELECT request_id FROM jf_llm_requests WHERE tenant_id=%s", (name,))
        ).fetchall()
    assert len(rows) == 1
    worker = LLMWorker(
        settings,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                403, json={"error": {"details": [{"reason": "VALIDATION_REQUIRED"}]}}
            )
        ),
    )
    await worker.execute(rows[0]["request_id"])
    with pytest.raises(LLMRequestDeferredError) as held:
        await node.process(item)
    assert held.value.requires_operator
    assert held.value.reason == "verification_required"
    assert (await worker.store.get(rows[0]["request_id"]))["attempts"] == 1
    await worker.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_cliproxy_circuit_survives_restart_and_closes_after_recovery(dispatch_settings):
    from job_ftch.infrastructure.llm.dispatch_worker import DispatchWait, LLMWorker

    settings, name = dispatch_settings
    policy = protect(settings, name)
    policy.circuit_failure_threshold = 1
    sent = []

    def upstream(request):
        sent.append(request)
        return httpx.Response(503 if len(sent) == 1 else 200, json={"id": "response"})

    worker = LLMWorker(settings, transport=httpx.MockTransport(upstream))
    first = await worker.store.submit(name, name, "one", payload())
    second = await worker.store.submit(name, name, "two", payload("next"))
    with pytest.raises(DispatchWait):
        await worker.execute(first)
    await worker.close()
    restarted = LLMWorker(settings, transport=httpx.MockTransport(upstream))
    with pytest.raises(DispatchWait):
        await restarted.execute(second)
    assert len(sent) == 1
    state = next(m for m in (await restarted.store.stats())["models"] if m["scope"] == name)
    assert state["circuit_open"]
    async with restarted.store.pool.connection() as conn:
        await conn.execute("UPDATE jf_llm_model_state SET not_before=now() WHERE scope=%s", (name,))
        await conn.execute("UPDATE jf_llm_scopes SET not_before=now() WHERE scope=%s", (name,))
        await conn.execute(
            "UPDATE jf_llm_requests SET next_run_at=now() WHERE request_id=%s", (second,)
        )
    await restarted.execute(second)
    assert len(sent) == 2
    state = next(m for m in (await restarted.store.stats())["models"] if m["scope"] == name)
    assert not state["circuit_open"] and state["failures"] == 0
    await restarted.close()


@pytest.mark.asyncio
async def test_cliproxy_reports_are_once_per_completed_run_and_share_session(dispatch_settings):
    from job_ftch.infrastructure.llm.dispatch_reports import enqueue_run_reports
    from job_ftch.infrastructure.llm.dispatch_session import CliproxyRun
    from job_ftch.infrastructure.llm.dispatch_transport import QueuedLLMTransport
    from job_ftch.infrastructure.llm.dispatch_worker import LLMWorker

    settings, name = dispatch_settings
    protect(settings, name).run_summary_enabled = True
    transport = QueuedLLMTransport(settings, name)
    worker = LLMWorker(
        settings,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"id": "ok", "usage": {"total_tokens": 9}})
        ),
    )
    async with CliproxyRun(settings, name) as session:
        run_id = session.run_id
        reply = await transport.handle_async_request(
            httpx.Request("POST", "https://ignored.invalid/v1/chat/completions", json=payload())
        )
        key = reply.json()["error"]["request_id"]
        await worker.execute(key)
        await session.finish(
            {
                "completion_state": "completed",
                "fetched": 2,
                "emitted": 1,
                "private_text": "must not enter report",
            }
        )
    await enqueue_run_reports(worker.store)
    await enqueue_run_reports(worker.store)
    async with worker.store.pool.connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT * FROM jf_llm_requests WHERE logical_run_id=%s AND operation='run_summary'",
                (run_id,),
            )
        ).fetchall()
    assert len(rows) == 1
    report = rows[0]
    assert report["session_id"] == (await worker.store.get(key))["session_id"]
    assert "must not enter report" not in str(report["payload"])
    assert report["payload"]["max_tokens"] == 512
    from job_ftch.infrastructure.llm.dispatch_worker import DispatchWait

    with pytest.raises(DispatchWait):
        await worker.execute(report["request_id"])
    assert (await worker.store.get(report["request_id"]))["attempts"] == 0
    await worker.close()
    await transport.aclose()


@pytest.mark.asyncio
async def test_cliproxy_scheduled_offset_is_persisted_once_and_route_changes_hold_run(
    dispatch_settings, monkeypatch
):
    from job_ftch.infrastructure.llm.dispatch_session import CliproxyRun

    settings, name = dispatch_settings
    protect(settings, name).run_jitter_seconds = 300
    draws = []

    def draw(a, b):
        draws.append((a, b))
        return 120

    monkeypatch.setattr("job_ftch.infrastructure.llm.dispatch_session.random.uniform", draw)
    due = None
    for _ in range(2):
        with pytest.raises(LLMRequestDeferredError) as scheduled:
            async with CliproxyRun(settings, name, trigger="schedule"):
                pytest.fail("scheduled run must wait before preflight")
        due = due or scheduled.value.retry_at
        assert scheduled.value.retry_at == due
        assert scheduled.value.reason == "scheduled_start"
    assert draws == [(0, 300)]
    settings.llm_provider_profiles[name].account_generation += 1
    with pytest.raises(LLMRequestDeferredError) as changed:
        async with CliproxyRun(settings, name, trigger="schedule"):
            pytest.fail("changed credential generation must not rebind the run")
    assert changed.value.requires_operator and changed.value.reason == "route_changed"


@pytest.mark.asyncio
async def test_cliproxy_cached_preflight_never_hides_new_pause_or_model_cooldown(dispatch_settings):
    from job_ftch.infrastructure.llm.dispatch_transport import QueuedLLMTransport
    from job_ftch.infrastructure.llm.dispatch_worker import LLMWorker

    settings, name = dispatch_settings
    protect(settings, name)
    worker = LLMWorker(
        settings,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"id": "fresh", "usage": {"total_tokens": 7}})
        ),
    )
    key = await worker.store.submit(name, name, "work", payload())
    await worker.execute(key)
    transport = QueuedLLMTransport(settings, name)
    request = httpx.Request(
        "POST",
        "https://ignored.invalid/v1/chat/completions",
        json=payload(),
        headers={"x-job-ftch-operation": "preflight"},
    )
    cached = await transport.handle_async_request(request)
    assert cached.status_code == 200 and cached.json()["usage"]["total_tokens"] == 0
    async with worker.store.pool.connection() as conn:
        await conn.execute("UPDATE jf_llm_scopes SET paused_reason='auth' WHERE scope=%s", (name,))
    paused = await transport.handle_async_request(request)
    assert paused.status_code == 425 and paused.json()["error"]["requires_operator"]
    async with worker.store.pool.connection() as conn:
        await conn.execute("UPDATE jf_llm_scopes SET paused_reason=NULL WHERE scope=%s", (name,))
        await conn.execute(
            "UPDATE jf_llm_model_state SET not_before=now()+interval '2 hours',reason='quota' WHERE scope=%s",
            (name,),
        )
    cooling = await transport.handle_async_request(request)
    assert cooling.status_code == 425 and cooling.json()["error"]["reason"] == "quota"
    async with worker.store.pool.connection() as conn:
        count = await (
            await conn.execute(
                "SELECT count(*) AS n FROM jf_llm_requests WHERE tenant_id=%s", (name,)
            )
        ).fetchone()
    assert count["n"] == 1
    await worker.close()
    await transport.aclose()


@pytest.mark.asyncio
async def test_tenant_runner_wires_durable_run_context_only_around_protected_work(
    dispatch_settings, monkeypatch
):
    from types import SimpleNamespace

    from job_ftch.application.llm_dispatch import llm_run
    from job_ftch.application.pipeline import RunSummary
    from job_ftch.application.tenant_runner import TenantRunner

    settings, name = dispatch_settings
    protect(settings, name)
    runner = object.__new__(TenantRunner)
    monkeypatch.setattr(runner, "get_runtime", lambda _: SimpleNamespace(settings=settings))
    seen = []

    async def ingest(*args, **kwargs):
        seen.append(llm_run.get())
        return RunSummary(
            completion_state="waiting_llm" if len(seen) == 1 else "completed"
        ).finish()

    monkeypatch.setattr(runner, "_run_tenant", ingest)
    for _ in range(3):
        await runner.run_tenant(name, source_ids=["one", "two"])
    assert all(seen)
    assert seen[0] == seen[1] and seen[2] != seen[1]
    assert not llm_run.get()


@pytest.mark.asyncio
async def test_source_continuations_keep_original_session_despite_narrower_selection(
    dispatch_settings,
):
    from job_ftch.infrastructure.llm.dispatch_session import CliproxyRun

    settings, name = dispatch_settings
    protect(settings, name)
    async with CliproxyRun(
        settings, name, selector="all-sources", attempt_run_id="first-attempt"
    ) as first:
        identity = first.run_id
        await first.finish({"completion_state": "waiting_llm"})
    async with CliproxyRun(
        settings, name, selector="all-sources", attempt_run_id="second-attempt"
    ) as resumed:
        assert resumed.run_id == identity
        await resumed.finish({"completion_state": "waiting_rate_limit"})
    async with CliproxyRun(
        settings,
        name,
        selector="one-source",
        attempt_run_id="child-attempt",
        parent_run_id="second-attempt",
    ) as child:
        assert child.run_id == identity
        await child.finish({"completion_state": "completed"})


@pytest.mark.asyncio
async def test_resume_guard_preserves_unknown_outcome_and_account_pause(
    dispatch_settings, monkeypatch
):
    from types import SimpleNamespace

    from job_ftch.infrastructure.llm import dispatch_worker

    settings, name = dispatch_settings
    protect(settings, name)

    async def lost(request):
        raise httpx.ReadTimeout("result lost", request=request)

    worker = dispatch_worker.LLMWorker(settings, transport=httpx.MockTransport(lost))
    key = await worker.store.submit(name, name, "unknown", payload())
    await worker.execute(key)
    monkeypatch.setattr(dispatch_worker, "get_settings", lambda: settings)
    with pytest.raises(ValueError, match="unknown outcomes"):
        await dispatch_worker.admin(SimpleNamespace(command="resume", target=name))
    assert (await worker.store.get(key))["state"] == "uncertain"
    scope = next(s for s in (await worker.store.stats())["scopes"] if s["scope"] == name)
    assert scope["paused_reason"] == "uncertain"
    await worker.close()
