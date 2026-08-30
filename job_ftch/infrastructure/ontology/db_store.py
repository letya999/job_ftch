"""DB-backed OntologyStore (per ADR-020).

Uses a separate aiosqlite/asyncpg connection to the same database as the dedup Store.
The ontology lives in dedicated tables (``jf_ontology_skill``, ``jf_ontology_role``,
``jf_ontology_seniority``, ``jf_ontology_anti``) so dedup resets never touch it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

try:
    import aiosqlite
except ImportError:
    aiosqlite = None  # type: ignore[assignment]

from job_ftch.application.registry import register_ontology_store

if TYPE_CHECKING:
    from job_ftch.config import Settings
    from job_ftch.domain import CompiledOntology, OntologyTermStat, ShotOntologyGraph


class DBOntologyStore:
    """SQLite-backed live ontology (per ADR-020)."""

    _SQL_UPSERT_SKILL = (
        "INSERT INTO jf_ontology_skill (canonical, alias, lang, polarity, source_shot_id, source_type, model, prompt_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(canonical, alias, lang) DO UPDATE SET "
        "polarity = excluded.polarity, source_shot_id = excluded.source_shot_id, source_type = excluded.source_type, model = excluded.model, prompt_hash = excluded.prompt_hash"
    )
    _SQL_LIST_SKILLS = (
        "SELECT DISTINCT canonical FROM jf_ontology_occurrence "
        "WHERE entity_type = 'skill' AND polarity = 'positive'"
    )
    _SQL_LIST_SKILLS_LANG = (
        "SELECT DISTINCT canonical FROM jf_ontology_occurrence "
        "WHERE entity_type = 'skill' AND lang = ? AND polarity = 'positive'"
    )
    _SQL_LIST_NEGATIVE_SKILLS = (
        "SELECT DISTINCT canonical FROM jf_ontology_occurrence "
        "WHERE entity_type = 'skill' AND polarity = 'negative'"
    )
    _SQL_LIST_NEGATIVE_SKILLS_LANG = (
        "SELECT DISTINCT canonical FROM jf_ontology_occurrence "
        "WHERE entity_type = 'skill' AND lang = ? AND polarity = 'negative'"
    )
    _SQL_LOOKUP_SKILL = (
        "SELECT canonical FROM jf_ontology_skill "
        "WHERE LOWER(alias) = LOWER(?) OR LOWER(canonical) = LOWER(?) LIMIT 1"
    )
    _SQL_INSERT_OCCURRENCE = (
        "INSERT OR IGNORE INTO jf_ontology_occurrence "
        "(entity_type, canonical, alias, lang, polarity, source_shot_id, source_type, model, prompt_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    _SQL_DELETE_SKILL_OPPOSITE = (
        "DELETE FROM jf_ontology_skill WHERE canonical = ? AND lang = ? AND polarity <> ?"
    )
    _SQL_DELETE_ROLE_OPPOSITE = (
        "DELETE FROM jf_ontology_role WHERE canonical = ? AND lang = ? AND polarity <> ?"
    )
    _SQL_DELETE_OCCURRENCE_OPPOSITE = (
        "DELETE FROM jf_ontology_occurrence "
        "WHERE entity_type = ? AND canonical = ? AND lang = ? AND polarity <> ?"
    )

    _SQL_UPSERT_ROLE = (
        "INSERT INTO jf_ontology_role (canonical, alias, lang, polarity, source_shot_id, source_type, model, prompt_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(canonical, alias, lang) DO UPDATE SET "
        "polarity = excluded.polarity, source_shot_id = excluded.source_shot_id, source_type = excluded.source_type, model = excluded.model, prompt_hash = excluded.prompt_hash"
    )
    _SQL_LIST_ROLES = (
        "SELECT DISTINCT canonical FROM jf_ontology_occurrence "
        "WHERE entity_type = 'role' AND polarity = 'positive'"
    )
    _SQL_LIST_ROLES_LANG = (
        "SELECT DISTINCT canonical FROM jf_ontology_occurrence "
        "WHERE entity_type = 'role' AND lang = ? AND polarity = 'positive'"
    )
    _SQL_LIST_NEGATIVE_ROLES = (
        "SELECT DISTINCT canonical FROM jf_ontology_occurrence "
        "WHERE entity_type = 'role' AND polarity = 'negative'"
    )
    _SQL_LIST_NEGATIVE_ROLES_LANG = (
        "SELECT DISTINCT canonical FROM jf_ontology_occurrence "
        "WHERE entity_type = 'role' AND lang = ? AND polarity = 'negative'"
    )

    _SQL_UPSERT_SENIORITY = "INSERT OR IGNORE INTO jf_ontology_seniority (level) VALUES (?)"
    _SQL_LIST_SENIORITY = "SELECT level FROM jf_ontology_seniority"

    _SQL_UPSERT_ANTI = "INSERT OR IGNORE INTO jf_ontology_anti (pattern) VALUES (?)"
    _SQL_LIST_ANTI = "SELECT pattern FROM jf_ontology_anti"
    _SQL_UPSERT_POSITIVE_KEYWORD = (
        "INSERT INTO jf_ontology_positive_keyword (term, weight) VALUES (?, ?) "
        "ON CONFLICT(term) DO UPDATE SET weight = excluded.weight"
    )
    _SQL_LIST_POSITIVE_KEYWORDS = (
        "SELECT term, weight FROM jf_ontology_positive_keyword ORDER BY weight DESC, term ASC"
    )
    _SQL_UPSERT_NEGATIVE_KEYWORD = (
        "INSERT INTO jf_ontology_negative_keyword (term, weight) VALUES (?, ?) "
        "ON CONFLICT(term) DO UPDATE SET weight = excluded.weight"
    )
    _SQL_LIST_NEGATIVE_KEYWORDS = (
        "SELECT term, weight FROM jf_ontology_negative_keyword ORDER BY weight DESC, term ASC"
    )
    _SQL_UPSERT_TERM_STAT = (
        "INSERT INTO jf_ontology_term_stat "
        "(entity_type, canonical, polarity, aliases_json, positive_count, negative_count, contextual_count, "
        "positive_weight, negative_weight, contextual_weight, recency_weight, section_weight, keyness, score, "
        "antonyms_json, related_terms_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(entity_type, canonical) DO UPDATE SET "
        "polarity = excluded.polarity, aliases_json = excluded.aliases_json, "
        "positive_count = excluded.positive_count, negative_count = excluded.negative_count, "
        "contextual_count = excluded.contextual_count, positive_weight = excluded.positive_weight, "
        "negative_weight = excluded.negative_weight, contextual_weight = excluded.contextual_weight, "
        "recency_weight = excluded.recency_weight, section_weight = excluded.section_weight, "
        "keyness = excluded.keyness, score = excluded.score, antonyms_json = excluded.antonyms_json, "
        "related_terms_json = excluded.related_terms_json, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
    )

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        self._conn: Any = None
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self) -> Any:
        if aiosqlite is None:
            raise ImportError("aiosqlite is required for DBOntologyStore.")
        async with self._init_lock:
            if self._conn is None:
                if self._path != ":memory:":
                    Path(self._path).parent.mkdir(parents=True, exist_ok=True)
                self._conn = await aiosqlite.connect(self._path)
                # Run the same migrations as SQLiteStore (ontology tables are part of 003_ontology.sql).
                migrations_dir = Path(__file__).parent.parent / "stores" / "migrations"
                for name in (
                    "001_initial_schema.sql",
                    "003_ontology.sql",
                    "009_ontology_occurrences.sql",
                    "011_ontology_graph.sql",
                    "012_ontology_term_stats.sql",
                    "013_compiled_ontology.sql",
                ):
                    path = migrations_dir / name
                    if path.exists():
                        await self._conn.executescript(path.read_text())
                # SQLite has no ``ADD COLUMN IF NOT EXISTS``.  The ontology
                # store is opened independently from the main store, so this
                # migration must be safe on both fresh and already-migrated
                # databases.
                async with self._conn.execute("PRAGMA table_info(jf_ontology_skill)") as cursor:
                    skill_columns = {str(row[1]) for row in await cursor.fetchall()}
                async with self._conn.execute("PRAGMA table_info(jf_ontology_role)") as cursor:
                    role_columns = {str(row[1]) for row in await cursor.fetchall()}
                additions = (
                    ("polarity", "TEXT NOT NULL DEFAULT 'positive'"),
                    ("source_shot_id", "TEXT"),
                    ("source_type", "TEXT"),
                    ("model", "TEXT"),
                    ("prompt_hash", "TEXT"),
                )
                for table, columns in (
                    ("jf_ontology_skill", skill_columns),
                    ("jf_ontology_role", role_columns),
                ):
                    for column, definition in additions:
                        if column not in columns:
                            await self._conn.execute(
                                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                            )
                await self._conn.commit()
        return self._conn

    async def _exec(self, sql: str, params: tuple[object, ...] = ()) -> None:
        conn = await self._ensure_initialized()
        await conn.execute(sql, params)
        await conn.commit()

    async def _fetchall(
        self, sql: str, params: tuple[object, ...] = ()
    ) -> list[tuple[object, ...]]:
        conn = await self._ensure_initialized()
        async with conn.execute(sql, params) as cursor:
            return [tuple(row) for row in await cursor.fetchall()]

    async def _fetchone(
        self, sql: str, params: tuple[object, ...] = ()
    ) -> tuple[object, ...] | None:
        conn = await self._ensure_initialized()
        async with conn.execute(sql, params) as cursor:
            row = await cursor.fetchone()
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
        await self._exec("DELETE FROM jf_ontology_negative_keyword WHERE term = ?", (term,))
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
        await self._exec("DELETE FROM jf_ontology_positive_keyword WHERE term = ?", (term,))
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
        row = await self._fetchone(self._SQL_LOOKUP_SKILL, (alias, alias))
        return str(row[0]) if row else None

    async def upsert_shot_graph(self, graph: ShotOntologyGraph) -> None:
        conn = await self._ensure_initialized()
        graph_id = graph.graph_id
        shot_id = graph.shot_id
        source_type = graph.source_type
        payload = graph.model_dump(mode="json")
        await conn.execute("BEGIN")
        try:
            await conn.execute(
                "DELETE FROM jf_ontology_graph_version WHERE graph_id = ?", (graph_id,)
            )
            await conn.execute("DELETE FROM jf_ontology_node WHERE graph_id = ?", (graph_id,))
            await conn.execute("DELETE FROM jf_ontology_edge WHERE graph_id = ?", (graph_id,))
            await conn.execute("DELETE FROM jf_ontology_evidence WHERE graph_id = ?", (graph_id,))
            await conn.execute(
                "INSERT INTO jf_ontology_graph_version (graph_id, shot_id, source_type, payload_json) VALUES (?, ?, ?, ?)",
                (graph_id, shot_id, source_type, json.dumps(payload, ensure_ascii=False)),
            )
            for node in getattr(graph, "nodes", ()):
                await conn.execute(
                    "INSERT OR IGNORE INTO jf_ontology_node "
                    "(node_id, graph_id, kind, canonical, display, lang, attrs_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        node.node_id,
                        graph_id,
                        node.kind,
                        node.canonical,
                        node.display,
                        node.lang,
                        json.dumps(node.attrs, ensure_ascii=False),
                    ),
                )
            for edge in getattr(graph, "edges", ()):
                await conn.execute(
                    "INSERT INTO jf_ontology_edge "
                    "(edge_id, graph_id, subject_node_id, predicate, object_node_id, polarity, weight, confidence, arity_group_id, attrs_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
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
                    ),
                )
            for ev in getattr(graph, "evidence", ()):
                await conn.execute(
                    "INSERT INTO jf_ontology_evidence "
                    "(evidence_id, graph_id, edge_id, source_shot_id, source_type, source_section, text_span, extraction_confidence, model, prompt_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
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
                    ),
                )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

    async def upsert_term_stats(self, stats: tuple[OntologyTermStat, ...]) -> None:
        conn = await self._ensure_initialized()
        await conn.execute("BEGIN")
        try:
            await conn.execute("DELETE FROM jf_ontology_term_stat")
            for stat in stats:
                await conn.execute(
                    self._SQL_UPSERT_TERM_STAT,
                    (
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
                    ),
                )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

    async def reset_live_projection(self) -> None:
        conn = await self._ensure_initialized()
        await conn.execute("BEGIN")
        try:
            await conn.execute("DELETE FROM jf_ontology_skill")
            await conn.execute("DELETE FROM jf_ontology_role")
            await conn.execute("DELETE FROM jf_ontology_seniority")
            await conn.execute("DELETE FROM jf_ontology_anti")
            await conn.execute("DELETE FROM jf_ontology_positive_keyword")
            await conn.execute("DELETE FROM jf_ontology_negative_keyword")
            await conn.execute("DELETE FROM jf_ontology_occurrence")
            await conn.execute("DELETE FROM jf_ontology_term_stat")
            await conn.execute("DELETE FROM jf_ontology_compiled_term")
            await conn.execute("DELETE FROM jf_ontology_compiled_relation")
            await conn.execute("DELETE FROM jf_ontology_graph_version")
            await conn.execute("DELETE FROM jf_ontology_node")
            await conn.execute("DELETE FROM jf_ontology_edge")
            await conn.execute("DELETE FROM jf_ontology_evidence")
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

    async def upsert_compiled_ontology(self, ontology: CompiledOntology) -> None:
        conn = await self._ensure_initialized()
        await conn.execute("BEGIN")
        try:
            await conn.execute("DELETE FROM jf_ontology_compiled_relation")
            await conn.execute("DELETE FROM jf_ontology_compiled_term")
            for term in ontology.terms:
                await conn.execute(
                    "INSERT INTO jf_ontology_compiled_term "
                    "(canonical, entity_type, semantic_role, polarity, scope, source_section, "
                    "aliases_json, evidence_shot_ids_json, support_count, contrast_count, "
                    "confidence, weight, accepted, reject_reason, rationale) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
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
                        1 if term.accepted else 0,
                        term.reject_reason,
                        term.rationale,
                    ),
                )
            for relation in ontology.relations:
                await conn.execute(
                    "INSERT INTO jf_ontology_compiled_relation "
                    "(subject, predicate, object, polarity, evidence_shot_ids_json, "
                    "confidence, weight, rationale) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        relation.subject,
                        relation.predicate,
                        relation.object,
                        relation.polarity,
                        json.dumps(relation.evidence_shot_ids, ensure_ascii=False),
                        relation.confidence,
                        relation.weight,
                        relation.rationale,
                    ),
                )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None


@register_ontology_store("db")
def _build_db_ontology_store(settings: Settings) -> DBOntologyStore:
    return DBOntologyStore(db_path=settings.store_path)
