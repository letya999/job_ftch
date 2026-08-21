"""Ontology shot enrichment (per ADR-019).

Owns:
  - `_build_shot_extraction_prompt(text, kind)` — prompt that asks the
    LLM to canonicalise a free-form shot into the ontology store.
  - `_enrich_ontology_from_shot(text, kind, llm, store)` — async
    extraction + ontology upsert.
  - `add_example_to_profile_with_enrichment(managed, kind, text, llm, store)`
    — high-level helper that adds an example and also pushes any new
    skills / roles / domains to the ontology store.
  - `load_resume_with_enrichment(text, ...)` — `load_resume` +
    enrichment, used by the bot adapter.

Per ADR-030, the ontology store is DB-backed with a file fallback.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from job_ftch.application.contracts import OntologyStore
    from job_ftch.config import Settings
    from job_ftch.domain import ManagedCandidateProfile, MaterializedOntologyTerms


logger = structlog.get_logger(__name__)


def ontology_compiler_runtime_settings(settings: Settings) -> Settings:
    """Same model/timeout Telegram `rebuild-ontology` already applies."""
    return settings.model_copy(
        update={
            "llm_backend": "openai",
            "openai_model": settings.ontology_compiler_model,
            "openai_timeout_seconds": settings.ontology_compiler_timeout_seconds,
        }
    )


def _llm_for_ontology_compile(passed: object | None) -> object | None:
    """Use the ontology compiler provider, not the extraction `openai_model`."""
    classify = getattr(passed, "classify", None) if passed is not None else None
    if not callable(classify) or type(passed).__name__ == "HeuristicLLMProvider":
        return passed
    current = str(
        getattr(passed, "model_id", None)
        or getattr(passed, "model", None)
        or getattr(passed, "_model", None)
        or ""
    ).strip()
    from job_ftch.config import get_settings

    settings = get_settings()
    wanted = str(getattr(settings, "ontology_compiler_model", "") or "").strip()
    if not wanted or current in {"", wanted}:
        return passed
    from job_ftch.application.registry import create_llm

    return create_llm(ontology_compiler_runtime_settings(settings))


_SHOT_KIND_LABEL: dict[str, str] = {
    "positive_resume": "good-resume example",
    "negative_resume": "bad-resume example",
    "positive_job": "good-job example",
    "negative_job": "bad-job example",
}


def _build_shot_extraction_prompt(text: str, kind: str) -> str:
    label = _SHOT_KIND_LABEL.get(kind, kind)
    is_negative = kind.startswith("negative")
    keyword_instruction = (
        "This is a negative example: fill negative_keywords only. Leave positive_keywords empty."
        if is_negative
        else "This is a positive example: fill positive_keywords only. "
        "Leave negative_keywords empty."
    )
    return (
        f"Example kind metadata: {label}.\n"
        "Do not extract the metadata label itself as a keyword.\n"
        f"{keyword_instruction}\n\n"
        f"INPUT:\n{text}\n\n"
        "Extract structured info per the JSON schema. Canonical names of "
        "skills, roles, and technologies must be in English lowercase. For "
        "anti_patterns, use the language of the input text. Extract keywords "
        "as concise weighted relevance signals with weight 1-5. Never use "
        "generic words like good-job, bad-job, good-resume, bad-resume, "
        "positive example, or negative example as keywords. Keep the result "
        "small: at most 12 skills, 5 roles, 3 seniority values, 6 keywords, "
        "and (for negative examples only) 6 anti-patterns. Every term must be "
        "a short canonical phrase, never a copied sentence, contact detail, "
        "company name, salary, date, or location."
    )


async def _write_materialized_terms(
    *,
    kind: str,
    text: str,
    ontology_store: OntologyStore,
    materialized: object,
    model: str,
    prompt_hash: str,
    ontology: object | None = None,
    term_stats: object | None = None,
) -> None:
    """Project materialized terms into the live ontology tables."""
    from job_ftch.application.profile_parsing import _detect_text_language_simple

    lang = _detect_text_language_simple(text)
    source_type = "resume" if "resume" in kind else "vacancy"
    source_shot_id = hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()

    async def write(operation: str, value: str, call: object) -> None:
        try:
            await call  # type: ignore[misc]
        except Exception as exc:
            logger.error(
                "ontology_upsert_failed",
                category=kind,
                shot_hash=source_shot_id,
                operation=operation,
                value_hash=hashlib.sha256(value.encode()).hexdigest(),
                exception_type=type(exc).__name__,
            )
            raise RuntimeError(f"ontology {operation} failed for {kind}") from exc

    compiled_writer = getattr(ontology_store, "upsert_compiled_ontology", None)
    if ontology is not None and callable(compiled_writer):
        await write("upsert_compiled_ontology", source_shot_id, compiled_writer(ontology))
    graph_writer = getattr(ontology_store, "upsert_shot_graph", None)
    if ontology is not None and callable(graph_writer):
        from job_ftch.application.ontology_graph_builder import build_ontology_graph_from_compiled
        from job_ftch.domain import CompiledOntology, MaterializedOntologyTerms

        compiled = ontology if isinstance(ontology, CompiledOntology) else None
        if compiled is not None:
            terms = materialized if isinstance(materialized, MaterializedOntologyTerms) else None
            graph = build_ontology_graph_from_compiled(
                ontology=compiled,
                graph_id="compiled:profile",
                shot_id=source_shot_id,
                model=model,
                prompt_hash=prompt_hash,
                materialized=terms,
            )
            await write("upsert_shot_graph", graph.graph_id, graph_writer(graph))
    stat_writer = getattr(ontology_store, "upsert_term_stats", None)
    if term_stats is not None and callable(stat_writer):
        await write("upsert_term_stats", source_shot_id, stat_writer(term_stats))

    anti_patterns = getattr(materialized, "anti_patterns", ()) or ()
    for pattern in anti_patterns:
        await write("upsert_anti_pattern", pattern, ontology_store.upsert_anti_pattern(pattern))
    for keyword, weight in getattr(materialized, "negative_keywords", ()) or ():
        await write(
            "upsert_negative_keyword",
            keyword,
            ontology_store.upsert_negative_keyword(keyword, weight=weight),
        )
    for skill in getattr(materialized, "negative_skills", ()) or ():
        await write(
            "upsert_skill",
            skill,
            ontology_store.upsert_skill(
                skill,
                alias=None,
                lang=lang,
                source_shot_id=source_shot_id,
                source_type=source_type,
                polarity="negative",
                model=model,
                prompt_hash=prompt_hash,
            ),
        )
    for role in getattr(materialized, "negative_roles", ()) or ():
        await write(
            "upsert_role",
            role,
            ontology_store.upsert_role(
                role,
                alias=None,
                lang=lang,
                source_shot_id=source_shot_id,
                source_type=source_type,
                polarity="negative",
                model=model,
                prompt_hash=prompt_hash,
            ),
        )
    for skill in getattr(materialized, "positive_skills", ()) or ():
        await write(
            "upsert_skill",
            skill,
            ontology_store.upsert_skill(
                skill,
                alias=None,
                lang=lang,
                source_shot_id=source_shot_id,
                source_type=source_type,
                polarity="positive",
                model=model,
                prompt_hash=prompt_hash,
            ),
        )
    for role in getattr(materialized, "positive_roles", ()) or ():
        await write(
            "upsert_role",
            role,
            ontology_store.upsert_role(
                role,
                alias=None,
                lang=lang,
                source_shot_id=source_shot_id,
                source_type=source_type,
                polarity="positive",
                model=model,
                prompt_hash=prompt_hash,
            ),
        )
    for level in getattr(materialized, "seniority", ()) or ():
        await write("upsert_seniority", level, ontology_store.upsert_seniority(level))
    for keyword, weight in getattr(materialized, "positive_keywords", ()) or ():
        await write(
            "upsert_positive_keyword",
            keyword,
            ontology_store.upsert_positive_keyword(keyword, weight=weight),
        )


_ROLE_HINTS: tuple[str, ...] = (
    "llm engineer",
    "ml engineer",
    "machine learning engineer",
    "data engineer",
    "data scientist",
    "mlops engineer",
    "ai engineer",
    "nlp engineer",
    "backend engineer",
    "frontend engineer",
    "full stack",
    "devops engineer",
    "product manager",
    "software engineer",
    "platform engineer",
)

# Keep this list local to application (no infrastructure import).
_HEURISTIC_SKILLS: tuple[str, ...] = (
    "python",
    "pytorch",
    "tensorflow",
    "docker",
    "kubernetes",
    "rag",
    "sql",
    "airflow",
    "fastapi",
    "llm",
    "nlp",
    "react",
    "typescript",
    "javascript",
    "java",
    "go",
    "rust",
    "postgresql",
    "redis",
    "kafka",
    "aws",
    "gcp",
    "azure",
    "pandas",
    "numpy",
    "scikit-learn",
    "sklearn",
    "transformers",
    "langchain",
    "llamaindex",
    "openai",
    "anthropic",
    "claude",
    "gpt",
    "computer vision",
    "machine learning",
    "deep learning",
    "mlops",
)

_SENIORITY_TOKENS: dict[str, tuple[str, ...]] = {
    "principal": ("principal", "staff"),
    "lead": ("lead", "tech lead", "team lead", "тимлид"),
    "senior": ("senior", "старш", "sr.", "sr "),
    "middle": ("middle", "mid-level", "мидл"),
    "junior": ("junior", "jr.", "джуниор"),
    "intern": ("intern", "стаж", "trainee"),
}


def _match_skills(text: str) -> tuple[str, ...]:
    import re

    lowered = text.casefold()
    found: list[str] = []
    multi = sorted((s for s in _HEURISTIC_SKILLS if " " in s), key=len, reverse=True)
    for skill in multi:
        if skill in lowered and skill not in found:
            found.append(skill)
    for skill in sorted((s for s in _HEURISTIC_SKILLS if " " not in s), key=len, reverse=True):
        if skill in found:
            continue
        if re.search(rf"(?<![a-z0-9+#-]){re.escape(skill)}(?![a-z0-9+#-])", lowered):
            found.append(skill)
    return tuple(found[:12])


def _match_seniority(text: str) -> str | None:
    lowered = text.casefold()
    for level, tokens in _SENIORITY_TOKENS.items():
        if any(token in lowered for token in tokens):
            return level
    return None


def _explicit_refusal_terms(text: str) -> tuple[str, ...]:
    """Surface explicit refusals from a negative shot, not every mentioned skill."""
    import re

    patterns = (
        r"\bno\s+([a-zA-Z][\w+.#/&-]{1,39})",
        r"\bwithout\s+([a-zA-Z][\w+.#/&-]{1,39})",
        r"\bdidn'?t\s+use\s+([a-zA-Z][\w+.#/&-]{1,39})",
        r"\bdoes not\s+use\s+([a-zA-Z][\w+.#/&-]{1,39})",
        r"не использовал(?:а|и|о)?(?:сь)?\s+([^\s,.;:]{2,40})",
        r"не использу(?:ю|ет|ем|ют)\s+([^\s,.;:]{2,40})",
        r"([A-Za-zА-Яа-я0-9+.#/]{2,40})\s+не использовал",
        r"нет опыта(?:\s+работы)?(?:\s+с|\s+в)?\s+([^\s,.;:]{2,40})",
    )
    skip = {"the", "a", "an", "опыт", "работы", "use", "using"}
    found: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            term = " ".join(match.group(1).strip().casefold().replace("_", " ").split())
            if term and term not in skip and term not in found:
                found.append(term)
    return tuple(found[:8])


def _heuristic_materialized_from_shot(text: str, kind: str) -> object:
    """Deterministic ontology projection when LLM compile is unavailable."""
    from job_ftch.domain import MaterializedOntologyTerms

    skills = _match_skills(text)
    lowered = text.casefold()
    roles = tuple(role for role in _ROLE_HINTS if role in lowered)[:5]
    seniority = _match_seniority(text)
    seniority_vals = (seniority,) if seniority else ()
    is_negative = kind.startswith("negative")
    if is_negative:
        refusals = _explicit_refusal_terms(text)
        keywords = tuple((term, 3) for term in refusals[:6])
        return MaterializedOntologyTerms(
            negative_skills=refusals,
            negative_roles=roles,
            anti_patterns=refusals[:6],
            negative_keywords=keywords,
            seniority=seniority_vals,
        )
    keywords = tuple((skill, 3) for skill in skills[:6])
    return MaterializedOntologyTerms(
        positive_skills=skills,
        positive_roles=roles,
        positive_keywords=keywords,
        seniority=seniority_vals,
    )


async def _enrich_ontology_heuristic(
    text: str,
    *,
    kind: str,
    ontology_store: OntologyStore,
) -> None:
    materialized = _heuristic_materialized_from_shot(text, kind)
    if not any(
        getattr(materialized, field)
        for field in (
            "positive_skills",
            "negative_skills",
            "positive_roles",
            "negative_roles",
            "anti_patterns",
            "positive_keywords",
            "negative_keywords",
            "seniority",
        )
    ):
        return
    prompt_hash = hashlib.sha256(f"heuristic:{kind}:{text}".encode()).hexdigest()
    await _write_materialized_terms(
        kind=kind,
        text=text,
        ontology_store=ontology_store,
        materialized=materialized,
        model="heuristic",
        prompt_hash=prompt_hash,
    )


async def _enrich_ontology_from_shot(
    text: str,
    *,
    kind: str,
    llm: object | None,
    ontology_store: OntologyStore,
) -> ManagedCandidateProfile | None:
    """Update ontology store from a shot (LLM compiler with heuristic fallback).

    The LLM is expected to expose structured `classify` calls used by
    `compile_ontology_from_shots`. Per ADR-019, this is point ① — the live
    ontology is kept in sync with user-supplied shots. When the LLM path is
    unavailable (heuristic backend / classify failure), a deterministic
    skill/role projection still fills the ontology tables so SQLite tenants
    do not stay empty after shot ingest.
    """
    from job_ftch.application.ontology_compiler import (
        compile_ontology_from_shots,
    )
    from job_ftch.config import get_settings

    llm = _llm_for_ontology_compile(llm)
    classify = getattr(llm, "classify", None) if llm is not None else None
    # HeuristicLLMProvider.classify is for job relevance, not ontology compiler schemas.
    llm_name = type(llm).__name__ if llm is not None else ""
    use_llm_compiler = callable(classify) and llm_name != "HeuristicLLMProvider"
    if use_llm_compiler:
        try:
            settings = get_settings()
            result = await compile_ontology_from_shots(
                shots=((kind, text),),
                llm=llm,
                prompt_path=settings.ontology_compiler_prompt_path,
                compile_timeout_seconds=settings.ontology_compiler_timeout_seconds,
            )
            await _write_materialized_terms(
                kind=kind,
                text=text,
                ontology_store=ontology_store,
                materialized=result.materialized,
                model=result.model,
                prompt_hash=result.prompt_hash,
                ontology=result.ontology,
                term_stats=result.term_stats,
            )
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "shot_enrichment_llm_failed_falling_back_heuristic",
                kind=kind,
                error=str(exc),
            )

    try:
        await _enrich_ontology_heuristic(text, kind=kind, ontology_store=ontology_store)
    except Exception as exc:  # noqa: BLE001
        logger.warning("shot_enrichment_heuristic_failed", kind=kind, error=str(exc))
    return None


def _shots_from_profile(managed: ManagedCandidateProfile) -> list[tuple[str, str]]:
    from job_ftch.application.profile_inputs import list_examples

    examples = list_examples(managed)
    shots: list[tuple[str, str]] = []
    for kind in ("positive_resume", "negative_resume", "positive_job", "negative_job"):
        for text in examples.get(kind, []):
            cleaned = str(text or "").strip()
            if cleaned:
                shots.append((kind, cleaned))
    return shots


def _materialized_positive_count(materialized: object) -> int:
    keywords = getattr(materialized, "positive_keywords", ()) or ()
    skills = getattr(materialized, "positive_skills", ()) or ()
    roles = getattr(materialized, "positive_roles", ()) or ()
    return len(keywords) + len(skills) + len(roles)


def _compiled_has_signal(ontology: object | None) -> bool:
    if ontology is None:
        return False
    return bool(getattr(ontology, "terms", None) or getattr(ontology, "relations", None))


def _merge_heuristic_materialized(shots: list[tuple[str, str]]) -> MaterializedOntologyTerms:
    from job_ftch.domain import MaterializedOntologyTerms

    pos_skills: list[str] = []
    pos_roles: list[str] = []
    pos_keywords: list[tuple[str, int]] = []
    neg_skills: list[str] = []
    neg_roles: list[str] = []
    neg_keywords: list[tuple[str, int]] = []
    anti: list[str] = []
    seniority: list[str] = []

    def _extend(bucket: list[str], values: object) -> None:
        if not isinstance(values, (list, tuple)):
            return
        for item in values:
            text = str(item).strip()
            if text and text not in bucket:
                bucket.append(text)

    def _extend_kw(bucket: list[tuple[str, int]], values: object) -> None:
        if not isinstance(values, (list, tuple)):
            return
        seen = {term for term, _weight in bucket}
        for item in values:
            if not isinstance(item, (tuple, list)) or not item:
                continue
            term = str(item[0]).strip()
            weight = int(item[1]) if len(item) > 1 else 3
            if term and term not in seen:
                bucket.append((term, weight))
                seen.add(term)

    for kind, text in shots:
        part = _heuristic_materialized_from_shot(text, kind)
        _extend(pos_skills, getattr(part, "positive_skills", ()))
        _extend(pos_roles, getattr(part, "positive_roles", ()))
        _extend_kw(pos_keywords, getattr(part, "positive_keywords", ()))
        _extend(neg_skills, getattr(part, "negative_skills", ()))
        _extend(neg_roles, getattr(part, "negative_roles", ()))
        _extend_kw(neg_keywords, getattr(part, "negative_keywords", ()))
        _extend(anti, getattr(part, "anti_patterns", ()))
        _extend(seniority, getattr(part, "seniority", ()))
    return MaterializedOntologyTerms(
        positive_skills=tuple(pos_skills[:24]),
        positive_roles=tuple(pos_roles[:12]),
        positive_keywords=tuple(pos_keywords[:24]),
        negative_skills=tuple(neg_skills[:24]),
        negative_roles=tuple(neg_roles[:12]),
        negative_keywords=tuple(neg_keywords[:24]),
        anti_patterns=tuple(anti[:12]),
        seniority=tuple(seniority[:6]),
    )


async def compile_profile_ontology(
    managed: ManagedCandidateProfile,
    *,
    llm: object | None,
    ontology_store: OntologyStore | None,
) -> dict[str, object]:
    """Compile/project ontology from every labeled shot on the profile.

    Single-shot LLM compile can succeed with an empty projection and skip the
    heuristic fallback. Refresh therefore always compiles the full shot set,
    then heuristically fills any empty positive projection.
    """
    if ontology_store is None:
        return {"pos_added": 0, "ontology_errors": ["ontology_store_missing"]}
    shots = _shots_from_profile(managed)
    if not shots:
        return {"pos_added": 0, "ontology_errors": []}

    errors: list[str] = []
    ontology = None
    materialized: MaterializedOntologyTerms | None = None
    term_stats = None
    model = "heuristic"
    prompt_hash = hashlib.sha256(
        "\n".join(f"{kind}:{text}" for kind, text in shots).encode("utf-8")
    ).hexdigest()

    compiler_llm = _llm_for_ontology_compile(llm)
    classify = getattr(compiler_llm, "classify", None) if compiler_llm is not None else None
    llm_name = type(compiler_llm).__name__ if compiler_llm is not None else ""
    use_llm_compiler = callable(classify) and llm_name != "HeuristicLLMProvider"
    if use_llm_compiler:
        try:
            from job_ftch.application.ontology_compiler import compile_ontology_from_shots
            from job_ftch.config import get_settings

            settings = get_settings()
            result = await compile_ontology_from_shots(
                shots=shots,
                llm=compiler_llm,
                prompt_path=settings.ontology_compiler_prompt_path,
                compile_timeout_seconds=settings.ontology_compiler_timeout_seconds,
            )
            ontology = result.ontology
            materialized = result.materialized
            term_stats = result.term_stats
            model = result.model
            prompt_hash = result.prompt_hash
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")
            logger.warning("profile_ontology_compile_failed", error=str(exc))

    if materialized is None or (
        _materialized_positive_count(materialized) == 0 and not _compiled_has_signal(ontology)
    ):
        heuristic = _merge_heuristic_materialized(shots)
        if _materialized_positive_count(heuristic) > 0:
            materialized = heuristic
            model = "heuristic"
        elif materialized is None:
            materialized = heuristic

    if materialized is None:
        return {"pos_added": 0, "ontology_errors": errors or ["empty_projection"]}

    reset = getattr(ontology_store, "reset_live_projection", None)
    if callable(reset):
        try:
            await reset()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")
            return {"pos_added": 0, "ontology_errors": errors}

    has_negative_shots = any(kind.startswith("negative") for kind, _text in shots)
    has_anti = bool(
        getattr(materialized, "anti_patterns", ())
        or getattr(materialized, "negative_skills", ())
        or getattr(materialized, "negative_roles", ())
        or getattr(materialized, "negative_keywords", ())
    )
    if has_negative_shots and not has_anti:
        heuristic = _merge_heuristic_materialized(shots)
        copier = getattr(materialized, "model_copy", None)
        if callable(copier):
            materialized = copier(
                update={
                    "anti_patterns": heuristic.anti_patterns,
                    "negative_skills": heuristic.negative_skills,
                    "negative_roles": heuristic.negative_roles,
                    "negative_keywords": heuristic.negative_keywords,
                }
            )

    sample_kind, sample_text = shots[0]
    try:
        await _write_materialized_terms(
            kind=sample_kind,
            text=sample_text,
            ontology_store=ontology_store,
            materialized=materialized,
            model=model,
            prompt_hash=prompt_hash,
            ontology=ontology,
            term_stats=term_stats,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{type(exc).__name__}: {exc}")
        return {"pos_added": 0, "ontology_errors": errors}

    return {
        "pos_added": _materialized_positive_count(materialized),
        "ontology_errors": errors,
        "model": model,
    }


async def add_example_to_profile_with_enrichment(
    managed: ManagedCandidateProfile,
    text: str,
    *,
    kind: str,
    llm: object | None,
    ontology_store: OntologyStore | None,
) -> ManagedCandidateProfile:
    """Add example to profile AND enrich the live ontology (ADR-019 point ①).

    Uses the LLM ontology compiler when available, otherwise heuristic
    skill/role projection so store-backed tenants (bot + MCP) stay populated.
    """
    from job_ftch.application.resume_extraction import add_example_to_profile

    managed = add_example_to_profile(managed, text, kind=kind)
    if ontology_store is not None:
        await _enrich_ontology_from_shot(text, kind=kind, llm=llm, ontology_store=ontology_store)
    return managed


async def load_resume_with_enrichment(
    text: str,
    *,
    user_id: str = "resume",
    profile_id: str = "resume",
    llm: object | None = None,
    ontology_store: OntologyStore | None = None,
) -> ManagedCandidateProfile:
    """Build a `ManagedCandidateProfile` from a resume text and enrich the
    ontology with the extracted skills / roles."""
    from job_ftch.application.resume_extraction import (
        build_profile_from_resume_text_async,
    )

    candidate_profile = await build_profile_from_resume_text_async(
        text,
        user_id=user_id,
        profile_id=profile_id,
        llm_provider=llm,
    )
    if llm is not None and ontology_store is not None:
        await _enrich_ontology_from_shot(
            text, kind="positive_resume", llm=llm, ontology_store=ontology_store
        )
    return candidate_profile
