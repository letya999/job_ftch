"""decision_version is a single source of truth in Pipeline, not scattered getattr."""

from __future__ import annotations

from unittest.mock import AsyncMock

from job_ftch.application.pipeline import Pipeline
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.nodes.sanitize import SanitizeNode


def _make_pipeline(
    *,
    decision_version: str = "test-v42",
    tenant_id: str = "test-tenant",
) -> Pipeline:
    store = InMemoryStore()
    source = AsyncMock()
    source.__aiter__ = AsyncMock(return_value=iter([]))
    sink = AsyncMock()

    class _PassNode:
        async def process(self, item: object) -> object:
            return item

    return Pipeline(
        source=source,
        sanitize_node=SanitizeNode(),
        nodes=[_PassNode()],
        sink=sink,
        store=store,
        decision_version=decision_version,
        tenant_id=tenant_id,
    )


def test_pipeline_stores_decision_version() -> None:
    pipeline = _make_pipeline(decision_version="policy-v2")
    assert pipeline._decision_version == "policy-v2"


def test_pipeline_stores_tenant_id() -> None:
    pipeline = _make_pipeline(tenant_id="acme-corp")
    assert pipeline._tenant_id == "acme-corp"


def test_pipeline_defaults() -> None:
    store = InMemoryStore()
    source = AsyncMock()
    sink = AsyncMock()

    class _PassNode:
        async def process(self, item: object) -> object:
            return item

    pipeline = Pipeline(
        source=source,
        sanitize_node=SanitizeNode(),
        nodes=[_PassNode()],
        sink=sink,
        store=store,
    )
    assert pipeline._decision_version == "pipeline-v1"
    assert pipeline._tenant_id == "default"
