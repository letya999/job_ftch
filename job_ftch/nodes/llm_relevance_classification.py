"""LLM relevance classification node (LLM point ② per ADR-019).

Resolves borderline relevance decisions for items whose embedding similarity
falls in (low_threshold, high_threshold) per ``MultiProfileMatchNode``.

Architecture role: a thin ``Stage[JobRecord, JobRecord]`` that only acts on
items flagged via ``metadata["needs_llm_review"] == True``. Cost-controlled
via ``max_per_run``. Caches results in ``Store.set_run_state`` keyed by
``source_record_id:profile_id``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.application.run_budget import AsyncCallBudget, ScopedCircuitBreaker
from job_ftch.domain import (
    AssessedJob,
    ClaimKind,
    EvidenceAtom,
    EvidencePolarity,
    EvidenceProvenance,
    JobRecord,
    RelevanceClassification,
    RelevanceEvidenceClassification,
    SearchProfile,
    aggregate_bundle,
    source_identity_for_parts,
)

if TYPE_CHECKING:
    from job_ftch.application.contracts import LLMProvider, Store


logger = structlog.get_logger(__name__)

_MAX_EXAMPLES_PER_LABEL = 6
_MAX_EXAMPLE_CHARS = 800
_COMPACT_CLASSIFICATION_MODE = "compact_evidence"
_LEGACY_CLASSIFICATION_MODE = "legacy_decision"
_CACHE_SCHEMA_VERSION = "relevance-v7"


def _append_llm_atom(
    item: JobRecord,
    metadata: dict[str, Any],
    *,
    profile_id: str,
    decision: str,
    confidence: object,
    evidence_ids: tuple[int, ...] = (),
    jobness: str | None = None,
    jobness_evidence_ids: tuple[int, ...] = (),
    producer_version: str = "llm-relevance-v2",
) -> None:
    """Materialize compact LLM facts as typed policy evidence."""
    identity = source_identity_for_parts(
        source_kind=item.source_kind,
        source_name=item.source_name,
        metadata=item.metadata,
    )
    normalized_confidence = (
        min(1.0, max(0.0, float(confidence))) if isinstance(confidence, (int, float)) else None
    )

    # Jobness is a distinct claim from profile relevance.  In compact mode it
    # is explicit structured output, but it still needs a cited posting span
    # before it can enter terminal evidence aggregation.
    if jobness in {"yes", "no"} and jobness_evidence_ids and normalized_confidence is not None:
        jobness_ref = "llm:jobness_evidence:" + ",".join(
            str(value) for value in jobness_evidence_ids
        )
        jobness_digest = hashlib.sha256(
            f"{jobness}|{normalized_confidence:.6f}|{jobness_ref}".encode()
        ).hexdigest()[:16]
        metadata.setdefault("evidence_atoms", []).append(
            EvidenceAtom(
                evidence_id=f"{item.raw_item_id}:llm-jobness:{jobness_digest}",
                claim=ClaimKind.IS_JOB,
                subject="vacancy",
                polarity=(
                    EvidencePolarity.SUPPORTS if jobness == "yes" else EvidencePolarity.CONTRADICTS
                ),
                strength=normalized_confidence,
                reliability=0.85,
                provenance=EvidenceProvenance.LLM,
                producer="llm_relevance",
                producer_version=producer_version,
                source_family=identity.family,
                observation_kind=identity.observation_kind,
                transport=identity.transport,
                independence_key=f"{item.raw_item_id}:llm-jobness",
                observation_id=item.raw_item_id,
                candidate_id=str(metadata.get("candidate_span_id") or item.raw_item_id),
                evidence_ref=jobness_ref,
            ).model_dump(mode="json")
        )

    if decision not in {"accept", "reject", "review"} or not isinstance(confidence, (int, float)):
        return
    assert normalized_confidence is not None
    evidence_ref = (
        "llm:relevance_review" if decision == "review" else "llm:relevance_classification"
    )
    if evidence_ids:
        evidence_ref = "llm:relevance_evidence:" + ",".join(str(value) for value in evidence_ids)
    digest = hashlib.sha256(
        f"{profile_id}|{decision}|{normalized_confidence:.6f}|{evidence_ref}".encode()
    ).hexdigest()[:16]
    metadata.setdefault("evidence_atoms", []).append(
        EvidenceAtom(
            evidence_id=f"{item.raw_item_id}:llm-relevance:{digest}",
            claim=ClaimKind.PROFILE_RELEVANCE,
            subject="profile_relevance",
            profile_id=profile_id,
            polarity=(
                EvidencePolarity.SUPPORTS
                if decision == "accept"
                else EvidencePolarity.CONTRADICTS
                if decision == "reject"
                else EvidencePolarity.UNKNOWN
            ),
            strength=normalized_confidence,
            reliability=0.85,
            provenance=EvidenceProvenance.LLM,
            producer="llm_relevance",
            producer_version=producer_version,
            source_family=identity.family,
            observation_kind=identity.observation_kind,
            transport=identity.transport,
            independence_key=f"{item.raw_item_id}:llm-relevance:{profile_id}",
            observation_id=item.raw_item_id,
            candidate_id=str(metadata.get("candidate_span_id") or item.raw_item_id),
            evidence_ref=evidence_ref,
        ).model_dump(mode="json")
    )


def _clean_snippet(value: object, *, max_chars: int) -> str:
    return " ".join(str(value).split())[:max_chars]


def _compact_evidence_snippets(item: JobRecord) -> tuple[str, ...]:
    """Build a bounded, numbered vacancy card from stated responsibilities."""
    snippets: list[str] = []
    if title := _clean_snippet(item.title, max_chars=240):
        snippets.append(f"title: {title}")
    for value in (item.role_family, item.role_track):
        if cleaned := _clean_snippet(value, max_chars=180):
            snippets.append(f"role: {cleaned}")
    for value in item.responsibilities[:6]:
        if cleaned := _clean_snippet(value, max_chars=320):
            snippets.append(f"responsibility: {cleaned}")
    for value in item.requirements_must[:3]:
        if cleaned := _clean_snippet(value, max_chars=240):
            snippets.append(f"requirement: {cleaned}")
    if len(snippets) <= 3:
        fallback = item.metadata.get("original_posting_text") or item.description
        if cleaned := _clean_snippet(fallback, max_chars=1800):
            snippets.append(f"posting: {cleaned}")
    return tuple(snippets[:16])


def _build_compact_evidence_prompt(
    item: JobRecord,
    profile: SearchProfile,
    *,
    system_prompt_override: str | None = None,
) -> str:
    profile_lines = []
    if profile.profile_description:
        profile_lines.append(f"Profile: {profile.profile_description[:500]}")
    if profile.target_roles:
        profile_lines.append(f"Target roles: {', '.join(profile.target_roles)[:500]}")
    if profile.anti_preferences:
        profile_lines.append(f"Explicit exclusions: {', '.join(profile.anti_preferences)[:700]}")
    if system_prompt_override:
        profile_lines.append(f"Compiled decision brief:\n{system_prompt_override[:1800]}")
    evidence = "\n".join(
        f"[{index}] {snippet}"
        for index, snippet in enumerate(_compact_evidence_snippets(item), start=1)
    )
    return (
        "## PROFILE DECISION CONTRACT\n"
        + "\n".join(profile_lines)
        + "\n\n## VACANCY EVIDENCE\n"
        + evidence
        + "\n\n## TASK\n"
        + _DOMAIN_NEUTRAL_TASK_RULES
    )


# Domain-specific guidance belongs in the compiled decision brief, which is derived from
# the profile's own shots. Naming one field's technologies here would bias every other
# profile that reuses this node, so these rules describe only how to read the evidence.
_DOMAIN_NEUTRAL_TASK_RULES = (
    "Return only the requested structured fields. Classify the actual role and what the "
    "person will personally do. The employer's industry, product, team name, or tooling is "
    "context only and does not by itself prove a target role. Compare the title and the "
    "responsibilities with the profile's stated target roles: a role is target when it is "
    "one of those roles or performs their core work, whichever the profile describes. "
    "Stated responsibilities outrank the title when they conflict. If responsibilities are "
    "missing, an unambiguous exact or close target title is sufficient positive evidence and "
    "must not become adjacent merely because the post is short. Use adjacent only for a "
    "different neighboring role that does not own the profile's target work. Intern, junior, "
    "middle, senior, and lead levels remain the same target role family unless the profile "
    "explicitly excludes that seniority. Someone who merely uses a tool associated with the "
    "target work is adjacent unless operating that tool is itself a target role. Product "
    "news, articles, announcements, portfolios, course pages, agency or freelance service "
    "offers, and recommendation pages are not hiring posts unless they explicitly recruit a "
    "person for a role. Use only the "
    "evidence IDs listed above. Positive IDs may prove a target role by title and/or duties; "
    "negative IDs must prove a non-target role or contradictory responsibilities."
)


def _is_ambiguity_candidate(
    result: RelevanceEvidenceClassification,
    decision: str,
) -> bool:
    """Select only the compact-judge disagreement zone for a second pass."""
    return result.is_job == "yes" and (
        (result.role_relation == "adjacent" and result.responsibility_fit == "support")
        or (
            decision != "accept"
            and result.role_relation == "target"
            and result.responsibility_fit == "unknown"
        )
    )


def _full_vacancy_evidence(item: JobRecord) -> tuple[str, ...]:
    posting = item.metadata.get("original_posting_text") or item.description or ""
    snippets = [f"title: {_clean_snippet(item.title, max_chars=240)}"]
    if cleaned := _clean_snippet(posting, max_chars=6000):
        snippets.append(f"posting: {cleaned}")
    return tuple(snippet for snippet in snippets if not snippet.endswith(": "))


def _build_ambiguity_resolution_prompt(
    item: JobRecord,
    profile: SearchProfile,
    *,
    system_prompt_override: str | None = None,
) -> str:
    """Ask for fresh, cited facts using the complete vacancy only when needed."""
    profile_lines = [f"Target roles: {', '.join(profile.target_roles)[:500]}"]
    if profile.profile_description:
        profile_lines.append(f"Profile: {profile.profile_description[:700]}")
    if profile.anti_preferences:
        profile_lines.append(f"Explicit exclusions: {', '.join(profile.anti_preferences)[:700]}")
    if system_prompt_override:
        # The second pass decides the same boundary as the first, so it needs the same
        # profile-derived brief rather than a hardcoded restatement of one domain.
        profile_lines.append(f"Compiled decision brief:\n{system_prompt_override[:1800]}")
    evidence = "\n".join(
        f"[{index}] {snippet}"
        for index, snippet in enumerate(_full_vacancy_evidence(item), start=1)
    )
    return (
        "## PROFILE DECISION CONTRACT\n"
        + "\n".join(profile_lines)
        + "\n\n## FULL VACANCY EVIDENCE\n"
        + evidence
        + "\n\n## TASK\n"
        "The compact review was ambiguous. Re-read the complete vacancy and return only the "
        "structured evidence fields. Decide whether the person personally owns the profile's "
        "target work, not whether the employer operates in that field. Mark target/support only "
        "when the cited title or duties show one of the profile's target roles or its stated core "
        "outcomes. A neighboring role remains adjacent unless its stated duties clearly own those "
        "outcomes. Cite only the numbered evidence above."
    )


def _build_precision_confirmation_prompt(
    item: JobRecord,
    profile: SearchProfile,
    *,
    system_prompt_override: str | None = None,
) -> str:
    profile_lines = [f"Target roles: {', '.join(profile.target_roles)[:500]}"]
    if profile.profile_description:
        profile_lines.append(f"Profile: {profile.profile_description[:700]}")
    if profile.anti_preferences:
        profile_lines.append(f"Explicit exclusions: {', '.join(profile.anti_preferences)[:700]}")
    if system_prompt_override:
        profile_lines.append(f"Compiled decision brief:\n{system_prompt_override[:1800]}")
    evidence = "\n".join(
        f"[{index}] {snippet}"
        for index, snippet in enumerate(_full_vacancy_evidence(item), start=1)
    )
    return (
        "## PROFILE DECISION CONTRACT\n"
        + "\n".join(profile_lines)
        + "\n\n## FULL VACANCY EVIDENCE\n"
        + evidence
        + "\n\n## TASK\n"
        "The compact review accepted this vacancy while also citing negative or conflicting "
        "evidence. Re-check precision using the complete vacancy. Return target/support only "
        "when the person personally owns the profile's target work. If the core work is a "
        "neighboring role, infrastructure, research, model training, analytics, or another "
        "non-target responsibility, return adjacent or other with negative evidence IDs. Cite "
        "only the numbered evidence above."
    )


def _compact_decision(
    result: RelevanceEvidenceClassification,
    item: JobRecord | None = None,
    profile: SearchProfile | None = None,
) -> tuple[str, tuple[int, ...]]:
    if result.is_job == "no":
        ids = result.negative_evidence_ids or result.positive_evidence_ids
        return ("reject", ids) if ids else ("review", ())
    exact_target_title = (
        item is not None
        and profile is not None
        and result.is_job == "yes"
        and bool(result.positive_evidence_ids)
        and _title_matches_target_role(item.title, profile.target_roles)
    )
    clean_adjacent_support = (
        result.is_job == "yes"
        and result.role_relation == "adjacent"
        and result.responsibility_fit == "support"
        and bool(result.positive_evidence_ids)
        and not result.negative_evidence_ids
    )
    clean_adjacent_unknown = (
        result.is_job == "yes"
        and result.role_relation == "adjacent"
        and result.responsibility_fit == "unknown"
        and bool(result.positive_evidence_ids)
        and not result.negative_evidence_ids
        and profile is not None
        and _has_profile_specific_role_signal(item, profile.target_roles)
    )
    if (
        result.role_relation in {"adjacent", "other"}
        and not exact_target_title
        and not clean_adjacent_support
        and not clean_adjacent_unknown
    ) or result.responsibility_fit == "contradict":
        return (
            ("reject", result.negative_evidence_ids)
            if result.negative_evidence_ids
            else ("review", ())
        )
    # Missing duties are sufficient only for an explicit exact/close target
    # title.  A generic hiring-role signal (for example just "Product
    # Manager") proves jobness, not profile relevance.  Treating it as duty
    # support caused ordinary product/project vacancies to become ACCEPT when
    # the model optimistically returned role_relation=target.
    responsibility_supported = result.responsibility_fit == "support" or (
        result.responsibility_fit == "unknown" and exact_target_title
    )
    if result.is_job != "yes" or not result.positive_evidence_ids:
        return "review", ()
    if result.role_relation == "target" and responsibility_supported:
        return "accept", result.positive_evidence_ids
    if clean_adjacent_support:
        return "accept", result.positive_evidence_ids
    if clean_adjacent_unknown:
        return "accept", result.positive_evidence_ids
    return "review", ()


_ROLE_TOKEN_ALIASES = {"ml": "ai", "ии": "ai"}
_ROLE_LEVEL_TOKENS = {
    "junior",
    "middle",
    "mid",
    "senior",
    "lead",
    "head",
    "intern",
    "стажер",
    "стажёр",
    "младший",
    "миддл",
    "сеньор",
    "старший",
}
_ROLE_STOP_TOKENS = {"and", "of", "the", "в", "для", "и", "на", "по"}
_ROLE_GENERIC_TOKENS = {
    "analyst",
    "architect",
    "developer",
    "engineer",
    "head",
    "lead",
    "manager",
    "product",
    "project",
    "specialist",
}
_ROLE_PREFIX_ALIASES = (
    ("автоматиз", "automation"),
    ("архитект", "architect"),
    ("инженер", "engineer"),
    ("менедж", "manager"),
    ("продакт", "product"),
    ("продукт", "product"),
    ("проект", "project"),
    ("разработ", "developer"),
    ("руковод", "head"),
)


def _canonical_role_token(token: str) -> str:
    aliased = _ROLE_TOKEN_ALIASES.get(token, token)
    for prefix, canonical in _ROLE_PREFIX_ALIASES:
        if aliased.startswith(prefix):
            return canonical
    return aliased


def _role_tokens(value: str | None) -> set[str]:
    tokens = re.findall(r"[^\W_]+", (value or "").casefold())
    return {
        _canonical_role_token(token)
        for token in tokens
        if token not in _ROLE_LEVEL_TOKENS and token not in _ROLE_STOP_TOKENS
    }


def _title_matches_target_role(title: str | None, target_roles: tuple[str, ...]) -> bool:
    """Match an explicit target title independent of word order and seniority."""
    title_tokens = _role_tokens(title)
    return bool(title_tokens) and any(
        bool(target_tokens := _role_tokens(role)) and target_tokens <= title_tokens
        for role in target_roles
    )


def _has_profile_specific_role_signal(
    item: JobRecord | None, target_roles: tuple[str, ...]
) -> bool:
    if item is None or not target_roles:
        return False
    target_specific = {
        token
        for role in target_roles
        for token in _role_tokens(role)
        if token not in _ROLE_GENERIC_TOKENS
    }
    if not target_specific:
        return False
    text_tokens = _role_tokens(
        "\n".join(
            part
            for part in (
                item.title,
                item.title_normalized,
                item.role_family,
                item.domain,
                item.description,
                str(item.metadata.get("original_posting_text") or ""),
            )
            if part
        )
    )
    return bool(target_specific & text_tokens)


def _balanced_examples(
    primary: tuple[str, ...],
    secondary: tuple[str, ...],
    *,
    limit: int = _MAX_EXAMPLES_PER_LABEL,
) -> tuple[str, ...]:
    """Keep curated vacancy shots visible even when resume shots are numerous."""
    selected: list[str] = []
    seen: set[str] = set()
    for bucket in (primary, secondary):
        for text in bucket:
            normalized = text.strip()
            if not normalized or normalized in seen:
                continue
            selected.append(normalized)
            seen.add(normalized)
            if len(selected) >= limit:
                return tuple(selected)
    return tuple(selected)


def _build_relevance_prompt(
    item: JobRecord,
    profile: SearchProfile,
    positive_jobs: tuple[str, ...],
    negative_jobs: tuple[str, ...],
    system_prompt_override: str | None = None,
    include_raw_examples: bool = True,
) -> str:
    parts: list[str] = []

    parts.append("## CANDIDATE PROFILE")
    if profile.profile_description:
        parts.append(f"Description: {profile.profile_description}")
    if profile.anti_preferences:
        parts.append(f"NOT interested in: {', '.join(profile.anti_preferences)}")

    if system_prompt_override:
        parts.append("## DECISION RULES\n")
        parts.append(system_prompt_override)

    if include_raw_examples and positive_jobs:
        parts.append("## POSITIVE EXAMPLES (candidate WANTS these):")
        for i, ex in enumerate(positive_jobs[:_MAX_EXAMPLES_PER_LABEL], start=1):
            parts.append(f"{i}. {ex[:_MAX_EXAMPLE_CHARS]}")

    if include_raw_examples and negative_jobs:
        parts.append("## NEGATIVE EXAMPLES (candidate REJECTS these):")
        for i, ex in enumerate(negative_jobs[:_MAX_EXAMPLES_PER_LABEL], start=1):
            parts.append(f"{i}. {ex[:_MAX_EXAMPLE_CHARS]}")

    parts.append("## JOB TO CLASSIFY")
    parts.append(
        f"Title: {item.title or '(no title)'}\n"
        f"Company: {item.company or '(unknown)'}\n"
        f"Description: {(item.description or '')[:3000]}"
    )
    parts.append(
        "\nBased ONLY on the rules and examples above, is the CORE responsibility of this role building or "
        "integrating LLM/AI into products/tools/automations? Judge by responsibilities, not by title tokens "
        "or team names. Answer exactly 'accept' or 'reject'."
    )
    return "\n".join(parts)


def _profile_prompt_digest(
    profile: SearchProfile,
    *,
    positive_jobs: tuple[str, ...],
    negative_jobs: tuple[str, ...],
    effective_prompt: str | None,
    classification_mode: str = _LEGACY_CLASSIFICATION_MODE,
) -> str:
    payload = {
        "version": 3,
        "classification_mode": classification_mode,
        "profile_description": profile.profile_description,
        "target_roles": list(profile.target_roles),
        "anti_preferences": list(profile.anti_preferences),
        "positive_jobs": list(positive_jobs),
        "negative_jobs": list(negative_jobs),
        "prompt": effective_prompt or "",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


class LLMRelevanceClassificationNode:
    """Same-type Stage: enriches JobRecord with ``_llm_relevance`` for borderline cases."""

    def __init__(
        self,
        llm: LLMProvider,
        store: Store,
        catalog: object,  # ProfileCatalog, kept duck-typed to avoid application-domain cycle
        *,
        low_threshold: float = 0.2,
        high_threshold: float = 0.5,
        max_per_run: int = 500,
        budget: AsyncCallBudget | None = None,
        system_prompt_override: str | None = None,
        relevance_prompts: dict[str, str | None] | None = None,
        circuit_breaker: ScopedCircuitBreaker | None = None,
        tenant_id: str = "default",
        graph_hash: str = "legacy",
    ) -> None:
        self._llm = llm
        self._store = store
        self._catalog = catalog
        self._low = low_threshold
        self._high = high_threshold
        self._max = max_per_run
        self._budget = budget
        self._system_prompt_override = system_prompt_override
        self._relevance_prompts = relevance_prompts or {}
        self._circuit_breaker = circuit_breaker or ScopedCircuitBreaker()
        self._tenant_id = tenant_id
        self._graph_hash = graph_hash
        self._graph_prompt_name: str | None = None
        self._call_policy = "threshold"
        self._classification_mode = _LEGACY_CLASSIFICATION_MODE
        self._count = 0
        self._max_ambiguity_resolutions = 0
        self._ambiguity_resolution_count = 0
        self._max_precision_confirmations = 0
        self._precision_confirmation_count = 0
        self.stats = {
            "llm_relevance_calls": 0,
            "llm_relevance_cache_hits": 0,
            "llm_relevance_fallback": 0,
            "llm_relevance_failures": 0,
            "llm_ambiguity_resolution_calls": 0,
            "llm_ambiguity_resolution_fallback": 0,
            "llm_ambiguity_resolution_failures": 0,
            "llm_precision_confirmation_calls": 0,
            "llm_precision_confirmation_fallback": 0,
            "llm_precision_confirmation_failures": 0,
        }

    def configure_graph_params(self, params: dict[str, object]) -> None:
        if "low_threshold" in params:
            self._low = float(str(params["low_threshold"]))
        if "high_threshold" in params:
            self._high = float(str(params["high_threshold"]))
        if "max_per_run" in params:
            self._max = int(str(params["max_per_run"]))
        if "max_ambiguity_resolutions" in params:
            self._max_ambiguity_resolutions = int(str(params["max_ambiguity_resolutions"]))
        if "max_precision_confirmations" in params:
            self._max_precision_confirmations = int(str(params["max_precision_confirmations"]))
        if "prompt" in params:
            self._graph_prompt_name = str(params["prompt"])
        if "call_policy" in params:
            self._call_policy = str(params["call_policy"])
        if "classification_mode" in params:
            mode = str(params["classification_mode"])
            if mode not in {_LEGACY_CLASSIFICATION_MODE, _COMPACT_CLASSIFICATION_MODE}:
                raise ValueError(f"unsupported relevance classification_mode: {mode}")
            self._classification_mode = mode

    async def _resolve_ambiguity(
        self,
        item: JobRecord,
        profile: SearchProfile,
        *,
        system_prompt_override: str | None = None,
    ) -> RelevanceEvidenceClassification | None:
        if self._ambiguity_resolution_count >= self._max_ambiguity_resolutions:
            self.stats["llm_ambiguity_resolution_fallback"] += 1
            return None
        if self._budget is not None and not await self._budget.try_acquire():
            self.stats["llm_ambiguity_resolution_fallback"] += 1
            return None
        self._ambiguity_resolution_count += 1
        self.stats["llm_ambiguity_resolution_calls"] += 1
        try:
            result = await self._llm.classify(
                _build_ambiguity_resolution_prompt(
                    item, profile, system_prompt_override=system_prompt_override
                ),
                RelevanceEvidenceClassification,
            )
        except Exception as exc:
            self.stats["llm_ambiguity_resolution_failures"] += 1
            logger.warning("llm_ambiguity_resolution_failed", error=str(exc))
            return None
        return result if isinstance(result, RelevanceEvidenceClassification) else None

    async def _confirm_precision(
        self,
        item: JobRecord,
        profile: SearchProfile,
        *,
        system_prompt_override: str | None = None,
    ) -> RelevanceEvidenceClassification | None:
        if self._precision_confirmation_count >= self._max_precision_confirmations:
            self.stats["llm_precision_confirmation_fallback"] += 1
            return None
        if self._budget is not None and not await self._budget.try_acquire():
            self.stats["llm_precision_confirmation_fallback"] += 1
            return None
        self._precision_confirmation_count += 1
        self.stats["llm_precision_confirmation_calls"] += 1
        try:
            result = await self._llm.classify(
                _build_precision_confirmation_prompt(
                    item, profile, system_prompt_override=system_prompt_override
                ),
                RelevanceEvidenceClassification,
            )
        except Exception as exc:
            self.stats["llm_precision_confirmation_failures"] += 1
            logger.warning("llm_precision_confirmation_failed", error=str(exc))
            return None
        return result if isinstance(result, RelevanceEvidenceClassification) else None

    async def process(self, item: JobRecord) -> JobRecord | None:
        if (item.metadata or {}).get("_garbage_reason"):
            return item

        # Historical control uses a score window.  Ablations may instead ask
        # for an LLM decision for every surviving candidate, or only for the
        # disagreement zone produced by UncertaintyRouterNode.
        relevance = getattr(item, "relevance_score", None) or 0.0
        pfs = (item.metadata or {}).get("parallel_final_score") if item.metadata else None
        if self._call_policy == "uncertainty_only":
            if not (item.metadata or {}).get("needs_llm_review"):
                return item
        elif self._call_policy != "force_all":
            if pfs is not None:
                if pfs < self._low:
                    return item  # clearly below floor — skip without LLM call
                if pfs >= self._high:
                    return item
            elif not (self._low < relevance < self._high):
                return item

        if self._budget is not None:
            acquired = await self._budget.try_acquire()
            if not acquired:
                logger.info(
                    "llm_relevance_fallback_to_threshold",
                    reason="budget_exhausted",
                    relevance=relevance,
                )
                self.stats["llm_relevance_fallback"] += 1
                return self._mark_degraded(item, "budget_exhausted")
        elif self._count >= self._max:
            logger.info(
                "llm_relevance_fallback_to_threshold",
                reason="max_per_run_reached",
                relevance=relevance,
            )
            self.stats["llm_relevance_fallback"] += 1
            return self._mark_degraded(item, "max_per_run_reached")

        # Pick the profile with the highest parallel score for this item (most relevant context).
        # Fallback to profiles[0] when scores are unavailable (eval / CLI runs).
        profile = None
        if self._catalog.profiles:  # type: ignore
            profile_scores = getattr(item, "profile_scores", None) or []
            if profile_scores:
                best_ps = max(profile_scores, key=lambda s: s.final_score)
                profile_map = {p.profile_id: p for p in self._catalog.profiles}  # type: ignore
                profile = profile_map.get(best_ps.profile_id, self._catalog.profiles[0])  # type: ignore
            else:
                profile = self._catalog.profiles[0]  # type: ignore
        if profile is None:
            return item

        profile_prompt = self._relevance_prompts.get(profile.profile_id)
        if self._graph_prompt_name == "none":
            effective_prompt = None
        elif self._graph_prompt_name and self._graph_prompt_name.startswith("profile:"):
            effective_prompt = self._relevance_prompts.get(
                self._graph_prompt_name.removeprefix("profile:")
            )
        else:
            effective_prompt = (
                profile_prompt if profile_prompt is not None else self._system_prompt_override
            )

        # BR-2 / BR-3: both buckets must be visible under a hard prompt cap.
        # Vacancy shots are the user's explicit job choices, so they lead the
        # prompt; resume shots provide supporting context after them.
        pos_jobs = _balanced_examples(
            profile.positive_job_example_texts,
            profile.positive_example_texts,
        )
        neg_jobs = _balanced_examples(
            profile.negative_job_example_texts,
            profile.negative_example_texts,
        )
        if self._classification_mode == _COMPACT_CLASSIFICATION_MODE:
            prompt = _build_compact_evidence_prompt(
                item,
                profile,
                system_prompt_override=effective_prompt,
            )
            response_schema: type[Any] = RelevanceEvidenceClassification
        else:
            prompt = _build_relevance_prompt(
                item,
                profile,
                positive_jobs=pos_jobs,
                negative_jobs=neg_jobs,
                system_prompt_override=effective_prompt,
                # A compiled profile brief already contains the versioned shot
                # synthesis. Repeating raw shots per vacancy defeats its token
                # budget and makes every decision prompt depend on large text.
                include_raw_examples=profile_prompt is None,
            )
            response_schema = RelevanceClassification

        # The cache is a reusable model-result cache, not a source-ID cache.
        # Hash the actual model input plus every runtime choice that can change
        # its semantics.  This prevents stale decisions after an item edit,
        # graph promotion, prompt/schema/model change, or parameter tuning.
        cache_payload = {
            "cache_schema": _CACHE_SCHEMA_VERSION,
            "app_revision": os.getenv("JOB_FTCH_APP_REVISION", "dev"),
            "graph_hash": self._graph_hash,
            "node": {
                "call_policy": self._call_policy,
                "classification_mode": self._classification_mode,
                "low_threshold": self._low,
                "high_threshold": self._high,
                "max_per_run": self._max,
                "max_ambiguity_resolutions": self._max_ambiguity_resolutions,
                "max_precision_confirmations": self._max_precision_confirmations,
                "prompt_name": self._graph_prompt_name,
            },
            "profile_id": profile.profile_id,
            "provider": type(self._llm).__qualname__,
            "model": str(getattr(self._llm, "model_id", type(self._llm).__name__)),
            "response_schema": (f"{response_schema.__module__}.{response_schema.__qualname__}"),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        }
        cache_digest = hashlib.sha256(
            json.dumps(cache_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        cache_key = f"{_CACHE_SCHEMA_VERSION}:{cache_digest}"

        cached = await self._store.get_run_state(cache_key)
        if cached:
            self.stats["llm_relevance_cache_hits"] += 1
            return self._apply_cached(item, cached, profile)

        try:
            circuit_key = self._circuit_key(item)
            if not await self._circuit_breaker.allow(**circuit_key):
                return self._apply_provider_unavailable_fallback(item, error="circuit_open")
            self.stats["llm_relevance_calls"] += 1
            result: Any = await self._llm.classify(prompt, response_schema)
        except Exception as exc:
            self.stats["llm_relevance_failures"] += 1
            logger.warning("llm_relevance_failed", error=str(exc))
            await self._circuit_breaker.record_failure(**self._circuit_key(item))
            return self._apply_provider_unavailable_fallback(item, error=str(exc))

        await self._circuit_breaker.record_success(**self._circuit_key(item))

        if result is None:
            return item

        if self._budget is None:
            self._count += 1

        ambiguity_resolution: RelevanceEvidenceClassification | None = None
        precision_confirmation: RelevanceEvidenceClassification | None = None
        if isinstance(result, RelevanceEvidenceClassification):
            decision, evidence_ids = _compact_decision(result, item, profile)
            if self._max_ambiguity_resolutions > 0 and _is_ambiguity_candidate(result, decision):
                ambiguity_resolution = await self._resolve_ambiguity(
                    item, profile, system_prompt_override=effective_prompt
                )
                if ambiguity_resolution is not None:
                    decision, evidence_ids = _compact_decision(ambiguity_resolution, item, profile)
            compact_for_confirmation = ambiguity_resolution or result
            if (
                decision == "accept"
                and self._max_precision_confirmations > 0
                and compact_for_confirmation.negative_evidence_ids
                and not _title_matches_target_role(item.title, profile.target_roles)
            ):
                precision_confirmation = await self._confirm_precision(
                    item, profile, system_prompt_override=effective_prompt
                )
                if precision_confirmation is not None:
                    decision, evidence_ids = _compact_decision(
                        precision_confirmation, item, profile
                    )
            confidence = 1.0
            reasoning = None
            positive_aspects: list[str] = []
            mismatched_aspects: list[str] = []
        else:
            decision = result.decision
            evidence_ids = ()
            confidence = result.confidence
            reasoning = result.reasoning
            positive_aspects = list(result.matched_positive_aspects)
            mismatched_aspects = list(result.mismatched_aspects)
        # On accept: raise score to at least the judge confidence.
        # On reject: do NOT raise the score — rejection must not push an item past routing thresholds.
        new_score = (
            relevance
            if self._classification_mode == _COMPACT_CLASSIFICATION_MODE
            else max(relevance, confidence)
            if decision == "accept"
            else relevance
        )

        await self._store.set_run_state(
            cache_key,
            json.dumps(
                {
                    "decision": decision,
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "matched_positive_aspects": positive_aspects,
                    "mismatched_aspects": mismatched_aspects,
                    "decision_enum": decision,
                    "score": new_score,
                    "classification_mode": self._classification_mode,
                    "evidence_ids": list(evidence_ids),
                    "compact_evidence": (
                        result.model_dump(mode="json")
                        if isinstance(result, RelevanceEvidenceClassification)
                        else None
                    ),
                    "ambiguity_resolution": (
                        ambiguity_resolution.model_dump(mode="json")
                        if ambiguity_resolution is not None
                        else None
                    ),
                    "precision_confirmation": (
                        precision_confirmation.model_dump(mode="json")
                        if precision_confirmation is not None
                        else None
                    ),
                }
            ),
        )

        meta = dict(item.metadata or {})
        if isinstance(result, RelevanceEvidenceClassification):
            meta["_llm_relevance"] = {
                "decision": decision,
                "primary": result.model_dump(mode="json"),
                "ambiguity_resolution": (
                    ambiguity_resolution.model_dump(mode="json")
                    if ambiguity_resolution is not None
                    else None
                ),
                "precision_confirmation": (
                    precision_confirmation.model_dump(mode="json")
                    if precision_confirmation is not None
                    else None
                ),
                "prompt_variant": self._graph_prompt_name or "profile_default",
                "classification_mode": self._classification_mode,
            }
        else:
            meta["_llm_relevance"] = {
                "decision": decision,
                "confidence": confidence,
                "reasoning": reasoning,
                "prompt_variant": self._graph_prompt_name or "profile_default",
            }
        compact_result = precision_confirmation or ambiguity_resolution or result
        _append_llm_atom(
            item,
            meta,
            profile_id=profile.profile_id,
            decision=decision,
            confidence=confidence,
            evidence_ids=evidence_ids,
            jobness=(
                compact_result.is_job
                if isinstance(compact_result, RelevanceEvidenceClassification)
                else None
            ),
            jobness_evidence_ids=(
                compact_result.positive_evidence_ids
                if isinstance(compact_result, RelevanceEvidenceClassification)
                and compact_result.is_job == "yes"
                else (
                    compact_result.negative_evidence_ids or compact_result.positive_evidence_ids
                    if isinstance(compact_result, RelevanceEvidenceClassification)
                    and compact_result.is_job == "no"
                    else ()
                )
            ),
            producer_version=(
                "llm-relevance-evidence-v1"
                if self._classification_mode == _COMPACT_CLASSIFICATION_MODE
                else "llm-relevance-v2"
            ),
        )

        return item.model_copy(
            update={
                "relevance_score": new_score,
                "metadata": meta,
            }
        )

    def _apply_cached(
        self,
        item: JobRecord,
        cached: str,
        profile: SearchProfile | None = None,
    ) -> JobRecord:
        try:
            data = json.loads(cached)
        except json.JSONDecodeError:
            return item
        decision = data.get("decision", "")
        confidence = data.get("confidence")
        reasoning = data.get("reasoning")
        # A cached review remains unknown evidence. It cannot become a reject
        # merely because a provider/budget path was unavailable.
        meta = dict(item.metadata or {})
        compact_raw = data.get("compact_evidence")
        compact: RelevanceEvidenceClassification | None = None
        resolution_raw = data.get("ambiguity_resolution")
        ambiguity_resolution: RelevanceEvidenceClassification | None = None
        confirmation_raw = data.get("precision_confirmation")
        precision_confirmation: RelevanceEvidenceClassification | None = None
        if isinstance(compact_raw, dict):
            try:
                compact = RelevanceEvidenceClassification.model_validate(compact_raw)
            except ValueError:
                compact = None
        if isinstance(resolution_raw, dict):
            try:
                ambiguity_resolution = RelevanceEvidenceClassification.model_validate(
                    resolution_raw
                )
            except ValueError:
                ambiguity_resolution = None
        if isinstance(confirmation_raw, dict):
            try:
                precision_confirmation = RelevanceEvidenceClassification.model_validate(
                    confirmation_raw
                )
            except ValueError:
                precision_confirmation = None
        if compact is not None:
            decision, evidence_ids = _compact_decision(compact, item, profile)
            if ambiguity_resolution is not None:
                decision, evidence_ids = _compact_decision(ambiguity_resolution, item, profile)
            if precision_confirmation is not None:
                decision, evidence_ids = _compact_decision(precision_confirmation, item, profile)
            confidence = 1.0
            meta["_llm_relevance"] = {
                "decision": decision,
                "primary": compact.model_dump(mode="json"),
                "ambiguity_resolution": (
                    ambiguity_resolution.model_dump(mode="json")
                    if ambiguity_resolution is not None
                    else None
                ),
                "precision_confirmation": (
                    precision_confirmation.model_dump(mode="json")
                    if precision_confirmation is not None
                    else None
                ),
                "prompt_variant": self._graph_prompt_name or "profile_default",
                "classification_mode": _COMPACT_CLASSIFICATION_MODE,
            }
        else:
            evidence_ids = ()
            meta["_llm_relevance"] = {
                "decision": decision,
                "confidence": confidence,
                "reasoning": reasoning,
                "prompt_variant": self._graph_prompt_name or "profile_default",
            }
        if profile is not None:
            effective_compact = precision_confirmation or ambiguity_resolution or compact
            _append_llm_atom(
                item,
                meta,
                profile_id=profile.profile_id,
                decision=str(decision),
                confidence=confidence,
                evidence_ids=evidence_ids,
                jobness=effective_compact.is_job if effective_compact is not None else None,
                jobness_evidence_ids=(
                    effective_compact.positive_evidence_ids
                    if effective_compact is not None and effective_compact.is_job == "yes"
                    else (
                        effective_compact.negative_evidence_ids
                        or effective_compact.positive_evidence_ids
                        if effective_compact is not None and effective_compact.is_job == "no"
                        else ()
                    )
                ),
                producer_version=(
                    "llm-relevance-evidence-v1" if compact is not None else "llm-relevance-v2"
                ),
            )
        return item.model_copy(
            update={
                "relevance_score": data.get("score", item.relevance_score),
                "metadata": meta,
            }
        )

    def _apply_provider_unavailable_fallback(self, item: JobRecord, *, error: str) -> JobRecord:
        meta = dict(item.metadata or {})
        meta["_llm_relevance_error"] = error
        meta["llm_relevance_degradation"] = "provider_unavailable"
        meta["_llm_relevance"] = {
            "decision": "review",
            "reasoning": "provider_unavailable",
            "degraded": True,
        }
        self.stats["llm_relevance_fallback"] += 1
        return item.model_copy(
            update={
                "metadata": meta,
            }
        )

    @staticmethod
    def _mark_degraded(item: JobRecord, reason: str) -> JobRecord:
        return item.model_copy(
            update={
                "metadata": {**item.metadata, "llm_relevance_degradation": reason},
            }
        )

    def _circuit_key(self, item: JobRecord) -> dict[str, str]:
        return {
            "provider": getattr(self._llm, "model_id", type(self._llm).__name__),
            "stage": "llm_relevance",
            "tenant": str(item.metadata.get("tenant_id") or self._tenant_id),
        }


class LLMRelevanceEvidenceNode:
    """Typed graph adapter: enrich an ``AssessedJob`` with LLM evidence.

    The compatibility node above keeps the legacy ``JobRecord`` transport
    alive. New YAML v2 graphs use this adapter so the terminal DecisionNode
    receives one complete typed evidence bundle rather than metadata routing.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._classifier = LLMRelevanceClassificationNode(*args, **kwargs)
        self._audit_mode = False

    def enable_audit_mode(self) -> None:
        self._audit_mode = True

    @property
    def stats(self) -> dict[str, int]:
        return self._classifier.stats

    def configure_graph_params(self, params: dict[str, object]) -> None:
        self._classifier.configure_graph_params(params)

    async def process(self, item: AssessedJob) -> AssessedJob:
        if not isinstance(item, AssessedJob):
            raise TypeError("LLMRelevanceEvidenceNode requires AssessedJob")
        if self._audit_mode:
            return item
        record = await self._classifier.process(item.record)
        if record is None:
            return item.model_copy(
                update={"degradation_reasons": (*item.degradation_reasons, "llm_relevance_empty")}
            )
        serialized = record.metadata.get("evidence_atoms", ())
        atoms_by_id = {atom.evidence_id: atom for atom in item.evidence}
        for raw in serialized:
            if not isinstance(raw, dict):
                continue
            try:
                atom = EvidenceAtom.model_validate(raw)
            except ValueError:
                continue
            atoms_by_id[atom.evidence_id] = atom
        bundle = aggregate_bundle(
            tuple(atoms_by_id.values()),
            {},
            policy_version=item.policy_version,
        )
        degraded = list(item.degradation_reasons)
        if record.metadata.get("llm_relevance_degradation"):
            degraded.append("llm_relevance_unavailable")
        return item.model_copy(
            update={
                "record": record,
                "evidence": bundle.atoms,
                "assessments": bundle.assessments,
                "degradation_reasons": tuple(dict.fromkeys(degraded)),
            }
        )
