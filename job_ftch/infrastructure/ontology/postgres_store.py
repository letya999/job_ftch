"""PostgreSQL-backed OntologyStore (per ADR-020).

The live ontology (skills, roles, seniority, anti-patterns) is updated
on-the-fly when the user adds a shot and the LLM extracts structured
data. The Postgres backend shares the same connection pool as the
``PostgreSQLStore`` so the dedup store and the ontology live in the
same database.

Why a separate class (not reuse ``DBOntologyStore``): the SQLite
version expects a file path and uses ``aiosqlite``, whereas this
backend needs a DSN and uses ``asyncpg``. The SQL is portable
(PostgreSQL ``ON CONFLICT`` vs SQLite ``INSERT OR IGNORE``) but the
driver API is not — splitting into two classes keeps each backend
honest about its dependencies.

Tables (``003_ontology_pg.sql``):
- jf_ontology_skill (canonical, alias, lang)
- jf_ontology_role (canonical, alias, lang)
- jf_ontology_seniority (level)
- jf_ontology_anti (pattern)
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, cast

try:
    import asyncpg

    _IMPORT_ERROR = None
except ImportError as exc:
    asyncpg = None
    _IMPORT_ERROR = exc

from job_ftch.application.registry import register_ontology_store

if TYPE_CHECKING:
    from job_ftch.config import Settings
    from job_ftch.domain import CompiledOntology, OntologyTermStat, ShotOntologyGraph


class PostgresOntologyStore:
    """PostgreSQL-backed live ontology (per ADR-020)."""

    _SQL_UPSERT_SKILL = (
        "INSERT INTO jf_ontology_skill (canonical, alias, lang, polarity, source_shot_id, source_type, model, prompt_hash) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
        "ON CONFLICT (canonical, alias, lang) DO UPDATE SET "
        "polarity = EXCLUDED.polarity, source_shot_id = EXCLUDED.source_shot_id, source_type = EXCLUDED.source_type, model = EXCLUDED.model, prompt_hash = EXCLUDED.prompt_hash"
    )
    _SQL_LIST_SKILLS = (
        "SELECT DISTINCT canonical FROM jf_ontology_occurrence "
        "WHERE entity_type = 'skill' AND polarity = 'positive'"
    )
    _SQL_LIST_SKILLS_LANG = (
        "SELECT DISTINCT canonical FROM jf_ontology_occurrence "
        "WHERE entity_type = 'skill' AND lang = $1 AND polarity = 'positive'"
    )
    _SQL_LIST_NEGATIVE_SKILLS = (
        "SELECT DISTINCT canonical FROM jf_ontology_occurrence "
        "WHERE entity_type = 'skill' AND polarity = 'negative'"
    )
    _SQL_LIST_NEGATIVE_SKILLS_LANG = (
        "SELECT DISTINCT canonical FROM jf_ontology_occurrence "
        "WHERE entity_type = 'skill' AND lang = $1 AND polarity = 'negative'"
    )
    _SQL_LOOKUP_SKILL = (
        "SELECT canonical FROM jf_ontology_skill "
        "WHERE LOWER(alias) = LOWER($1) OR LOWER(canonical) = LOWER($1) "
        "LIMIT 1"
    )
    _SQL_INSERT_OCCURRENCE = (
        "INSERT INTO jf_ontology_occurrence "
        "(entity_type, canonical, alias, lang, polarity, source_shot_id, source_type, model, prompt_hash) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) ON CONFLICT DO NOTHING"
    )
    _SQL_DELETE_SKILL_OPPOSITE = (
        "DELETE FROM jf_ontology_skill WHERE canonical = $1 AND lang = $2 AND polarity <> $3"
    )
    _SQL_DELETE_ROLE_OPPOSITE = (
        "DELETE FROM jf_ontology_role WHERE canonical = $1 AND lang = $2 AND polarity <> $3"
    )
    _SQL_DELETE_OCCURRENCE_OPPOSITE = (
        "DELETE FROM jf_ontology_occurrence "
        "WHERE entity_type = $1 AND canonical = $2 AND lang = $3 AND polarity <> $4"
    )

    _SQL_UPSERT_ROLE = (
        "INSERT INTO jf_ontology_role (canonical, alias, lang, polarity, source_shot_id, source_type, model, prompt_hash) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
        "ON CONFLICT (canonical, alias, lang) DO UPDATE SET "
        "polarity = EXCLUDED.polarity, source_shot_id = EXCLUDED.source_shot_id, source_type = EXCLUDED.source_type, model = EXCLUDED.model, prompt_hash = EXCLUDED.prompt_hash"
    )
    _SQL_LIST_ROLES = (
        "SELECT DISTINCT canonical FROM jf_ontology_occurrence "
        "WHERE entity_type = 'role' AND polarity = 'positive'"
    )
    _SQL_LIST_ROLES_LANG = (
        "SELECT DISTINCT canonical FROM jf_ontology_occurrence "
        "WHERE entity_type = 'role' AND lang = $1 AND polarity = 'positive'"
    )
    _SQL_LIST_NEGATIVE_ROLES = (
        "SELECT DISTINCT canonical FROM jf_ontology_occurrence "
        "WHERE entity_type = 'role' AND polarity = 'negative'"
    )
    _SQL_LIST_NEGATIVE_ROLES_LANG = (
        "SELECT DISTINCT canonical FROM jf_ontology_occurrence "
        "WHERE entity_type = 'role' AND lang = $1 AND polarity = 'negative'"
    )

    _SQL_UPSERT_SENIORITY = (
        "INSERT INTO jf_ontology_seniority (level) VALUES ($1) ON CONFLICT (level) DO NOTHING"
    )
    _SQL_LIST_SENIORITY = "SELECT level FROM jf_ontology_seniority"

    _SQL_UPSERT_ANTI = (
        "INSERT INTO jf_ontology_anti (pattern) VALUES ($1) ON CONFLICT (pattern) DO NOTHING"
    )
    _SQL_LIST_ANTI = "SELECT pattern FROM jf_ontology_anti"
    _SQL_UPSERT_POSITIVE_KEYWORD = (
        "INSERT INTO jf_ontology_positive_keyword (term, weight) VALUES ($1, $2) "
        "ON CONFLICT (term) DO UPDATE SET weight = EXCLUDED.weight"
    )
    _SQL_LIST_POSITIVE_KEYWORDS = (
        "SELECT term, weight FROM jf_ontology_positive_keyword ORDER BY weight DESC, term ASC"
    )
    _SQL_UPSERT_NEGATIVE_KEYWORD = (
        "INSERT INTO jf_ontology_negative_keyword (term, weight) VALUES ($1, $2) "
        "ON CONFLICT (term) DO UPDATE SET weight = EXCLUDED.weight"
    )
    _SQL_LIST_NEGATIVE_KEYWORDS = (
        "SELECT term, weight FROM jf_ontology_negative_keyword ORDER BY weight DESC, term ASC"
    )
    _SQL_UPSERT_TERM_STAT = (
        "INSERT INTO jf_ontology_term_stat "
        "(entity_type, canonical, polarity, aliases_json, positive_count, negative_count, contextual_count, "
        "positive_weight, negative_weight, contextual_weight, recency_weight, section_weight, keyness, score, "
        "antonyms_json, related_terms_json) "
        "VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15::jsonb, $16::jsonb) "
        "ON CONFLICT (entity_type, canonical) DO UPDATE SET "
        "polarity = EXCLUDED.polarity, aliases_json = EXCLUDED.aliases_json, "
        "positive_count = EXCLUDED.positive_count, negative_count = EXCLUDED.negative_count, "
        "contextual_count = EXCLUDED.contextual_count, positive_weight = EXCLUDED.positive_weight, "
        "negative_weight = EXCLUDED.negative_weight, contextual_weight = EXCLUDED.contextual_weight, "
        "recency_weight = EXCLUDED.recency_weight, section_weight = EXCLUDED.section_weight, "
        "keyness = EXCLUDED.keyness, score = EXCLUDED.score, antonyms_json = EXCLUDED.antonyms_json, "
        "related_terms_json = EXCLUDED.related_terms_json, updated_at = now()"
    )

    def __init__(
        self,
        dsn: str,
        pool_min: int = 2,
        pool_max: int = 10,
    ) -> None:
        self._dsn = dsn
        self._pool_min = pool_min
        self._pool_max = pool_max
        self._pool: Any = None
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self) -> Any:
        if asyncpg is None:
            raise ImportError(
                "PostgreSQL backend requires the 'postgres' extra: pip install job-ftch[postgres]"
            ) from _IMPORT_ERROR

        async with self._init_lock:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(
                    self._dsn,
                    min_size=self._pool_min,
                    max_size=self._pool_max,
                )
        return self._pool

    async def _exec(self, sql: str, params: tuple[object, ...] = ()) -> None:
        pool = await self._ensure_initialized()
        async with pool.acquire() as conn:
            await conn.execute(sql, *params)

    async def _fetchall(
        self, sql: str, params: tuple[object, ...] = ()
    ) -> list[tuple[object, ...]]:
        pool = await self._ensure_initialized()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [tuple(row) for row in rows]

    async def _fetchone(
        self, sql: str, params: tuple[object, ...] = ()
    ) -> tuple[object, ...] | None:
        pool = await self._ensure_initialized()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
            return tuple(row) if row else None

    async def upsert_skill(
        self,
        canonical: str,
        *,
        alias: str | None = None,
        lang: str = "en",
        source_shot_id: str | None = None,
        source_type: str | None = None,
        polarity: str = "positive",
        model: str | None = None,
        prompt_hash: str | None = None,
    ) -> None:
        await self._exec(self._SQL_DELETE_SKILL_OPPOSITE, (canonical, lang, polarity))
        await self._exec(self._SQL_DELETE_OCCURRENCE_OPPOSITE, ("skill", canonical, lang, polarity))
        await self._exec(
            self._SQL_UPSERT_SKILL,
            (
                canonical,
                alias or canonical,
                lang,
                polarity,
                source_shot_id,
                source_type,
                model,
                prompt_hash,
            ),
        )
        await self._record_occurrence(
            "skill",
            canonical,
            alias or canonical,
            lang,
            polarity,
            source_shot_id,
            source_type,
            model,
            prompt_hash,
        )

    async def list_skills(self, lang: str | None = None) -> tuple[str, ...]:
        if lang is None:
            rows = await self._fetchall(self._SQL_LIST_SKILLS)
        else:
            rows = await self._fetchall(self._SQL_LIST_SKILLS_LANG, (lang,))
        return tuple(str(r[0]) for r in rows)

    async def list_negative_skills(self, lang: str | None = None) -> tuple[str, ...]:
        if lang is None:
            rows = await self._fetchall(self._SQL_LIST_NEGATIVE_SKILLS)
        else:
            rows = await self._fetchall(self._SQL_LIST_NEGATIVE_SKILLS_LANG, (lang,))
        return tuple(str(r[0]) for r in rows)

    async def upsert_role(
        self,
        canonical: str,
        *,
        alias: str | None = None,
        lang: str = "en",
        source_shot_id: str | None = None,
        source_type: str | None = None,
        polarity: str = "positive",
        model: str | None = None,
        prompt_hash: str | None = None,
    ) -> None:
        await self._exec(self._SQL_DELETE_ROLE_OPPOSITE, (canonical, lang, polarity))
        await self._exec(self._SQL_DELETE_OCCURRENCE_OPPOSITE, ("role", canonical, lang, polarity))
        await self._exec(
            self._SQL_UPSERT_ROLE,
            (
                canonical,
                alias or canonical,
                lang,
                polarity,
                source_shot_id,
                source_type,
                model,
                prompt_hash,
            ),
        )
        await self._record_occurrence(
            "role",
            canonical,
            alias or canonical,
            lang,
            polarity,
            source_shot_id,
            source_type,
            model,
            prompt_hash,
        )

    async def _record_occurrence(
        self,
        entity_type: str,
        canonical: str,
        alias: str,
        lang: str,
        polarity: str,
        source_shot_id: str | None,
        source_type: str | None,
        model: str | None,
        prompt_hash: str | None,
    ) -> None:
        await self._exec(
            self._SQL_INSERT_OCCURRENCE,
            (
                entity_type,
                canonical,
                alias,
                lang,
                polarity,
                source_shot_id or f"manual:{entity_type}:{canonical}:{alias}:{lang}",
                source_type,
                model,
                prompt_hash or "manual",
            ),
        )

    async def list_roles(self, lang: str | None = None) -> tuple[str, ...]:
        if lang is None:
            rows = await self._fetchall(self._SQL_LIST_ROLES)
        else:
            rows = await self._fetchall(self._SQL_LIST_ROLES_LANG, (lang,))
        return tuple(str(r[0]) for r in rows)

    async def list_negative_roles(self, lang: str | None = None) -> tuple[str, ...]:
        if lang is None:
            rows = await self._fetchall(self._SQL_LIST_NEGATIVE_ROLES)
        else:
            rows = await self._fetchall(self._SQL_LIST_NEGATIVE_ROLES_LANG, (lang,))
        return tuple(str(r[0]) for r in rows)

    async def upsert_seniority(self, level: str) -> None:
        await self._exec(self._SQL_UPSERT_SENIORITY, (level,))

    async def list_seniority(self) -> tuple[str, ...]:
        rows = await self._fetchall(self._SQL_LIST_SENIORITY)
        return tuple(str(r[0]) for r in rows)

    async def upsert_anti_pattern(self, pattern: str) -> None:
        await self._exec(self._SQL_UPSERT_ANTI, (pattern,))

    async def list_anti_patterns(self) -> tuple[str, ...]:
        rows = await self._fetchall(self._SQL_LIST_ANTI)
        return tuple(str(r[0]) for r in rows)

    async def upsert_positive_keyword(self, term: str, *, weight: int = 1) -> None:
        await self._exec("DELETE FROM jf_ontology_negative_keyword WHERE term = $1", (term,))
        await self._exec(self._SQL_UPSERT_POSITIVE_KEYWORD, (term, weight))

    async def list_positive_keywords(self) -> tuple[dict[str, object], ...]:
        rows = await self._fetchall(self._SQL_LIST_POSITIVE_KEYWORDS)
        return tuple(
            {
                "term": str(term),
                "weight": int(cast("int | str | float", weight)),
            }
            for term, weight in rows
        )

    async def upsert_negative_keyword(self, term: str, *, weight: int = 1) -> None:
        await self._exec("DELETE FROM jf_ontology_positive_keyword WHERE term = $1", (term,))
        await self._exec(self._SQL_UPSERT_NEGATIVE_KEYWORD, (term, weight))

    async def list_negative_keywords(self) -> tuple[dict[str, object], ...]:
        rows = await self._fetchall(self._SQL_LIST_NEGATIVE_KEYWORDS)
        return tuple(
            {
                "term": str(term),
                "weight": int(cast("int | str | float", weight)),
            }
            for term, weight in rows
        )

    async def upsert_skill_alias(self, alias: str, canonical: str, lang: str = "en") -> None:
        await self.upsert_skill(canonical, alias=alias, lang=lang)

    async def lookup_skill(self, alias: str) -> str | None:
        row = await self._fetchone(self._SQL_LOOKUP_SKILL, (alias,))
        return str(row[0]) if row else None

    async def upsert_shot_graph(self, graph: ShotOntologyGraph) -> None:
        pool = await self._ensure_initialized()
        graph_id = graph.graph_id
        shot_id = graph.shot_id
        source_type = graph.source_type
        payload = graph.model_dump(mode="json")
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM jf_ontology_graph_version WHERE graph_id = $1", graph_id
            )
            await conn.execute("DELETE FROM jf_ontology_node WHERE graph_id = $1", graph_id)
            await conn.execute("DELETE FROM jf_ontology_edge WHERE graph_id = $1", graph_id)
            await conn.execute("DELETE FROM jf_ontology_evidence WHERE graph_id = $1", graph_id)
            await conn.execute(
                "INSERT INTO jf_ontology_graph_version (graph_id, shot_id, source_type, payload_json) "
                "VALUES ($1, $2, $3, $4::jsonb)",
                graph_id,
                shot_id,
                source_type,
                json.dumps(payload, ensure_ascii=False),
            )
            for node in getattr(graph, "nodes", ()):
                await conn.execute(
                    "INSERT INTO jf_ontology_node (node_id, graph_id, kind, canonical, display, lang, attrs_json) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb) ON CONFLICT (node_id) DO NOTHING",
                    node.node_id,
                    graph_id,
                    node.kind,
                    node.canonical,
                    node.display,
                    node.lang,
                    json.dumps(node.attrs, ensure_ascii=False),
                )
            for edge in getattr(graph, "edges", ()):
                await conn.execute(
                    "INSERT INTO jf_ontology_edge "
                    "(edge_id, graph_id, subject_node_id, predicate, object_node_id, polarity, weight, confidence, arity_group_id, attrs_json) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)",
                    edge.edge_id,
                    graph_id,
                    edge.subject_node_id,
                    edge.predicate,
                    edge.object_node_id,
                    edge.polarity,
                    edge.weight,
                    edge.confidence,
                    edge.arity_group_id,
                    json.dumps(edge.attrs, ensure_ascii=False),
                )
            for ev in getattr(graph, "evidence", ()):
                await conn.execute(
                    "INSERT INTO jf_ontology_evidence "
                    "(evidence_id, graph_id, edge_id, source_shot_id, source_type, source_section, text_span, extraction_confidence, model, prompt_hash) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                    ev.evidence_id,
                    graph_id,
                    ev.edge_id,
                    ev.source_shot_id,
                    ev.source_type,
                    ev.source_section,
                    ev.text_span,
                    ev.extraction_confidence,
                    ev.model,
                    ev.prompt_hash,
                )

    async def upsert_term_stats(self, stats: tuple[OntologyTermStat, ...]) -> None:
        pool = await self._ensure_initialized()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM jf_ontology_term_stat")
            for stat in stats:
                await conn.execute(
                    self._SQL_UPSERT_TERM_STAT,
                    stat.entity_type,
                    stat.canonical,
                    stat.polarity,
                    json.dumps(stat.aliases, ensure_ascii=False),
                    stat.positive_count,
                    stat.negative_count,
                    stat.contextual_count,
                    stat.positive_weight,
                    stat.negative_weight,
                    stat.contextual_weight,
                    stat.recency_weight,
                    stat.section_weight,
                    stat.keyness,
                    stat.score,
                    json.dumps(stat.antonyms, ensure_ascii=False),
                    json.dumps(stat.related_terms, ensure_ascii=False),
                )

    async def upsert_compiled_ontology(self, ontology: CompiledOntology) -> None:
        pool = await self._ensure_initialized()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM jf_ontology_compiled_relation")
            await conn.execute("DELETE FROM jf_ontology_compiled_term")
            for term in ontology.terms:
                await conn.execute(
                    "INSERT INTO jf_ontology_compiled_term "
                    "(canonical, entity_type, semantic_role, polarity, scope, source_section, "
                    "aliases_json, evidence_shot_ids_json, support_count, contrast_count, "
                    "confidence, weight, accepted, reject_reason, rationale) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10, $11, $12, $13, $14, $15)",
                    term.canonical,
                    term.entity_type,
                    term.semantic_role,
                    term.polarity,
                    term.scope,
                    term.source_section,
                    json.dumps(term.aliases, ensure_ascii=False),
                    json.dumps(term.evidence_shot_ids, ensure_ascii=False),
                    term.support_count,
                    term.contrast_count,
                    term.confidence,
                    term.weight,
                    term.accepted,
                    term.reject_reason,
                    term.rationale,
                )
            for relation in ontology.relations:
                await conn.execute(
                    "INSERT INTO jf_ontology_compiled_relation "
                    "(subject, predicate, object, polarity, evidence_shot_ids_json, "
                    "confidence, weight, rationale) "
                    "VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8)",
                    relation.subject,
                    relation.predicate,
                    relation.object,
                    relation.polarity,
                    json.dumps(relation.evidence_shot_ids, ensure_ascii=False),
                    relation.confidence,
                    relation.weight,
                    relation.rationale,
                )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


@register_ontology_store("postgres")
def _build_postgres_ontology_store(settings: Settings) -> PostgresOntologyStore:
    """Build a Postgres-backed ontology store.

    Reuses the same DSN and pool sizing as the main
    ``PostgreSQLStore`` so the dedup store and the ontology live
    in the same database and the pool can be tuned in one place.
    """
    if not settings.store_dsn:
        msg = "store_dsn is required for PostgresOntologyStore"
        raise ValueError(msg)
    dsn = (
        settings.store_dsn.get_secret_value()
        if hasattr(settings.store_dsn, "get_secret_value")
        else str(settings.store_dsn)
    )
    return PostgresOntologyStore(
        dsn=dsn,
        pool_min=settings.store_pool_min,
        pool_max=settings.store_pool_max,
    )
