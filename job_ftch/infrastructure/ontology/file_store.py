"""File-backed OntologyStore (per ADR-020, dev fallback).

Used when ``store_backend`` is not ``sqlite`` or ``postgresql`` (typically in tests
or dev without a persistent store). Stores as JSON files in
``infrastructure/ontology/data/``.

Not concurrent-safe across processes. Dev-only.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_ontology_store

if TYPE_CHECKING:
    from job_ftch.config import Settings
    from job_ftch.domain import CompiledOntology, OntologyTermStat, ShotOntologyGraph


def _data_dir() -> Path:
    return Path(__file__).parent / "data"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore
    except (json.JSONDecodeError, OSError):
        return {}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class FileOntologyStore:
    """JSON-file-backed live ontology (per ADR-020, fallback)."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._dir = data_dir or _data_dir()
        self._lock = asyncio.Lock()
        self._skill_path = self._dir / "ontology_skills.json"
        self._role_path = self._dir / "ontology_roles.json"
        self._seniority_path = self._dir / "ontology_seniority.json"
        self._anti_path = self._dir / "ontology_anti.json"
        self._positive_keywords_path = self._dir / "ontology_positive_keywords.json"
        self._negative_keywords_path = self._dir / "ontology_negative_keywords.json"
        self._graph_path = self._dir / "ontology_shot_graphs.json"
        self._term_stats_path = self._dir / "ontology_term_stats.json"
        self._compiled_ontology_path = self._dir / "ontology_compiled.json"

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _load(self, path: Path) -> dict[str, Any]:
        return _load(path)

    def _save(self, path: Path, data: dict[str, Any]) -> None:
        _save(path, data)

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
        async with self._lock:
            data = self._load(self._skill_path)
            entry = data.setdefault(
                canonical, {"canonical": canonical, "aliases": {}, "updated_at": self._now()}
            )
            entry.setdefault("aliases", {}).setdefault(lang, []).append(alias or canonical)
            entry["polarity"] = polarity
            entry["source_shot_id"] = source_shot_id
            entry["source_type"] = source_type
            entry["model"] = model
            entry["prompt_hash"] = prompt_hash
            entry["updated_at"] = self._now()
            self._save(self._skill_path, data)

    async def list_skills(self, lang: str | None = None) -> tuple[str, ...]:
        data = self._load(self._skill_path)
        if lang is None:
            return tuple(k for k, v in data.items() if v.get("polarity", "positive") == "positive")
        return tuple(
            canonical
            for canonical, entry in data.items()
            if lang in entry.get("aliases", {}) and entry.get("polarity", "positive") == "positive"
        )

    async def list_negative_skills(self, lang: str | None = None) -> tuple[str, ...]:
        data = self._load(self._skill_path)
        if lang is None:
            return tuple(k for k, v in data.items() if v.get("polarity", "positive") == "negative")
        return tuple(
            canonical
            for canonical, entry in data.items()
            if lang in entry.get("aliases", {}) and entry.get("polarity", "positive") == "negative"
        )

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
        async with self._lock:
            data = self._load(self._role_path)
            entry = data.setdefault(
                canonical, {"canonical": canonical, "aliases": {}, "updated_at": self._now()}
            )
            entry.setdefault("aliases", {}).setdefault(lang, []).append(alias or canonical)
            entry["polarity"] = polarity
            entry["source_shot_id"] = source_shot_id
            entry["source_type"] = source_type
            entry["model"] = model
            entry["prompt_hash"] = prompt_hash
            entry["updated_at"] = self._now()
            self._save(self._role_path, data)

    async def list_roles(self, lang: str | None = None) -> tuple[str, ...]:
        data = self._load(self._role_path)
        if lang is None:
            return tuple(k for k, v in data.items() if v.get("polarity", "positive") == "positive")
        return tuple(
            canonical
            for canonical, entry in data.items()
            if lang in entry.get("aliases", {}) and entry.get("polarity", "positive") == "positive"
        )

    async def list_negative_roles(self, lang: str | None = None) -> tuple[str, ...]:
        data = self._load(self._role_path)
        if lang is None:
            return tuple(k for k, v in data.items() if v.get("polarity", "positive") == "negative")
        return tuple(
            canonical
            for canonical, entry in data.items()
            if lang in entry.get("aliases", {}) and entry.get("polarity", "positive") == "negative"
        )

    async def upsert_seniority(self, level: str) -> None:
        async with self._lock:
            data = self._load(self._seniority_path)
            data.setdefault("levels", []).append(level)
            data["levels"] = sorted(set(data["levels"]))
            self._save(self._seniority_path, data)

    async def list_seniority(self) -> tuple[str, ...]:
        data = self._load(self._seniority_path)
        return tuple(data.get("levels", []))

    async def upsert_anti_pattern(self, pattern: str) -> None:
        async with self._lock:
            data = self._load(self._anti_path)
            data.setdefault("patterns", []).append(pattern)
            data["patterns"] = sorted(set(data["patterns"]))
            self._save(self._anti_path, data)

    async def list_anti_patterns(self) -> tuple[str, ...]:
        data = self._load(self._anti_path)
        return tuple(data.get("patterns", []))

    async def upsert_positive_keyword(self, term: str, *, weight: int = 1) -> None:
        async with self._lock:
            negative_data = self._load(self._negative_keywords_path)
            negative_data.pop(term, None)
            self._save(self._negative_keywords_path, negative_data)
            data = self._load(self._positive_keywords_path)
            data[term] = {"term": term, "weight": weight, "updated_at": self._now()}
            self._save(self._positive_keywords_path, data)

    async def list_positive_keywords(self) -> tuple[dict[str, object], ...]:
        data = self._load(self._positive_keywords_path)
        items = sorted(
            data.values(),
            key=lambda item: (-int(item.get("weight", 0)), str(item.get("term", ""))),
        )
        return tuple(
            {"term": str(item["term"]), "weight": int(item.get("weight", 1))} for item in items
        )

    async def upsert_negative_keyword(self, term: str, *, weight: int = 1) -> None:
        async with self._lock:
            positive_data = self._load(self._positive_keywords_path)
            positive_data.pop(term, None)
            self._save(self._positive_keywords_path, positive_data)
            data = self._load(self._negative_keywords_path)
            data[term] = {"term": term, "weight": weight, "updated_at": self._now()}
            self._save(self._negative_keywords_path, data)

    async def list_negative_keywords(self) -> tuple[dict[str, object], ...]:
        data = self._load(self._negative_keywords_path)
        items = sorted(
            data.values(),
            key=lambda item: (-int(item.get("weight", 0)), str(item.get("term", ""))),
        )
        return tuple(
            {"term": str(item["term"]), "weight": int(item.get("weight", 1))} for item in items
        )

    async def upsert_skill_alias(self, alias: str, canonical: str, lang: str = "en") -> None:
        await self.upsert_skill(canonical, alias=alias, lang=lang)

    async def lookup_skill(self, alias: str) -> str | None:
        data = self._load(self._skill_path)
        lowered = alias.casefold()
        # Direct canonical match
        if lowered in {k.casefold() for k in data}:
            for k in data:
                if k.casefold() == lowered:
                    return k
        # Alias match
        for canonical, entry in data.items():
            for lang_aliases in entry.get("aliases", {}).values():
                if any(a.casefold() == lowered for a in lang_aliases):
                    return canonical
        return None

    async def upsert_shot_graph(self, graph: ShotOntologyGraph) -> None:
        async with self._lock:
            data = self._load(self._graph_path)
            graph_id = graph.graph_id
            data[graph_id] = graph.model_dump(mode="json")
            self._save(self._graph_path, data)

    async def upsert_term_stats(self, stats: tuple[OntologyTermStat, ...]) -> None:
        async with self._lock:
            data = {
                f"{stat.entity_type}:{stat.canonical}": stat.model_dump(mode="json")
                for stat in stats
            }
            self._save(self._term_stats_path, data)

    async def upsert_compiled_ontology(self, ontology: CompiledOntology) -> None:
        async with self._lock:
            self._save(self._compiled_ontology_path, ontology.model_dump(mode="json"))

    async def close(self) -> None:
        return None


@register_ontology_store("file")
def _build_file_ontology_store(settings: Settings) -> FileOntologyStore:
    return FileOntologyStore(data_dir=_data_dir())
