"""Local MCP tenant sources stay aligned with the ai_jobs fixture set."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from job_ftch.application.source_loader import load_sources

_REPO = Path(__file__).resolve().parents[2]
_FIXTURE = _REPO / "fixtures" / "sources" / "ai_jobs.json"
_TENANT = _REPO / "docker" / "local-mcp" / "config" / "tenants" / "local_mcp.yaml"


def _source_name(spec: object) -> str:
    name = getattr(spec, "source_name", None)
    if name:
        return str(name)
    entity = getattr(spec, "entity", None)
    if entity:
        return str(entity)
    url = getattr(spec, "url", None)
    return str(url or spec)


def test_local_mcp_sources_match_ai_jobs_fixture_count_and_names() -> None:
    """local_mcp tenant must ship the canonical 17-source AI jobs set."""
    fixture = load_sources(_FIXTURE)
    tenant = yaml.safe_load(_TENANT.read_text(encoding="utf-8"))
    tenant_sources = tenant["sources"]
    assert len(fixture) == 17
    assert len(tenant_sources) == 17

    fixture_names = {_source_name(spec) for spec in fixture}
    tenant_names = {
        str(row.get("source_name") or row.get("entity") or row.get("url")) for row in tenant_sources
    }
    assert fixture_names == tenant_names


def test_local_mcp_sources_include_telegram_and_career_sites() -> None:
    tenant = yaml.safe_load(_TENANT.read_text(encoding="utf-8"))
    kinds = [row["type"] for row in tenant["sources"]]
    assert kinds.count("telegram_channel") == 4
    assert kinds.count("telegram_group") == 1
    assert kinds.count("career_site") == 12


def test_ai_jobs_fixture_comment_documents_normalization() -> None:
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert "sources" in raw
    assert len(raw["sources"]) == 17
    comment = str(raw.get("_comment") or "")
    assert "17" in comment or "Normalised" in comment or "Normalized" in comment
