"""LLM-backed profile ontology compiler.

This module deliberately contains no domain-specific relevance dictionaries.
It turns labeled user shots into a structured ontology and a compatibility
projection for the existing runtime tables.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field

from job_ftch.domain import (
    CompiledOntology,
    CompiledOntologyRelation,
    CompiledOntologyTerm,
    MaterializedOntologyTerms,
    OntologyTermStat,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


ShotKind = Literal["positive_resume", "negative_resume", "positive_job", "negative_job"]

_STRUCTURAL_LABEL_TERMS = {
    "anti pattern",
    "anti role",
    "anti skill",
    "background skill",
    "context",
    "current role",
    "negative keyword",
    "past role",
    "positive keyword",
    "seniority",
    "supporting skill",
    "target role",
    "target skill",
}


class OntologyCompilerPrompts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    chunk_size: int = Field(default=8, ge=1, le=20)
    coverage_chunk_size: int = Field(default=2, ge=1, le=8)
    critique_enabled: bool = True
    candidate_system: str
    candidate_user_template: str
    coverage_system: str
    coverage_user_template: str
    compile_system: str
    compile_user_template: str
    critique_system: str
    critique_user_template: str


class LabeledOntologyShot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shot_id: str
    kind: ShotKind
    text: str


class OntologyCandidateChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    terms: tuple[CompiledOntologyTerm, ...] = Field(default=(), max_length=96)
    relations: tuple[CompiledOntologyRelation, ...] = Field(default=(), max_length=96)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class OntologyCompilationResult:
    ontology: CompiledOntology
    materialized: MaterializedOntologyTerms
    term_stats: tuple[OntologyTermStat, ...]
    prompt_hash: str
    model: str
    candidate_chunks: tuple[OntologyCandidateChunk, ...] = ()


def load_ontology_compiler_prompts(path: str | Path) -> OntologyCompilerPrompts:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return OntologyCompilerPrompts.model_validate(raw)


def shot_id_for_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def make_labeled_ontology_shots(
    shots: Sequence[tuple[str, str]],
) -> tuple[LabeledOntologyShot, ...]:
    return tuple(
        LabeledOntologyShot(shot_id=shot_id_for_text(text), kind=cast("ShotKind", kind), text=text)
        for kind, text in shots
    )


def _json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def normalize_compiled_term(value: str) -> str:
    value = value.strip().casefold().replace("_", " ").replace("-", " ")
    value = "".join(
        char if char.isalnum() or char.isspace() or char in ".+#/&" else " " for char in value
    )
    return " ".join(value.split())


def _chunks(
    shots: Sequence[LabeledOntologyShot],
    size: int,
) -> tuple[tuple[LabeledOntologyShot, ...], ...]:
    return tuple(tuple(shots[index : index + size]) for index in range(0, len(shots), size))


def _format_prompt(template: str, **values: str) -> str:
    return template.format(**values)


def _dedupe_terms(terms: Sequence[CompiledOntologyTerm]) -> tuple[CompiledOntologyTerm, ...]:
    by_key: dict[tuple[str, str], CompiledOntologyTerm] = {}
    for term in terms:
        canonical = normalize_compiled_term(term.canonical)
        if not canonical:
            continue
        key = (term.entity_type, canonical)
        existing = by_key.get(key)
        normalized = term.model_copy(update={"canonical": canonical})
        if (
            existing is None
            or (normalized.accepted and not existing.accepted)
            or (
                normalized.accepted
                and bool(normalized.evidence_shot_ids)
                and not bool(existing.evidence_shot_ids)
            )
        ):
            by_key[key] = normalized
    return tuple(by_key[key] for key in sorted(by_key))


def _dedupe_relations(
    relations: Sequence[CompiledOntologyRelation],
) -> tuple[CompiledOntologyRelation, ...]:
    by_key: dict[tuple[str, str, str], CompiledOntologyRelation] = {}
    for relation in relations:
        subject = normalize_compiled_term(relation.subject)
        obj = normalize_compiled_term(relation.object)
        if not subject or not obj:
            continue
        key = (subject, relation.predicate, obj)
        normalized = relation.model_copy(update={"subject": subject, "object": obj})
        existing = by_key.get(key)
        if existing is None or normalized.confidence > existing.confidence:
            by_key[key] = normalized
    return tuple(by_key[key] for key in sorted(by_key))


def _term_tokens(value: str) -> frozenset[str]:
    return frozenset(token for token in normalize_compiled_term(value).split() if token)


def _is_broader_term(candidate: str, accepted: Sequence[str]) -> bool:
    candidate_tokens = _term_tokens(candidate)
    if len(candidate_tokens) < 2:
        return True
    for value in accepted:
        accepted_tokens = _term_tokens(value)
        if candidate_tokens < accepted_tokens:
            return True
    return False


def sanitize_compiled_ontology(ontology: CompiledOntology) -> CompiledOntology:
    terms = _dedupe_terms(ontology.terms)
    accepted_polarity: dict[tuple[str, str], str] = {}
    sanitized_terms: list[CompiledOntologyTerm] = []
    for term in terms:
        if term.canonical in _STRUCTURAL_LABEL_TERMS:
            continue
        accepted = bool(term.accepted and term.evidence_shot_ids)
        key = (term.entity_type, term.canonical)
        previous = accepted_polarity.get(key)
        if accepted and previous is not None and previous != term.polarity:
            accepted = False
        if accepted:
            accepted_polarity[key] = term.polarity
        sanitized_terms.append(term.model_copy(update={"accepted": accepted}))
    return ontology.model_copy(
        update={
            "terms": tuple(sanitized_terms),
            "relations": _dedupe_relations(ontology.relations),
        }
    )


def _has_negative_shots(shots: Sequence[LabeledOntologyShot]) -> bool:
    return any(shot.kind.startswith("negative") for shot in shots)


def _has_accepted_negative_projection(ontology: CompiledOntology) -> bool:
    return any(
        term.accepted
        and term.semantic_role in {"negative_keyword", "anti_pattern", "anti_role", "anti_skill"}
        and term.evidence_shot_ids
        for term in ontology.terms
    )


def _has_negative_candidate(chunk: OntologyCandidateChunk) -> bool:
    return any(
        term.semantic_role in {"negative_keyword", "anti_pattern", "anti_role", "anti_skill"}
        and term.evidence_shot_ids
        for term in chunk.terms
    )


def _restore_negative_projection_from_candidates(
    ontology: CompiledOntology,
    candidate_chunks: Sequence[OntologyCandidateChunk],
) -> CompiledOntology:
    existing = {(term.entity_type, term.canonical) for term in ontology.terms}
    restored: list[CompiledOntologyTerm] = []
    for chunk in candidate_chunks:
        for term in chunk.terms:
            if term.semantic_role not in {
                "negative_keyword",
                "anti_pattern",
                "anti_role",
                "anti_skill",
            }:
                continue
            if not term.evidence_shot_ids:
                continue
            key = (term.entity_type, term.canonical)
            if key in existing:
                continue
            restored.append(
                term.model_copy(
                    update={
                        "accepted": True,
                        "polarity": "negative",
                        "scope": "anti" if term.scope == "unknown" else term.scope,
                        "reject_reason": "",
                    }
                )
            )
            existing.add(key)
            if len(restored) >= 12:
                break
        if len(restored) >= 12:
            break
    if not restored:
        return ontology
    return ontology.model_copy(update={"terms": (*ontology.terms, *restored)})


def _candidate_chunks_for_compile(
    candidate_chunks: Sequence[OntologyCandidateChunk],
) -> tuple[OntologyCandidateChunk, ...]:
    compact: list[OntologyCandidateChunk] = []
    for chunk in candidate_chunks:
        terms = tuple(
            term
            for term in chunk.terms
            if term.entity_type == "role"
            or term.semantic_role in {"anti_role", "anti_skill", "negative_keyword", "anti_pattern"}
            or (
                term.semantic_role in {"target_skill", "supporting_skill"}
                and (term.support_count >= 2 or len(term.evidence_shot_ids) >= 2)
            )
        )[:32]
        relation_terms = {normalize_compiled_term(term.canonical) for term in terms}
        relations = tuple(
            relation
            for relation in chunk.relations
            if normalize_compiled_term(relation.subject) in relation_terms
            or normalize_compiled_term(relation.object) in relation_terms
            or relation.polarity == "negative"
        )[:32]
        compact.append(
            OntologyCandidateChunk(
                terms=terms,
                relations=relations,
                warnings=chunk.warnings,
            )
        )
    return tuple(compact)


def _restore_projection_from_candidates(
    ontology: CompiledOntology,
    candidate_chunks: Sequence[OntologyCandidateChunk],
    shots: Sequence[LabeledOntologyShot],
) -> CompiledOntology:
    shot_kind = {shot.shot_id: shot.kind for shot in shots}
    existing = {
        (term.entity_type, normalize_compiled_term(term.canonical)) for term in ontology.terms
    }
    accepted_positive_roles: list[str] = [
        term.canonical
        for term in ontology.terms
        if term.accepted and term.entity_type == "role" and term.semantic_role == "target_role"
    ]
    by_key: dict[tuple[str, str], CompiledOntologyTerm] = {}
    candidate_by_canonical: dict[str, CompiledOntologyTerm] = {}
    positive_skill_evidence_by_canonical: dict[str, tuple[str, ...]] = {}
    positive_skill_support_by_canonical: dict[str, int] = {}
    positive_skill_terms_by_shot: dict[str, set[str]] = {}
    positive_candidate_keys: set[tuple[str, str]] = {
        (term.entity_type, canonical)
        for chunk in candidate_chunks
        for term in chunk.terms
        if term.polarity == "positive"
        if (canonical := normalize_compiled_term(term.canonical))
    }
    relation_terms_by_key: dict[str, tuple[str, ...]] = {}
    relation_confidence: dict[str, float] = {}
    relation_weight: dict[str, float] = {}
    for chunk in candidate_chunks:
        for term in chunk.terms:
            canonical = normalize_compiled_term(term.canonical)
            if canonical:
                candidate_by_canonical.setdefault(canonical, term)
            evidence_kinds = {shot_kind.get(shot_id, "") for shot_id in term.evidence_shot_ids}
            if (
                canonical
                and term.entity_type == "skill"
                and term.polarity == "positive"
                and term.semantic_role in {"target_skill", "supporting_skill"}
                and any(kind.startswith("positive") for kind in evidence_kinds)
                and not any(kind.startswith("negative") for kind in evidence_kinds)
            ):
                positive_skill_evidence_by_canonical[canonical] = tuple(
                    dict.fromkeys(
                        (
                            *positive_skill_evidence_by_canonical.get(canonical, ()),
                            *term.evidence_shot_ids,
                        )
                    )
                )
                positive_skill_support_by_canonical[canonical] = max(
                    positive_skill_support_by_canonical.get(canonical, 0),
                    term.support_count,
                    len(term.evidence_shot_ids),
                )
                for shot_id in term.evidence_shot_ids:
                    if shot_kind.get(shot_id, "").startswith("positive"):
                        positive_skill_terms_by_shot.setdefault(shot_id, set()).add(canonical)
        for relation in chunk.relations:
            if relation.polarity != "positive" or relation.predicate not in {
                "requires",
                "supports",
            }:
                continue
            for value in (relation.subject, relation.object):
                canonical = normalize_compiled_term(value)
                if not canonical:
                    continue
                relation_terms_by_key[canonical] = tuple(
                    dict.fromkeys(
                        (*relation_terms_by_key.get(canonical, ()), *relation.evidence_shot_ids)
                    )
                )
                relation_confidence[canonical] = max(
                    relation_confidence.get(canonical, 0.0), relation.confidence
                )
                relation_weight[canonical] = max(
                    relation_weight.get(canonical, 0.0), relation.weight
                )
    for chunk in candidate_chunks:
        for term in chunk.terms:
            canonical = normalize_compiled_term(term.canonical)
            if not canonical or canonical in _STRUCTURAL_LABEL_TERMS:
                continue
            evidence_kinds = {shot_kind.get(shot_id, "") for shot_id in term.evidence_shot_ids}
            has_positive_evidence = any(kind.startswith("positive") for kind in evidence_kinds)
            has_negative_evidence = any(kind.startswith("negative") for kind in evidence_kinds)
            positive_skill_evidence = positive_skill_evidence_by_canonical.get(canonical, ())
            positive_skill_support = positive_skill_support_by_canonical.get(
                canonical, term.support_count
            )
            positive_skill_source_width = max(
                (
                    len(positive_skill_terms_by_shot.get(shot_id, ()))
                    for shot_id in positive_skill_evidence
                ),
                default=0,
            )
            positive_skill_has_repeated_evidence = (
                len(positive_skill_evidence) >= 2 or positive_skill_support >= 2
            )
            positive_skill_has_bounded_single_evidence = (
                positive_skill_source_width <= 12
                and term.source_section in {"skills", "requirements", "responsibilities"}
                and term.confidence >= 0.78
                and term.weight >= 0.78
            )
            positive_role_evidence = (
                (
                    "positive_job" in evidence_kinds
                    and term.source_section in {"title", "responsibilities", "requirements"}
                )
                or (
                    "positive_resume" in evidence_kinds
                    and term.source_section in {"desired_role", "current_role"}
                )
                or len(term.evidence_shot_ids) >= 2
                or term.support_count >= 2
            )
            is_positive_role = (
                term.entity_type == "role"
                and term.semantic_role == "target_role"
                and term.polarity == "positive"
                and has_positive_evidence
                and not has_negative_evidence
                and positive_role_evidence
                and term.scope not in {"past", "background"}
                and term.source_section != "past_role"
                and term.confidence >= 0.74
                and term.weight >= 0.65
                and not _is_broader_term(canonical, accepted_positive_roles)
            )
            is_explicit_positive_skill = (
                term.entity_type == "skill"
                and term.polarity == "positive"
                and has_positive_evidence
                and not has_negative_evidence
                and term.semantic_role in {"target_role", "target_skill", "supporting_skill"}
                and term.semantic_role != "target_role"
                and term.scope not in {"past", "background"}
                and term.source_section != "past_role"
                and term.confidence >= 0.78
                and term.weight >= 0.65
                and (
                    positive_skill_has_repeated_evidence
                    or positive_skill_has_bounded_single_evidence
                )
            )
            is_negative_projection = (
                term.polarity == "negative"
                and has_negative_evidence
                and (
                    term.semantic_role
                    in {"negative_keyword", "anti_pattern", "anti_role", "anti_skill"}
                    or (
                        term.entity_type in {"role", "skill", "keyword", "anti_pattern"}
                        and term.source_section
                        in {
                            "title",
                            "requirements",
                            "responsibilities",
                            "skills",
                            "anti_reason",
                            "summary",
                        }
                        and term.confidence >= 0.75
                        and term.weight >= 0.7
                    )
                )
                and term.confidence >= 0.5
                and term.evidence_shot_ids
            )
            if (
                not is_positive_role
                and not is_explicit_positive_skill
                and not is_negative_projection
            ):
                continue
            normalized_update: dict[str, Any] = {
                "canonical": canonical,
                "accepted": True,
                "scope": "target" if (is_positive_role or is_explicit_positive_skill) else "anti",
                "reject_reason": "",
            }
            if is_explicit_positive_skill:
                normalized_update["evidence_shot_ids"] = positive_skill_evidence
                normalized_update["support_count"] = max(
                    term.support_count,
                    len(term.evidence_shot_ids),
                    len(positive_skill_evidence),
                    positive_skill_support,
                )
            entity_type = term.entity_type
            if is_negative_projection:
                normalized_update["support_count"] = max(
                    term.support_count, len(term.evidence_shot_ids)
                )
                if term.semantic_role not in {
                    "negative_keyword",
                    "anti_pattern",
                    "anti_role",
                    "anti_skill",
                }:
                    if term.entity_type == "role":
                        normalized_update["semantic_role"] = "anti_role"
                    elif term.entity_type == "skill":
                        normalized_update["semantic_role"] = "anti_skill"
                    else:
                        normalized_update["semantic_role"] = "negative_keyword"
                if (term.entity_type, canonical) in positive_candidate_keys or (
                    term.entity_type,
                    canonical,
                ) in by_key:
                    entity_type = "anti_pattern"
                    normalized_update.update(
                        {
                            "canonical": f"insufficient {canonical}",
                            "entity_type": entity_type,
                            "semantic_role": "anti_pattern",
                        }
                    )
            key = (entity_type, normalize_compiled_term(normalized_update["canonical"]))
            if key in existing:
                continue
            normalized = term.model_copy(update=normalized_update)
            previous = by_key.get(key)
            if previous is None or (
                normalized.support_count,
                len(normalized.evidence_shot_ids),
                normalized.confidence * normalized.weight,
            ) > (
                previous.support_count,
                len(previous.evidence_shot_ids),
                previous.confidence * previous.weight,
            ):
                by_key[key] = normalized
                if is_positive_role:
                    accepted_positive_roles.append(canonical)
    role_tokens = {_term_tokens(role) for role in accepted_positive_roles}
    for canonical, evidence_ids in relation_terms_by_key.items():
        if (candidate := candidate_by_canonical.get(canonical)) is not None:
            evidence_kinds = {shot_kind.get(shot_id, "") for shot_id in candidate.evidence_shot_ids}
            has_negative_evidence = any(kind.startswith("negative") for kind in evidence_kinds)
            if has_negative_evidence or candidate.semantic_role in {
                "anti_role",
                "anti_skill",
                "negative_keyword",
                "anti_pattern",
                "past_role",
                "target_role",
            }:
                continue
        relation_evidence_kinds = {shot_kind.get(shot_id, "") for shot_id in evidence_ids}
        if not any(kind.startswith("positive") for kind in relation_evidence_kinds):
            continue
        if _term_tokens(canonical) in role_tokens:
            continue
        key = ("skill", canonical)
        if key in existing or key in by_key:
            continue
        confidence = max(relation_confidence.get(canonical, 0.0), 0.65)
        weight = max(relation_weight.get(canonical, 0.0), 0.6)
        if confidence < 0.7 or weight < 0.6:
            continue
        by_key[key] = CompiledOntologyTerm(
            canonical=canonical,
            aliases=(),
            entity_type="skill",
            semantic_role="supporting_skill",
            polarity="positive",
            scope="supporting",
            source_section=(candidate.source_section if candidate is not None else "unknown"),
            evidence_shot_ids=evidence_ids,
            support_count=max(1, len(evidence_ids)),
            contrast_count=0,
            confidence=confidence,
            weight=weight,
            accepted=True,
            rationale="Recovered from positive ontology relation to a target/support concept.",
        )
    restored = tuple(by_key[key] for key in sorted(by_key))
    if not restored:
        return ontology
    return ontology.model_copy(update={"terms": (*ontology.terms, *restored)})


def materialize_compiled_ontology(
    ontology: CompiledOntology,
) -> tuple[MaterializedOntologyTerms, tuple[OntologyTermStat, ...]]:
    positive_roles: list[str] = []
    negative_roles: list[str] = []
    positive_skills: list[str] = []
    negative_skills: list[str] = []
    positive_keywords: list[tuple[str, int]] = []
    negative_keywords: list[tuple[str, int]] = []
    anti_patterns: list[str] = []
    seniority: list[str] = []
    stats: list[OntologyTermStat] = []

    for term in ontology.terms:
        score = round(term.weight * term.confidence, 4)
        positive_count = term.support_count if term.polarity == "positive" else 0
        negative_count = term.support_count if term.polarity == "negative" else 0
        contextual_count = term.support_count if term.polarity in {"neutral", "contextual"} else 0
        stats.append(
            OntologyTermStat(
                entity_type=term.entity_type,
                canonical=term.canonical,
                polarity=term.polarity,
                aliases=term.aliases,
                positive_count=positive_count,
                negative_count=negative_count,
                contextual_count=contextual_count,
                positive_weight=score if term.polarity == "positive" else 0.0,
                negative_weight=score if term.polarity == "negative" else 0.0,
                contextual_weight=score if term.polarity in {"neutral", "contextual"} else 0.0,
                recency_weight=0.0,
                section_weight=0.0,
                keyness=round(term.support_count - term.contrast_count, 4),
                score=score,
                related_terms=tuple(
                    relation.object
                    for relation in ontology.relations
                    if relation.subject == term.canonical
                )[:8],
            )
        )
        if not term.accepted:
            continue
        keyword_weight = max(1, min(5, round(term.weight * 5)))
        if term.entity_type == "role" and term.semantic_role == "target_role":
            positive_roles.append(term.canonical)
            positive_keywords.append((term.canonical, keyword_weight))
        elif term.entity_type == "role" and term.semantic_role == "anti_role":
            negative_roles.append(term.canonical)
            negative_keywords.append((term.canonical, keyword_weight))
        elif term.entity_type == "skill" and term.semantic_role in {
            "target_skill",
            "supporting_skill",
        }:
            positive_skills.append(term.canonical)
            has_keyword_strength = term.semantic_role == "target_skill" and (
                term.support_count >= 2
                or len(term.evidence_shot_ids) >= 2
                or term.source_section in {"title", "desired_role", "current_role", "summary"}
            )
            if has_keyword_strength:
                positive_keywords.append((term.canonical, keyword_weight))
        elif term.entity_type == "skill" and term.semantic_role == "anti_skill":
            negative_skills.append(term.canonical)
            negative_keywords.append((term.canonical, keyword_weight))
        elif term.semantic_role == "positive_keyword":
            positive_keywords.append((term.canonical, keyword_weight))
        elif term.semantic_role == "negative_keyword":
            negative_keywords.append((term.canonical, keyword_weight))
        elif term.semantic_role == "anti_pattern":
            anti_patterns.append(term.canonical)
            negative_keywords.append((term.canonical, keyword_weight))
        elif term.semantic_role == "seniority":
            seniority.append(term.canonical)

    materialized = MaterializedOntologyTerms(
        positive_roles=tuple(dict.fromkeys(positive_roles)),
        negative_roles=tuple(dict.fromkeys(negative_roles)),
        positive_skills=tuple(dict.fromkeys(positive_skills)),
        negative_skills=tuple(dict.fromkeys(negative_skills)),
        seniority=tuple(dict.fromkeys(seniority)),
        anti_patterns=tuple(dict.fromkeys(anti_patterns)),
        positive_keywords=tuple(
            sorted(dict(positive_keywords).items(), key=lambda item: (-item[1], item[0]))
        ),
        negative_keywords=tuple(
            sorted(dict(negative_keywords).items(), key=lambda item: (-item[1], item[0]))
        ),
    )
    return materialized, tuple(stats)


async def compile_ontology_from_shots(
    *,
    shots: Sequence[tuple[str, str]],
    llm: object,
    prompt_path: str | Path,
) -> OntologyCompilationResult:
    classify = getattr(llm, "classify", None)
    if not callable(classify):
        raise RuntimeError("configured LLM does not support structured classification")

    prompts = load_ontology_compiler_prompts(prompt_path)
    labeled = make_labeled_ontology_shots(shots)
    prompt_parts: list[str] = []
    candidate_chunks: list[OntologyCandidateChunk] = []
    for chunk in _chunks(labeled, prompts.chunk_size):
        user_prompt = _format_prompt(
            prompts.candidate_user_template,
            shots_json=_json([shot.model_dump(mode="json") for shot in chunk]),
        )
        prompt = f"{prompts.candidate_system}\n\n{user_prompt}"
        prompt_parts.append(prompt)
        candidate = await classify(prompt, OntologyCandidateChunk)
        if candidate is None:
            raise RuntimeError("ontology candidate extraction returned no result")
        candidate = candidate.model_copy(
            update={
                "terms": tuple(
                    term.model_copy(update={"accepted": False}) for term in candidate.terms
                )
            }
        )
        if any(shot.kind.startswith("negative") for shot in chunk) and not _has_negative_candidate(
            candidate
        ):
            rescue_terms = list(candidate.terms)
            rescue_relations = list(candidate.relations)
            for shot in chunk:
                if not shot.kind.startswith("negative"):
                    continue
                rescue_user_prompt = _format_prompt(
                    prompts.candidate_user_template,
                    shots_json=_json([shot.model_dump(mode="json")]),
                )
                rescue_prompt = (
                    f"{prompts.candidate_system}\n\n"
                    "This is a negative-shot rescue pass. Return only reusable rejection "
                    "signals from this shot: negative_keyword, anti_pattern, anti_role, "
                    "or anti_skill. Do not return positive target terms.\n\n"
                    f"{rescue_user_prompt}"
                )
                prompt_parts.append(rescue_prompt)
                rescued = await classify(rescue_prompt, OntologyCandidateChunk)
                if rescued is not None:
                    rescue_terms.extend(
                        term.model_copy(update={"accepted": False}) for term in rescued.terms
                    )
                    rescue_relations.extend(rescued.relations)
            candidate = OntologyCandidateChunk(
                terms=tuple(rescue_terms[:24]),
                relations=tuple(rescue_relations[:24]),
                warnings=candidate.warnings,
            )
        candidate_chunks.append(candidate)
    if len(labeled) > 1:
        for chunk in _chunks(labeled, prompts.coverage_chunk_size):
            user_prompt = _format_prompt(
                prompts.coverage_user_template,
                shots_json=_json([shot.model_dump(mode="json") for shot in chunk]),
            )
            prompt = f"{prompts.coverage_system}\n\n{user_prompt}"
            prompt_parts.append(prompt)
            coverage = await classify(prompt, OntologyCandidateChunk)
            if coverage is not None and (coverage.terms or coverage.relations):
                candidate_chunks.append(
                    coverage.model_copy(
                        update={
                            "terms": tuple(
                                term.model_copy(update={"accepted": False})
                                for term in coverage.terms
                            )
                        }
                    )
                )

    compile_prompt = f"{prompts.compile_system}\n\n" + _format_prompt(
        prompts.compile_user_template,
        candidates_json=_json(
            [
                candidate.model_dump(mode="json")
                for candidate in _candidate_chunks_for_compile(candidate_chunks)
            ]
        ),
    )
    prompt_parts.append(compile_prompt)
    compile_failed = False
    try:
        ontology = await classify(compile_prompt, CompiledOntology)
    except Exception as exc:
        compile_failed = True
        ontology = CompiledOntology(
            summary="Candidate-only ontology projection after compile pass failed.",
            warnings=(f"compile_failed:{type(exc).__name__}",),
        )
    if ontology is None:
        compile_failed = True
        ontology = CompiledOntology(
            summary="Candidate-only ontology projection after empty compile pass.",
            warnings=("compile_empty",),
        )
    ontology = sanitize_compiled_ontology(ontology)

    if prompts.critique_enabled and not compile_failed:
        critique_prompt = f"{prompts.critique_system}\n\n" + _format_prompt(
            prompts.critique_user_template,
            ontology_json=_json(ontology),
        )
        prompt_parts.append(critique_prompt)
        critiqued = await classify(critique_prompt, CompiledOntology)
        if critiqued is not None:
            ontology = sanitize_compiled_ontology(critiqued)

    if len(labeled) > 1 and any(chunk.terms for chunk in candidate_chunks):
        ontology = ontology.model_copy(update={"terms": ()})

    if _has_negative_shots(labeled) and not _has_accepted_negative_projection(ontology):
        ontology = sanitize_compiled_ontology(
            _restore_negative_projection_from_candidates(ontology, candidate_chunks)
        )
    ontology = sanitize_compiled_ontology(
        _restore_projection_from_candidates(ontology, candidate_chunks, labeled)
    )
    if _has_negative_shots(labeled) and not _has_accepted_negative_projection(ontology):
        msg = "compiled ontology has negative shots but no accepted negative projection"
        raise RuntimeError(msg)

    materialized, term_stats = materialize_compiled_ontology(ontology)
    prompt_hash = hashlib.sha256("\n\n---\n\n".join(prompt_parts).encode("utf-8")).hexdigest()
    model = str(getattr(llm, "model_id", None) or getattr(llm, "model", "unknown"))
    return OntologyCompilationResult(
        ontology=ontology,
        materialized=materialized,
        term_stats=term_stats,
        prompt_hash=prompt_hash,
        model=model,
        candidate_chunks=tuple(candidate_chunks),
    )
