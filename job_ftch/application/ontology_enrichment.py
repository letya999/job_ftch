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


async def _enrich_ontology_from_shot(
    text: str,
    *,
    kind: str,
    llm: object,
    ontology_store: OntologyStore,
) -> ManagedCandidateProfile | None:
    """Run LLM on a shot, update ontology store. Returns None if anything fails.

    The LLM is expected to expose structured `classify` calls used by
    `compile_ontology_from_shots`. Per ADR-019, this is point ① — the live
    ontology is kept in sync with user-supplied shots.
    """
    from job_ftch.application.ontology_compiler import (
        compile_ontology_from_shots,
    )
    from job_ftch.application.profile_parsing import _detect_text_language_simple
    from job_ftch.config import get_settings

    classify = getattr(llm, "classify", None)
    if not callable(classify):
        return None
    try:
        settings = get_settings()
        result = await compile_ontology_from_shots(
            shots=((kind, text),),
            llm=llm,
            prompt_path=settings.ontology_compiler_prompt_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("shot_enrichment_llm_failed", kind=kind, error=str(exc))
        return None

    lang = _detect_text_language_simple(text)
    source_type = "resume" if "resume" in kind else "vacancy"
    source_shot_id = hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()
    prompt_hash = result.prompt_hash
    model = result.model

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
    if callable(compiled_writer):
        await write(
            "upsert_compiled_ontology",
            source_shot_id,
            compiled_writer(result.ontology),
        )
    stat_writer = getattr(ontology_store, "upsert_term_stats", None)
    if callable(stat_writer):
        await write("upsert_term_stats", source_shot_id, stat_writer(result.term_stats))

    for pattern in result.materialized.anti_patterns:
        await write("upsert_anti_pattern", pattern, ontology_store.upsert_anti_pattern(pattern))
    for keyword, weight in result.materialized.negative_keywords:
        await write(
            "upsert_negative_keyword",
            keyword,
            ontology_store.upsert_negative_keyword(keyword, weight=weight),
        )
    for skill in result.materialized.negative_skills:
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
    for role in result.materialized.negative_roles:
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
    for skill in result.materialized.positive_skills:
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
    for role in result.materialized.positive_roles:
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
    for level in result.materialized.seniority:
        await write("upsert_seniority", level, ontology_store.upsert_seniority(level))
    for keyword, weight in result.materialized.positive_keywords:
        await write(
            "upsert_positive_keyword",
            keyword,
            ontology_store.upsert_positive_keyword(keyword, weight=weight),
        )
    return None


async def add_example_to_profile_with_enrichment(
    managed: ManagedCandidateProfile,
    text: str,
    *,
    kind: str,
    llm: object,
    ontology_store: OntologyStore,
) -> ManagedCandidateProfile:
    """Add example to profile AND enrich the live ontology via LLM (ADR-019
    point ①). Sync `add_example_to_profile` still works for tests and
    non-LLM callers.
    """
    from job_ftch.application.resume_extraction import add_example_to_profile

    managed = add_example_to_profile(managed, text, kind=kind)
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
