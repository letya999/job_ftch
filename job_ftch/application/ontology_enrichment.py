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
    from job_ftch.domain import ManagedCandidateProfile


logger = structlog.get_logger(__name__)

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


def _heuristic_materialized_from_shot(text: str, kind: str) -> object:
    """Deterministic ontology projection when LLM compile is unavailable."""
    from job_ftch.domain import MaterializedOntologyTerms

    skills = _match_skills(text)
    lowered = text.casefold()
    roles = tuple(role for role in _ROLE_HINTS if role in lowered)[:5]
    seniority = _match_seniority(text)
    seniority_vals = (seniority,) if seniority else ()
    is_negative = kind.startswith("negative")
    keywords = tuple((skill, 3) for skill in skills[:6])
    if is_negative:
        return MaterializedOntologyTerms(
            negative_skills=skills,
            negative_roles=roles,
            anti_patterns=skills[:6],
            negative_keywords=keywords,
            seniority=seniority_vals,
        )
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
        await _enrich_ontology_from_shot(
            text, kind=kind, llm=llm, ontology_store=ontology_store
        )
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
