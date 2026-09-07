"""FastAPI adapter for the library-first runtime."""
# mypy: disable-error-code="misc,untyped-decorator"

from __future__ import annotations

import base64
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import TypeAdapter

try:
    from fastapi import BackgroundTasks, Request
except ImportError:  # pragma: no cover - optional API extra
    BackgroundTasks = Any  # type: ignore[assignment]
    Request = Any  # type: ignore[assignment]

from job_ftch.application.builder import PipelineBuilder
from job_ftch.application.contracts import SearchBackend
from job_ftch.config import Settings, get_settings
from job_ftch.domain.source_spec import SourceSpec


def create_app(
    builder: PipelineBuilder | None = None,
    *,
    search_backend: SearchBackend | None = None,
    runner: Any | None = None,
    settings: Settings | None = None,
) -> Any:
    try:
        from fastapi import FastAPI, HTTPException
    except ImportError as exc:
        msg = "FastAPI adapter requires the 'api' extra: pip install job-ftch[api]"
        raise ImportError(msg) from exc

    app = FastAPI(title="job_ftch")
    effective_settings = settings or get_settings()
    source_adapter: TypeAdapter[SourceSpec] = TypeAdapter(SourceSpec)

    @app.post("/pipeline/run")
    async def run_pipeline(source_spec: dict[str, Any]) -> dict[str, Any]:
        if builder is None:
            raise HTTPException(status_code=503, detail="Pipeline builder is not configured.")
        spec = source_adapter.validate_python(source_spec)
        summary = await builder.clone().sources([spec]).run_async()
        return summary.as_dict()

    @app.get("/pipeline/status")
    async def pipeline_status() -> dict[str, Any]:
        if builder is None:
            raise HTTPException(status_code=503, detail="Pipeline builder is not configured.")
        store = builder.get_store()
        return {
            "status": await store.get_run_state("pipeline.status"),
            "finished_at": await store.get_run_state("pipeline.finished_at"),
            "emitted": await store.get_run_state("pipeline.emitted"),
        }

    @app.get("/pipeline/sources")
    async def pipeline_sources() -> list[dict[str, Any]]:
        if builder is None:
            raise HTTPException(status_code=503, detail="Pipeline builder is not configured.")
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

    def _token_from_request(request: Any) -> str | None:
        value = str(request.headers.get("authorization") or "")
        scheme, _, token = value.partition(" ")
        if scheme.casefold() != "bearer" or not token.strip():
            return None
        return token.strip()

    def _tenant_allowed(tenant_id: str) -> bool:
        allowlist = {
            item.strip() for item in effective_settings.api_tenant_allowlist if item.strip()
        }
        return not allowlist or tenant_id in allowlist

    def _require_tenant(request: Any, tenant_id: str) -> None:
        expected = effective_settings.api_token
        supplied = _token_from_request(request)
        if (
            expected is None
            or not supplied
            or not hmac.compare_digest(supplied, expected.get_secret_value())
        ):
            raise HTTPException(status_code=401, detail="Invalid bearer token.")
        if not _tenant_allowed(tenant_id):
            raise HTTPException(status_code=403, detail="Tenant is not authorized.")
        if runner is None:
            raise HTTPException(status_code=503, detail="Tenant runner is not configured.")

    def _tenant_runner() -> Any:
        if runner is None:
            raise HTTPException(status_code=503, detail="Tenant runner is not configured.")
        return runner

    def _runtime_for(tenant_id: str) -> Any:
        try:
            return _tenant_runner().get_runtime(tenant_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Tenant not found.") from None

    def _cursor(observed_at: datetime, observation_id: str) -> str:
        raw = json.dumps(
            {"observed_at": observed_at.astimezone(UTC).isoformat(), "id": observation_id},
            separators=(",", ":"),
        ).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def _decode_cursor(value: str | None) -> tuple[datetime, str] | None:
        if not value:
            return None
        try:
            padded = value + "=" * (-len(value) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode())
            timestamp = datetime.fromisoformat(str(payload["observed_at"]))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            return timestamp.astimezone(UTC), str(payload["id"])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise HTTPException(status_code=400, detail="Invalid cursor.") from None

    def _query_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid datetime.") from None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _row_timestamp(value: object) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value))
            except (TypeError, ValueError):
                return datetime(1970, 1, 1, tzinfo=UTC)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _post_accepted(path: str) -> Any:
        try:
            return app.post(path, status_code=202)
        except TypeError:  # lightweight route fakes used by packaging tests
            return app.post(path)

    @app.get("/v1/tenants")
    async def v1_tenants(request: Request) -> dict[str, Any]:
        expected = effective_settings.api_token
        supplied = _token_from_request(request)
        if (
            expected is None
            or not supplied
            or not hmac.compare_digest(supplied, expected.get_secret_value())
        ):
            raise HTTPException(status_code=401, detail="Invalid bearer token.")
        if runner is None:
            raise HTTPException(status_code=503, detail="Tenant runner is not configured.")
        tenants = await _tenant_runner().list_tenants()
        payload = [
            item.model_dump(mode="json") for item in tenants if _tenant_allowed(item.tenant_id)
        ]
        return {"tenants": payload}

    @_post_accepted("/v1/tenants/{tenant_id}/runs")
    async def v1_queue_run(
        request: Request, tenant_id: str, background_tasks: BackgroundTasks
    ) -> dict[str, Any]:
        _require_tenant(request, tenant_id)
        _runtime_for(tenant_id)
        is_active = getattr(_tenant_runner(), "tenant_run_is_active", None)
        if callable(is_active) and is_active(tenant_id):
            raise HTTPException(status_code=409, detail="Tenant run already active.")
        run_id = uuid.uuid4().hex

        async def execute() -> None:
            await _tenant_runner().run_tenant(tenant_id, run_id=run_id, trigger="api")

        background_tasks.add_task(execute)
        return {"tenant_id": tenant_id, "run_id": run_id, "status": "queued"}

    @app.get("/v1/tenants/{tenant_id}/runs")
    async def v1_runs(request: Request, tenant_id: str, limit: int = 20) -> dict[str, Any]:
        _require_tenant(request, tenant_id)
        _runtime_for(tenant_id)
        rows = await _tenant_runner().list_runs(tenant_id=tenant_id, limit=min(max(limit, 1), 100))
        return {"tenant_id": tenant_id, "runs": [row.as_dict() for row in rows]}

    @app.get("/v1/tenants/{tenant_id}/runs/{run_id}")
    async def v1_run(request: Request, tenant_id: str, run_id: str) -> dict[str, Any]:
        _require_tenant(request, tenant_id)
        _runtime_for(tenant_id)
        row = await _tenant_runner().get_run(run_id, tenant_id=tenant_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return cast("dict[str, Any]", row.as_dict())

    @app.get("/v1/tenants/{tenant_id}/runs/{run_id}/stats")
    async def v1_run_stats(request: Request, tenant_id: str, run_id: str) -> dict[str, Any]:
        """Return the persisted run counters and per-source execution stats."""
        _require_tenant(request, tenant_id)
        _runtime_for(tenant_id)
        row = await _tenant_runner().get_run(run_id, tenant_id=tenant_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        payload = row.as_dict()
        metadata_keys = {
            "by_source_kind",
            "by_source_id",
            "tenant_id",
            "applied_profile",
            "started_at",
            "finished_at",
            "scheduled_run_index",
            "source_run_id",
            "source_failures",
            "source_evictions",
            "source_outcomes",
            "graph_hash",
            "graph_node_metrics",
            "skipped_already_active",
            "trigger",
            "config_fingerprint",
        }
        return {
            "tenant_id": tenant_id,
            "run_id": run_id,
            "counters": {key: payload[key] for key in payload.keys() - metadata_keys},
            "by_source_kind": payload.get("by_source_kind", {}),
            "by_source_id": payload.get("by_source_id", {}),
            "source_outcomes": payload.get("source_outcomes", []),
            "source_failures": payload.get("source_failures", []),
            "graph_hash": payload.get("graph_hash"),
            "graph_node_metrics": payload.get("graph_node_metrics", {}),
            "started_at": payload.get("started_at"),
            "finished_at": payload.get("finished_at"),
            "trigger": payload.get("trigger"),
        }

    @app.get("/v1/tenants/{tenant_id}/runs/{run_id}/sources")
    async def v1_run_sources(request: Request, tenant_id: str, run_id: str) -> dict[str, Any]:
        """Return one compact source status row for every source in a run."""
        _require_tenant(request, tenant_id)
        _runtime_for(tenant_id)
        row = await _tenant_runner().get_run(run_id, tenant_id=tenant_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        payload = row.as_dict()
        by_source_id = payload.get("by_source_id", {})
        outcomes = payload.get("source_outcomes", [])
        source_rows_by_id: dict[str, dict[str, Any]] = {}
        if isinstance(by_source_id, dict):
            for source_id, stats in by_source_id.items():
                source_rows_by_id[str(source_id)] = {"source_id": source_id, "stats": stats}
        if isinstance(outcomes, list):
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                source_id = str(
                    outcome.get("source_id")
                    or outcome.get("source_key")
                    or outcome.get("source_name")
                    or ""
                )
                if not source_id:
                    continue
                source_rows_by_id.setdefault(source_id, {"source_id": source_id})["outcome"] = (
                    outcome
                )
        source_rows = sorted(source_rows_by_id.values(), key=lambda item: str(item["source_id"]))
        return {"tenant_id": tenant_id, "run_id": run_id, "sources": source_rows}

    @app.get("/v1/tenants/{tenant_id}/observations")
    async def v1_observations(
        request: Request,
        tenant_id: str,
        limit: int = 100,
        cursor: str | None = None,
        source_id: str | None = None,
        observed_after: str | None = None,
        observed_before: str | None = None,
    ) -> dict[str, Any]:
        _require_tenant(request, tenant_id)
        limit = min(max(limit, 1), 500)
        after_cursor = _decode_cursor(cursor)
        after = after_cursor[0] if after_cursor else None
        before = _query_datetime(observed_before)
        lower = _query_datetime(observed_after) or after
        entries = await _runtime_for(tenant_id).store.list_observations(
            tenant_id=tenant_id, limit=10_000
        )
        selected = []
        for entry in entries:
            if source_id:
                raw_source_id = f"{entry.raw_item.source_kind}:{entry.raw_item.source_name}"
                if source_id not in {raw_source_id, entry.raw_item.source_name}:
                    continue
            stamp = entry.observed_at.astimezone(UTC)
            if lower is not None and stamp < lower:
                continue
            if (
                after_cursor
                and stamp == after_cursor[0]
                and entry.observation_id <= after_cursor[1]
            ):
                continue
            if before is not None and stamp >= before:
                continue
            selected.append(entry)
        page = selected[:limit]
        next_cursor = (
            _cursor(page[-1].observed_at, page[-1].observation_id)
            if len(selected) > limit
            else None
        )
        return {
            "tenant_id": tenant_id,
            "items": [entry.model_dump(mode="json") for entry in page],
            "next_cursor": next_cursor,
        }

    @app.get("/v1/tenants/{tenant_id}/outcomes")
    async def v1_outcomes(
        request: Request,
        tenant_id: str,
        lane: str = "review",
        run_id: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
        outcome: str | None = None,
        reason: str | None = None,
        source_name: str | None = None,
    ) -> dict[str, Any]:
        _require_tenant(request, tenant_id)
        try:
            rows = await _runtime_for(tenant_id).store.list_operational_outcomes(
                lane,
                run_id=run_id,
                limit=0,
                outcome=outcome,
                reason=reason,
                source_name=source_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        rows.sort(
            key=lambda row: (
                _row_timestamp(row.get("recorded_at")),
                str(row.get("outcome_id") or ""),
            )
        )
        after_cursor = _decode_cursor(cursor)
        before_stamp = after_cursor[0] if after_cursor else None
        before_id = after_cursor[1] if after_cursor else ""
        selected = [
            row
            for row in rows
            if before_stamp is None
            or (
                _row_timestamp(row.get("recorded_at")) > before_stamp
                or (
                    _row_timestamp(row.get("recorded_at")) == before_stamp
                    and str(row.get("outcome_id") or "") > before_id
                )
            )
        ]
        page = selected[: min(max(limit, 1), 200)]
        next_cursor = (
            _cursor(
                _row_timestamp(page[-1].get("recorded_at")), str(page[-1].get("outcome_id") or "")
            )
            if len(selected) > len(page)
            else None
        )
        return {"tenant_id": tenant_id, "lane": lane, "items": page, "next_cursor": next_cursor}

    @app.get("/v1/tenants/{tenant_id}/jobs")
    async def v1_jobs(
        request: Request,
        tenant_id: str,
        limit: int = 100,
        cursor: str | None = None,
        observed_after: str | None = None,
        observed_before: str | None = None,
        run_id: str | None = None,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        _require_tenant(request, tenant_id)
        after_cursor = _decode_cursor(cursor)
        since = _query_datetime(observed_after) or (after_cursor[0] if after_cursor else None)
        before = _query_datetime(observed_before)
        list_jobs = getattr(_tenant_runner(), "list_jobs", None)
        if callable(list_jobs):
            jobs = await list_jobs(tenant_id, since=since)
        else:
            _runtime_for(tenant_id)
            jobs = await _tenant_runner().latest_jobs(tenant_id, limit=10_000, since=since)

        def job_key(job: Any) -> tuple[datetime, str]:
            stamp = _row_timestamp(getattr(job, "fetched_at", None))
            return stamp, str(getattr(job, "job_id", "") or getattr(job, "stable_id", ""))

        def job_source_id(job: Any) -> str:
            return f"{getattr(job, 'source_kind', '')}:{getattr(job, 'source_name', '')}"

        jobs = sorted(jobs, key=job_key)
        selected = []
        for job in jobs:
            metadata = getattr(job, "metadata", {}) or {}
            if run_id and str(metadata.get("source_run_id") or "") != run_id:
                continue
            if source_id and source_id not in {
                job_source_id(job),
                str(getattr(job, "source_name", "")),
            }:
                continue
            stamp, item_id = job_key(job)
            if after_cursor and (stamp, item_id) <= after_cursor:
                continue
            if before is not None and stamp >= before:
                continue
            selected.append(job)
        page = selected[: min(max(limit, 1), 500)]
        next_cursor = _cursor(*job_key(page[-1])) if len(selected) > len(page) else None
        return {
            "tenant_id": tenant_id,
            "run_id": run_id,
            "source_id": source_id,
            "items": [job.model_dump(mode="json") for job in page],
            "next_cursor": next_cursor,
        }

    @app.get("/v1/tenants/{tenant_id}/jobs/{job_id}")
    async def v1_job(request: Request, tenant_id: str, job_id: str) -> dict[str, Any]:
        """Return one complete parsed job plus its preserved raw posting body."""
        _require_tenant(request, tenant_id)
        runtime = _runtime_for(tenant_id)
        job = None
        getter = getattr(getattr(runtime, "job_backend", None), "get_job", None)
        if callable(getter):
            job = await getter(job_id)
        if job is None:
            list_jobs = getattr(_tenant_runner(), "list_jobs", None)
            if callable(list_jobs):
                jobs = await list_jobs(tenant_id)
                job = next(
                    (
                        candidate
                        for candidate in jobs
                        if str(getattr(candidate, "job_id", "")) == job_id
                        or str(getattr(candidate, "stable_id", "")) == job_id
                    ),
                    None,
                )
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        parsed = job.model_dump(mode="json")
        metadata = getattr(job, "metadata", {}) or {}
        raw_body = (
            metadata.get("original_posting_text")
            or metadata.get("raw_body")
            or getattr(job, "description_raw", None)
            or getattr(job, "description", None)
        )
        return {
            "tenant_id": tenant_id,
            "job_id": str(getattr(job, "job_id", "") or getattr(job, "stable_id", job_id)),
            "raw_body": raw_body,
            "job": parsed,
        }

    @app.get("/v1/tenants/{tenant_id}/runs/{run_id}/items")
    async def v1_run_items(
        request: Request,
        tenant_id: str,
        run_id: str,
        status: str | None = None,
        source_id: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
        observed_after: str | None = None,
        observed_before: str | None = None,
    ) -> dict[str, Any]:
        """Read accepted, review, and rejected items produced by one run."""
        _require_tenant(request, tenant_id)
        _runtime_for(tenant_id)
        if await _tenant_runner().get_run(run_id, tenant_id=tenant_id) is None:
            raise HTTPException(status_code=404, detail="Run not found.")

        allowed = {item.strip().casefold() for item in (status or "").split(",") if item.strip()}
        valid = {"accepted", "review", "rejected", "failed"}
        invalid = allowed - valid
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: {', '.join(sorted(invalid))}.",
            )
        after_cursor = _decode_cursor(cursor)
        lower = _query_datetime(observed_after) or (after_cursor[0] if after_cursor else None)
        before = _query_datetime(observed_before)
        items: list[dict[str, Any]] = []

        list_jobs = getattr(_tenant_runner(), "list_jobs", None)
        jobs = await list_jobs(tenant_id, since=lower) if callable(list_jobs) else []
        for job in jobs:
            metadata = getattr(job, "metadata", {}) or {}
            if str(metadata.get("source_run_id") or "") != run_id:
                continue
            source_key = f"{getattr(job, 'source_kind', '')}:{getattr(job, 'source_name', '')}"
            if source_id and source_id not in {source_key, str(getattr(job, "source_name", ""))}:
                continue
            stamp = _row_timestamp(getattr(job, "fetched_at", None))
            item_id = str(getattr(job, "job_id", "") or getattr(job, "stable_id", ""))
            items.append(
                {
                    "status": "accepted",
                    "lane": "accepted",
                    "recorded_at": stamp.isoformat(),
                    "source_id": source_key,
                    "item_id": item_id,
                    "item": job.model_dump(mode="json"),
                }
            )

        if not allowed or allowed & {"review", "rejected", "failed"}:
            store = _runtime_for(tenant_id).store
            for lane in ("review", "rejected"):
                if (
                    allowed
                    and lane not in allowed
                    and not (lane == "rejected" and "failed" in allowed)
                ):
                    continue
                rows = await store.list_operational_outcomes(lane, run_id=run_id, limit=0)
                for row in rows:
                    outcome = str(row.get("outcome") or "").casefold()
                    if "failed" in allowed and lane == "rejected" and outcome != "failed":
                        continue
                    if (
                        allowed
                        and lane not in allowed
                        and not (lane == "rejected" and "failed" in allowed)
                    ):
                        continue
                    row_source = str(
                        row.get("source_id")
                        or f"{row.get('source_kind', '')}:{row.get('source_name', '')}"
                    )
                    if source_id and source_id not in {
                        row_source,
                        str(row.get("source_name") or ""),
                    }:
                        continue
                    items.append(
                        {
                            "status": lane,
                            "lane": lane,
                            "recorded_at": _row_timestamp(row.get("recorded_at")).isoformat(),
                            "source_id": row_source,
                            "item_id": str(row.get("outcome_id") or row.get("stable_id") or ""),
                            "item": row,
                        }
                    )

        if allowed and "accepted" not in allowed:
            items = [item for item in items if item["status"] in allowed]
        items.sort(key=lambda item: (_row_timestamp(item["recorded_at"]), item["item_id"]))
        selected = []
        for item in items:
            stamp = _row_timestamp(item["recorded_at"])
            item_id = str(item["item_id"])
            if lower is not None and stamp < lower:
                continue
            if after_cursor and (stamp, item_id) <= after_cursor:
                continue
            if before is not None and stamp >= before:
                continue
            selected.append(item)
        page = selected[: min(max(limit, 1), 500)]
        next_cursor = (
            _cursor(_row_timestamp(page[-1]["recorded_at"]), str(page[-1]["item_id"]))
            if len(selected) > len(page)
            else None
        )
        return {
            "tenant_id": tenant_id,
            "run_id": run_id,
            "status": sorted(allowed) if allowed else ["accepted", "review", "rejected"],
            "items": page,
            "next_cursor": next_cursor,
        }

    @_post_accepted("/v1/tenants/{tenant_id}/replays")
    async def v1_replay(
        request: Request, tenant_id: str, background_tasks: BackgroundTasks
    ) -> dict[str, Any]:
        _require_tenant(request, tenant_id)
        _runtime_for(tenant_id)
        replay = getattr(_tenant_runner(), "replay_tenant", None)
        if not callable(replay):
            raise HTTPException(status_code=501, detail="Replay is not configured.")
        run_id = uuid.uuid4().hex
        background_tasks.add_task(replay, tenant_id, run_id=run_id)
        return {"tenant_id": tenant_id, "run_id": run_id, "status": "queued"}

    return app
