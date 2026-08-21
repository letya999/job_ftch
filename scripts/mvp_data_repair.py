"""Repeatable MVP data-repair operations.

All commands are read-only by default.  Database/Qdrant mutations require
``--apply`` and write a machine-readable report without shot text to stdout.
Local backups intentionally remain in ``artifacts/`` and are gitignored.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from job_ftch.application.ontology_compiler import (
    OntologyCandidateChunk,
    OntologyCompilationResult,
    _restore_projection_from_candidates,
    compile_ontology_from_shots,
    make_labeled_ontology_shots,
    materialize_compiled_ontology,
    sanitize_compiled_ontology,
)
from job_ftch.application.ontology_enrichment import ontology_compiler_runtime_settings
from job_ftch.application.ontology_graph_builder import build_ontology_graph_from_compiled
from job_ftch.application.registry import create_llm, create_ontology_store, create_store
from job_ftch.application.shot_sync import sync_profile_to_shot_store
from job_ftch.application.tenant_store import TenantStore
from job_ftch.config import get_settings
from job_ftch.domain import CompiledOntology

ONTOLOGY_TABLES = (
    "jf_ontology_compiled_relation",
    "jf_ontology_compiled_term",
    "jf_ontology_term_stat",
    "jf_ontology_evidence",
    "jf_ontology_edge",
    "jf_ontology_node",
    "jf_ontology_graph_version",
    "jf_ontology_skill",
    "jf_ontology_role",
    "jf_ontology_seniority",
    "jf_ontology_anti",
    "jf_ontology_positive_keyword",
    "jf_ontology_negative_keyword",
    "jf_ontology_occurrence",
)
CATALOG_TABLES = (
    "jf_jobs",
    "jf_job_groups",
    "jf_job_group_urls",
    "jf_job_group_fingerprints",
    "jf_dedup_claims",
    "jf_outbox",
    "jf_observations",
    "jf_source_snapshots",
    "jf_source_ingest_state",
)
CATALOG_CLEAR_SQL = (
    "DELETE FROM jf_jobs",
    "DELETE FROM jf_job_group_urls",
    "DELETE FROM jf_job_group_fingerprints",
    "DELETE FROM jf_job_groups",
    "DELETE FROM jf_dedup_claims",
)
TENANT_SOURCE_CLEAR_SQL = (
    "DELETE FROM jf_observations WHERE tenant_id = $1",
    "DELETE FROM jf_source_snapshots WHERE tenant_id = $1",
    "DELETE FROM jf_source_ingest_state WHERE tenant_id = $1",
)
# Table names come exclusively from the module-level immutable allowlist.
ONTOLOGY_CLEAR_SQL = tuple("DELETE FROM " + table for table in ONTOLOGY_TABLES)  # nosec B608
LIVE_SHOTS = "profile_shots_bgem3_mvp_v3"
LIVE_JOBS = "job_ftch_jobs_mvp_v1"


def _artifact(name: str) -> Path:
    path = Path("artifacts/mvp_data_repair")
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{name}.json"


def _write_report(name: str, payload: dict[str, Any]) -> Path:
    path = _artifact(name)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


async def _profile() -> tuple[Any, Any, str]:
    settings = get_settings()
    store: Any = create_store(settings)
    tenant = TenantStore("ai_jobs", store)
    users = await tenant.list_candidate_profile_users()
    if len(users) != 1:
        raise RuntimeError(f"expected one profile user, found {len(users)}")
    user_id = users[0]
    active = await tenant.get_active_candidate_profile_id(user_id)
    if active is None:
        raise RuntimeError("active candidate profile marker is missing")
    profile = await tenant.get_candidate_profile(user_id, active)
    if profile is None:
        raise RuntimeError("active candidate profile record is missing")
    return tenant, profile, user_id


async def audit_profile_data(*, backup: bool) -> dict[str, Any]:
    settings = get_settings()
    store: Any = create_store(settings)
    await store._ensure_initialized()  # operational script; use the active PostgreSQL connector
    pool = store._pool
    table_counts: dict[str, int] = {}
    async with pool.acquire() as conn:
        for table in (*ONTOLOGY_TABLES, *CATALOG_TABLES):
            try:
                table_counts[table] = int(  # nosec B608 -- allowlisted table name
                    await conn.fetchval(f"SELECT COUNT(*) FROM {table}")  # nosec B608
                )
            except Exception:
                table_counts[table] = -1
    tenant, profile, user_id = await _profile()
    del tenant
    search = profile.profile.search_profiles[0]
    categories = {
        "resume:positive": len(search.positive_example_texts),
        "resume:negative": len(search.negative_example_texts),
        "vacancy:positive": len(search.positive_job_example_texts),
        "vacancy:negative": len(search.negative_job_example_texts),
    }
    result = {
        "tenant_id": "ai_jobs",
        "user_id": user_id,
        "profile_id": profile.profile_id,
        "profile_hash": hashlib.sha256(profile.model_dump_json().encode()).hexdigest(),
        "profile_shot_counts": categories,
        "postgres": table_counts,
        "backup_requested": backup,
    }
    if backup:
        async with pool.acquire() as conn:
            exported = {
                table: [
                    dict(row)
                    for row in await conn.fetch(  # nosec B608 -- allowlisted table name
                        f"SELECT * FROM {table}"  # nosec B608
                    )
                ]
                for table in (*ONTOLOGY_TABLES, *CATALOG_TABLES, "jf_kv", "jf_set")
            }
        backup_path = _write_report("postgres_backup", {"tables": exported})
        result["postgres_backup"] = str(backup_path)
        from qdrant_client import QdrantClient

        client = QdrantClient(
            url=settings.qdrant_url,
            api_key=(
                settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
            ),
        )
        qdrant_backup: dict[str, Any] = {}
        for collection in ("profile_shots_bgem3", "job_ftch_jobs"):
            if not client.collection_exists(collection):
                qdrant_backup[collection] = {"exists": False, "points": []}
                continue
            points, _ = client.scroll(
                collection_name=collection, with_payload=True, with_vectors=True, limit=100_000
            )
            qdrant_backup[collection] = {
                "exists": True,
                "points": [point.model_dump(mode="json") for point in points],
            }
        qdrant_path = _write_report("qdrant_backup", {"collections": qdrant_backup})
        result["qdrant_backup"] = str(qdrant_path)
    await store.close()
    return result


async def rebuild_profile_shots(*, apply: bool) -> dict[str, Any]:
    tenant, profile, user_id = await _profile()
    search = profile.profile.search_profiles[0]
    expected = {
        "resume:positive": len(search.positive_example_texts),
        "resume:negative": len(search.negative_example_texts),
        "vacancy:positive": len(search.positive_job_example_texts),
        "vacancy:negative": len(search.negative_job_example_texts),
    }
    report: dict[str, Any] = {"collection": LIVE_SHOTS, "expected": expected, "apply": apply}
    if apply:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as rest

        settings = get_settings()
        client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None,
        )
        if not client.collection_exists(LIVE_SHOTS):
            client.create_collection(
                collection_name=LIVE_SHOTS,
                vectors_config=rest.VectorParams(size=1024, distance=rest.Distance.COSINE),
            )
        await sync_profile_to_shot_store(profile=profile, tenant_id="ai_jobs", user_id=user_id)
        points, _ = client.scroll(
            collection_name=LIVE_SHOTS, with_payload=True, with_vectors=False, limit=1000
        )
        actual: dict[str, int] = {}
        for point in points:
            category = str((point.payload or {}).get("category"))
            actual[category] = actual.get(category, 0) + 1
        if actual != expected:
            raise RuntimeError(f"shot rebuild verification failed: {actual!r}")
        report["actual"] = actual
    await tenant.close()
    return report


async def reset_job_catalog(*, apply: bool) -> dict[str, Any]:
    settings = get_settings()
    store: Any = create_store(settings)
    await store._ensure_initialized()
    report: dict[str, Any] = {
        "tenant_id": "ai_jobs",
        "apply": apply,
        "tables": list(CATALOG_TABLES),
    }
    if apply:
        async with store._pool.acquire() as conn, conn.transaction():
            # The catalog and dedup schema is global in this single-tenant MVP;
            # source state remains tenant-scoped.  The preflight backup and
            # single-tenant audit are mandatory before this branch is used.
            for statement in CATALOG_CLEAR_SQL:
                await conn.execute(statement)
            # jf_outbox is global in the single-tenant MVP schema.  The
            # remaining tables carry tenant_id and can be reset selectively.
            await conn.execute("DELETE FROM jf_outbox")
            for statement in TENANT_SOURCE_CLEAR_SQL:
                await conn.execute(statement, "ai_jobs")
            await conn.execute(
                "DELETE FROM jf_kv WHERE key LIKE ANY($1::text[])",
                ["ai_jobs:dedup_record:%", "ai_jobs:processed_at:%", "ai_jobs:snapshot:%"],
            )
            await conn.execute(
                "DELETE FROM jf_set WHERE key LIKE ANY($1::text[])",
                ["ai_jobs:dedup_keys%", "ai_jobs:processed"],
            )
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as rest

        client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None,
        )
        if client.collection_exists(LIVE_JOBS):
            client.delete_collection(LIVE_JOBS)
        client.create_collection(
            collection_name=LIVE_JOBS,
            vectors_config=rest.VectorParams(size=1024, distance=rest.Distance.COSINE),
        )
    await store.close()
    return report


async def rebuild_ontology(*, apply: bool, staging_artifact: Path | None = None) -> dict[str, Any]:
    """Extract all active-profile shots, then atomically replace live ontology."""
    tenant, profile, _user_id = await _profile()
    base_settings = get_settings()
    settings = ontology_compiler_runtime_settings(base_settings)
    search = profile.profile.search_profiles[0]
    shots = (
        *(("positive_resume", text) for text in search.positive_example_texts),
        *(("negative_resume", text) for text in search.negative_example_texts),
        *(("positive_job", text) for text in search.positive_job_example_texts),
        *(("negative_job", text) for text in search.negative_job_example_texts),
    )
    if staging_artifact is not None:
        manifest = json.loads(staging_artifact.read_text(encoding="utf-8"))
        ontology = sanitize_compiled_ontology(
            CompiledOntology.model_validate(manifest["compiled_ontology"])
        )
        candidate_chunks = tuple(
            OntologyCandidateChunk.model_validate(chunk)
            for chunk in manifest.get("candidate_chunks", ())
        )
        if candidate_chunks:
            ontology = ontology.model_copy(update={"terms": ()})
            ontology = sanitize_compiled_ontology(
                _restore_projection_from_candidates(
                    ontology,
                    candidate_chunks,
                    make_labeled_ontology_shots(shots),
                )
            )
        materialized, term_stats = materialize_compiled_ontology(ontology)
        result = OntologyCompilationResult(
            ontology=ontology,
            materialized=materialized,
            term_stats=term_stats,
            prompt_hash=str(item) if (item := manifest.get("prompt_hash")) else "staging",
            model=str(manifest.get("model") or "staging"),
            candidate_chunks=candidate_chunks,
        )
        artifact = staging_artifact
    else:
        llm = create_llm(settings)
        result = await compile_ontology_from_shots(
            shots=shots,
            llm=llm,
            prompt_path=settings.ontology_compiler_prompt_path,
        )
        manifest = {
            "profile_hash": hashlib.sha256(profile.model_dump_json().encode()).hexdigest(),
            "model": result.model,
            "prompt_hash": result.prompt_hash,
            "compiler_mode": settings.ontology_compiler_mode,
            "compiled_ontology": result.ontology.model_dump(mode="json"),
            "candidate_chunks": [
                chunk.model_dump(mode="json") for chunk in result.candidate_chunks
            ],
            "shots": [
                {
                    "kind": kind,
                    "shot_hash": hashlib.sha256(text.encode()).hexdigest(),
                }
                for kind, text in shots
            ],
        }
        artifact = _write_report("ontology_extraction_staging", manifest)
    report: dict[str, Any] = {
        "apply": apply,
        "staging_artifact": str(artifact),
        "shots": len(shots),
        "accepted_terms": sum(1 for term in result.ontology.terms if term.accepted),
    }
    if apply:
        store: Any = create_store(settings)
        await store._ensure_initialized()
        model = result.model
        async with store._pool.acquire() as conn, conn.transaction():
            for statement in ONTOLOGY_CLEAR_SQL:
                await conn.execute(statement)
        await store.close()

        ontology_store: Any = create_ontology_store(settings)
        try:
            corpus_source_id = hashlib.sha256(
                "".join(hashlib.sha256(text.encode()).hexdigest() for _, text in shots).encode()
            ).hexdigest()[:24]
            corpus_prompt_hash = "compiled-ontology:" + result.prompt_hash[:24]
            compiled_writer = getattr(ontology_store, "upsert_compiled_ontology", None)
            if callable(compiled_writer):
                await compiled_writer(result.ontology)
            graph_writer = getattr(ontology_store, "upsert_shot_graph", None)
            if callable(graph_writer):
                graph = build_ontology_graph_from_compiled(
                    ontology=result.ontology,
                    graph_id="compiled:" + corpus_source_id,
                    shot_id=corpus_source_id,
                    model=model,
                    prompt_hash=result.prompt_hash,
                    materialized=result.materialized,
                )
                await graph_writer(graph)
            stat_writer = getattr(ontology_store, "upsert_term_stats", None)
            if callable(stat_writer):
                await stat_writer(result.term_stats)
            lang = "mixed"
            for skill in result.materialized.positive_skills:
                await ontology_store.upsert_skill(
                    skill,
                    alias=skill,
                    lang=lang,
                    source_shot_id=corpus_source_id,
                    source_type="corpus",
                    polarity="positive",
                    model=model,
                    prompt_hash=corpus_prompt_hash,
                )
            for skill in result.materialized.negative_skills:
                await ontology_store.upsert_skill(
                    skill,
                    alias=skill,
                    lang=lang,
                    source_shot_id=corpus_source_id,
                    source_type="corpus",
                    polarity="negative",
                    model=model,
                    prompt_hash=corpus_prompt_hash,
                )
            for role in result.materialized.positive_roles:
                await ontology_store.upsert_role(
                    role,
                    alias=role,
                    lang=lang,
                    source_shot_id=corpus_source_id,
                    source_type="corpus",
                    polarity="positive",
                    model=model,
                    prompt_hash=corpus_prompt_hash,
                )
            for role in result.materialized.negative_roles:
                await ontology_store.upsert_role(
                    role,
                    alias=role,
                    lang=lang,
                    source_shot_id=corpus_source_id,
                    source_type="corpus",
                    polarity="negative",
                    model=model,
                    prompt_hash=corpus_prompt_hash,
                )
            for term in result.materialized.anti_patterns:
                await ontology_store.upsert_anti_pattern(term)
            for level in result.materialized.seniority:
                await ontology_store.upsert_seniority(level)
            for term, weight in result.materialized.positive_keywords:
                await ontology_store.upsert_positive_keyword(term, weight=weight)
            for term, weight in result.materialized.negative_keywords:
                await ontology_store.upsert_negative_keyword(term, weight=weight)
            report["materialized"] = result.materialized.model_dump(mode="json")
            report["term_stats"] = len(result.term_stats)
        finally:
            close = getattr(ontology_store, "close", None)
            if callable(close):
                await close()

        verify_store: Any = create_store(settings)
        await verify_store._ensure_initialized()
        async with verify_store._pool.acquire() as conn:
            counts = {
                table: int(  # nosec B608 -- allowlisted table name
                    await conn.fetchval(f"SELECT COUNT(*) FROM {table}")  # nosec B608
                )
                for table in ONTOLOGY_TABLES
            }
            has_negative_projection = (
                counts["jf_ontology_negative_keyword"] or counts["jf_ontology_anti"]
            )
            if not has_negative_projection:
                raise RuntimeError(f"ontology rebuild lacks negative evidence: {counts}")
            if not counts["jf_ontology_occurrence"]:
                raise RuntimeError(f"ontology rebuild lacks occurrence evidence: {counts}")
            report["counts"] = counts
        await verify_store.close()
    await tenant.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "audit-profile-data",
            "rebuild-profile-shots",
            "rebuild-ontology",
            "reset-job-catalog",
        ),
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", action="store_true")
    parser.add_argument("--staging-artifact", type=Path)
    args = parser.parse_args()
    if args.command == "audit-profile-data":
        result = asyncio.run(audit_profile_data(backup=args.backup))
    elif args.command == "rebuild-profile-shots":
        result = asyncio.run(rebuild_profile_shots(apply=args.apply))
    elif args.command == "reset-job-catalog":
        result = asyncio.run(reset_job_catalog(apply=args.apply))
    else:
        result = asyncio.run(
            rebuild_ontology(apply=args.apply, staging_artifact=args.staging_artifact)
        )
    report = _write_report(args.command, result)
    print(json.dumps({**result, "report": str(report)}, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
