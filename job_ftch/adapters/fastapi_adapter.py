"""FastAPI adapter for the library-first runtime."""
# mypy: disable-error-code="misc,untyped-decorator"

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from job_ftch.application.builder import PipelineBuilder
from job_ftch.application.contracts import SearchBackend
from job_ftch.domain.source_spec import SourceSpec


def create_app(
    builder: PipelineBuilder,
    *,
    search_backend: SearchBackend | None = None,
) -> Any:
    try:
        from fastapi import FastAPI, HTTPException
    except ImportError as exc:
        msg = "FastAPI adapter requires the 'api' extra: pip install job-ftch[api]"
        raise ImportError(msg) from exc

    app = FastAPI(title="job_ftch")
    source_adapter: TypeAdapter[SourceSpec] = TypeAdapter(SourceSpec)

    @app.post("/pipeline/run")
    async def run_pipeline(source_spec: dict[str, Any]) -> dict[str, Any]:
        spec = source_adapter.validate_python(source_spec)
        summary = await builder.clone().sources([spec]).run_async()
        return summary.as_dict()

    @app.get("/pipeline/status")
    async def pipeline_status() -> dict[str, Any]:
        store = builder.get_store()
        return {
            "status": await store.get_run_state("pipeline.status"),
            "finished_at": await store.get_run_state("pipeline.finished_at"),
            "emitted": await store.get_run_state("pipeline.emitted"),
        }

    @app.get("/pipeline/sources")
    async def pipeline_sources() -> list[dict[str, Any]]:
        store = builder.get_store()
        list_source_health = getattr(store, "list_source_health", None)
        if callable(list_source_health):
            return list(await list_source_health())
        return []

    @app.get("/jobs/search")
    async def search_jobs(q: str, limit: int = 20) -> list[dict[str, Any]]:
        if search_backend is None:
            raise HTTPException(status_code=503, detail="Search backend is not configured.")
        groups = await search_backend.search(q, limit=limit)
        return [group.model_dump(mode="json") for group in groups]

    return app
