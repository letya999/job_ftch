"""Dagster adapter for wrapping source specs as assets."""
# mypy: disable-error-code="misc,untyped-decorator"

from __future__ import annotations

import asyncio
from typing import Any

from job_ftch.application.builder import PipelineBuilder
from job_ftch.domain.source_spec import SourceSpec


def _asset_name(spec: SourceSpec, index: int) -> str:
    return f"{spec.type}_{index}"


def create_definitions(builder: PipelineBuilder, specs: list[SourceSpec]) -> Any:
    try:
        from dagster import Definitions, MaterializeResult, asset
    except ImportError as exc:
        msg = "Dagster adapter requires dagster: pip install job-ftch[dagster]"
        raise ImportError(msg) from exc

    assets = []
    for index, spec in enumerate(specs):
        asset_name = _asset_name(spec, index)

        @asset(name=asset_name)
        def _run_asset(spec: SourceSpec = spec) -> Any:
            summary = asyncio.run(builder.clone().sources([spec]).run_async())
            return MaterializeResult(metadata=summary.as_dict())  # type: ignore

        assets.append(_run_asset)
    return Definitions(assets=assets)
