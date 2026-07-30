from __future__ import annotations

from job_ftch.infrastructure.observability.otel_setup import _resolve_langfuse_host


def test_resolve_langfuse_host_keeps_remote_url(monkeypatch) -> None:
    monkeypatch.setattr(
        "job_ftch.infrastructure.observability.otel_setup._running_in_container",
        lambda: False,
    )

    assert (
        _resolve_langfuse_host("http://host.docker.internal:3001")
        == "http://host.docker.internal:3001"
    )


def test_resolve_langfuse_host_rewrites_localhost_inside_container(monkeypatch) -> None:
    monkeypatch.setattr(
        "job_ftch.infrastructure.observability.otel_setup._running_in_container",
        lambda: True,
    )

    assert _resolve_langfuse_host("http://localhost:3001") == "http://host.docker.internal:3001"
