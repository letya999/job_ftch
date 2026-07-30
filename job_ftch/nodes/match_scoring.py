"""Multi-profile match scoring for normalized jobs."""

from __future__ import annotations

import re

from opentelemetry import trace

from job_ftch.domain import (
    JobRecord,
    MatchDecision,
    ProfileMatchScore,
    Seniority,
    SkillTag,
)
from job_ftch.domain.profile import ProfileCatalog, SearchProfile  # noqa: TC001

_SENIORITY_ORDER = {
    Seniority.INTERN: 0,
    Seniority.JUNIOR: 1,
    Seniority.MIDDLE: 2,
    Seniority.SENIOR: 3,
    Seniority.LEAD: 4,
    Seniority.STAFF: 5,
    Seniority.PRINCIPAL: 6,
    Seniority.HEAD: 7,
    Seniority.DIRECTOR: 8,
    Seniority.EXECUTIVE: 9,
    Seniority.UNKNOWN: -1,
}

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9+#-]+")

# Generic role-qualifier tokens that appear in almost every title and carry
# zero discriminating signal between roles (e.g. "DevOps Engineer" vs "AI Engineer"
# share "engineer" but the meaningful difference is "DevOps" vs "AI").
_ROLE_STOP_WORDS = frozenset(
    {
        "engineer",
        "developer",
        "manager",
        "specialist",
        "analyst",
        "lead",
        "senior",
        "junior",
        "head",
        "chief",
        "officer",
        "architect",
        "consultant",
        "expert",
        "professional",
        "associate",
        "intern",
        "staff",
        "principal",
    }
)


def _string_overlap_score(value: str | None, options: tuple[str, ...]) -> float:
    if value is None or not options:
        return 0.0
    lowered = value.casefold()
    value_tokens = {token.casefold() for token in _TOKEN_RE.findall(value)}
    best = 0.0
    for option in options:
        normalized = option.casefold().strip()
        if not normalized:
            continue
        if normalized in lowered:
            return 1.0
        option_tokens = [token.casefold() for token in _TOKEN_RE.findall(option)]
        if not option_tokens or not value_tokens:
            continue
        # Use only role-discriminating tokens for partial overlap scoring.
        # Fall back to all tokens when the option consists entirely of stop-words.
        discriminating = [t for t in option_tokens if t not in _ROLE_STOP_WORDS]
        tokens_to_check = discriminating if discriminating else option_tokens
        overlap = sum(1 for t in tokens_to_check if t in value_tokens) / len(tokens_to_check)
        best = max(best, overlap)
    return round(best, 2)


def _skill_overlap(job_skills: tuple[SkillTag, ...], required: tuple[SkillTag, ...]) -> float:
    if not required:
        return 0.0
    names = {skill.canonical_name.casefold() for skill in job_skills}
    hits = sum(1 for skill in required if skill.canonical_name.casefold() in names)
    return min(1.0, hits / max(1, len(required)))


def _cosine_sim(v1: tuple[float, ...], v2: tuple[float, ...]) -> float:
    import math

    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2, strict=False))
    m1 = math.sqrt(sum(a * a for a in v1))
    m2 = math.sqrt(sum(b * b for b in v2))
    if m1 == 0 or m2 == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (m1 * m2)))


class MultiProfileMatchNode:
    def __init__(self, catalog: ProfileCatalog) -> None:
        self._catalog = catalog

    async def process(self, item: JobRecord) -> JobRecord | None:
        tracer = trace.get_tracer("job_ftch.nodes")
        with tracer.start_as_current_span("match_scoring.score") as span:
            span.set_attribute("job_ftch.node", "MultiProfileMatchNode")
            span.set_attribute("job_ftch.node.type", "scoring")
            span.set_attribute("job_ftch.profile_count", len(self._catalog.profiles))

            if not self._catalog.profiles:
                result = item.model_copy(
                    update={
                        "profile_scores": (),
                        "relevance_score": 1.0,
                        "best_profile_id": None,
                        "best_score": None,
                    }
                )
                return result

            constraint_states = {
                profile.profile_id: self._hard_constraint_states(item, profile)
                for profile in self._catalog.profiles
            }
            scores = tuple(
                self._score_profile(
                    item,
                    profile,
                    hard_pass=all(
                        state != "contradicted"
                        for state in constraint_states[profile.profile_id].values()
                    ),
                )
                for profile in self._catalog.profiles
            )
            # Profiles own their own calibrated thresholds. A lower raw score
            # can be an ACCEPT for one profile while the global maximum is
            # merely REVIEW for another; catalog policy is any ACCEPT, then
            # any REVIEW, then all confidently negative.
            accepted = [score for score in scores if score.decision is MatchDecision.ACCEPT]
            reviewable = [score for score in scores if score.decision is MatchDecision.REVIEW]
            selected = accepted or reviewable or list(scores)
            best_match = max(selected, key=lambda score: score.final_score, default=None)
            best = best_match.final_score if best_match is not None else 0.0

            if best_match is not None:
                span.set_attribute("job_ftch.best_score", best)
                span.set_attribute("job_ftch.best_profile_id", best_match.profile_id)

            return item.model_copy(
                update={
                    "profile_scores": scores,
                    "relevance_score": best,
                    "best_profile_id": best_match.profile_id if best_match is not None else None,
                    "best_score": best if best_match is not None else None,
                    "metadata": {
                        **item.metadata,
                        "multi_profile_policy": "per_profile_v1",
                        "hard_constraint_states": constraint_states,
                    },
                }
            )

    def _score_profile(
        self, item: JobRecord, profile: SearchProfile, *, hard_pass: bool | None = None
    ) -> ProfileMatchScore:
        if hard_pass is None:
            hard_pass = self._passes_hard_constraints(item, profile)
        role_text = "\n".join(
            part
            for part in (
                item.title_normalized or item.title,
                item.description,
                item.role_family,
                item.domain,
                item.industry,
            )
            if part
        )
        title_score = _string_overlap_score(
            item.title_normalized or item.title, profile.target_roles
        )
        semantic_role_score = max(
            title_score,
            _string_overlap_score(item.role_family, profile.target_roles),
        )
        explicit_skills = item.skills_explicit + item.skills_inferred
        hard_skill_score = _skill_overlap(explicit_skills, profile.required_skills)
        preferred_skill_score = _skill_overlap(explicit_skills, profile.preferred_skills)
        skills_score = min(1.0, 0.65 * hard_skill_score + 0.35 * preferred_skill_score)
        domain_score = max(
            _string_overlap_score(role_text, profile.target_domains),
            _string_overlap_score(role_text, profile.target_industries),
        )
        seniority_score = self._seniority_score(item.seniority, profile)
        region_score = self._region_score(item, profile)
        salary_score = self._salary_score(item, profile)
        culture_score = self._culture_score(item, profile)

        # Vector scoring
        job_vector = item.metadata.get("embedding_vector")
        vector_score = 0.0
        neg_vector_penalty = 0.0
        if job_vector and isinstance(job_vector, (list, tuple)):
            job_vector = tuple(job_vector)
            if profile.embedding_vector:
                vector_score = _cosine_sim(job_vector, profile.embedding_vector)
            if profile.negative_embedding_vectors:
                sims = [_cosine_sim(job_vector, nv) for nv in profile.negative_embedding_vectors]
                # If very similar to any negative example, penalize
                max_neg_sim = max(sims) if sims else 0.0
                if max_neg_sim > 0.8:
                    neg_vector_penalty = 0.6
                elif max_neg_sim > 0.7:
                    neg_vector_penalty = 0.2

        # Fallback: if no positive-example centroid, use the cross-lingual role-anchor
        # similarity computed by EmbeddingPrefilterNode. This lets Russian-but-relevant
        # posts rank up and off-target posts (e.g. QA when seeking AI) rank down.
        if vector_score == 0.0:
            role_match = item.metadata.get("embedding_role_match")
            if isinstance(role_match, (int, float)):
                vector_score = max(0.0, min(1.0, float(role_match)))

        risk_penalty = min(1.0, 0.15 * len(item.risk_signals))
        vacancy_type_score = 1.0 if item.post_type.value == "job_posting" else 0.0

        weighted = (
            profile.weights.semantic_role * semantic_role_score
            + profile.weights.skills * skills_score
            + profile.weights.domain * domain_score
            + profile.weights.seniority * seniority_score
            + profile.weights.region * region_score
            + profile.weights.salary * salary_score
            + profile.weights.culture * culture_score
            + profile.weights.vector * vector_score
            + 0.05 * vacancy_type_score
        )
        final_score = max(
            0.0,
            min(
                1.0,
                round(weighted - risk_penalty - neg_vector_penalty, 2),
            ),
        )

        if not hard_pass:
            decision = MatchDecision.REJECT
        elif final_score >= profile.relevance_threshold:
            decision = MatchDecision.ACCEPT
        elif final_score >= max(0.2, profile.relevance_threshold * 0.75):
            decision = MatchDecision.REVIEW
        else:
            decision = MatchDecision.REJECT

        explanation = (
            f"title={title_score:.2f} semantic={semantic_role_score:.2f} skills={skills_score:.2f} "
            f"domain={domain_score:.2f} vector={vector_score:.2f} vacancy={vacancy_type_score:.0f} "
            f"neg_p={neg_vector_penalty:.2f}"
        )
        return ProfileMatchScore(
            profile_id=profile.profile_id,
            profile_name=profile.name,
            hard_pass=hard_pass,
            vacancy_type_score=vacancy_type_score,
            semantic_role_score=semantic_role_score,
            title_score=title_score,
            skills_score=skills_score,
            domain_score=domain_score,
            seniority_score=seniority_score,
            region_score=region_score,
            salary_score=salary_score,
            culture_score=culture_score,
            vector_score=vector_score,
            neg_vector_penalty=neg_vector_penalty,
            risk_penalty=risk_penalty,
            final_score=final_score,
            decision=decision,
            explanation=explanation,
        )

    def _passes_hard_constraints(self, item: JobRecord, profile: SearchProfile) -> bool:
        return all(
            state != "contradicted"
            for state in self._hard_constraint_states(item, profile).values()
        )

    def _hard_constraint_states(self, item: JobRecord, profile: SearchProfile) -> dict[str, str]:
        """Return evidence states, never treating absence as contradiction."""
        states: dict[str, str] = {}
        # Permissive by default: the bot only sets ``allowed_languages``
        # when the user ran a PDF resume through LLM extraction. A
        # text-only ``/positive`` run does not touch it, and a missing
        # allow-list must not silently block otherwise-relevant jobs.
        if (
            profile.allowed_languages
            and item.language not in profile.allowed_languages
            and item.language != "unknown"
        ):
            states["language"] = "contradicted"
        elif profile.allowed_languages:
            states["language"] = "unknown" if item.language == "unknown" else "present"
        if profile.employment_types:
            states["employment_type"] = (
                "unknown"
                if item.employment_type.value == "unknown"
                else "present"
                if item.employment_type in profile.employment_types
                else "contradicted"
            )
        if (
            profile.blocked_companies
            and item.company is not None
            and any(
                company.casefold() in item.company.casefold()
                for company in profile.blocked_companies
            )
        ):
            states["company"] = "contradicted"
        elif profile.blocked_companies:
            states["company"] = "unknown" if item.company is None else "present"
        if (
            profile.blocked_domains
            and item.domain is not None
            and any(
                domain.casefold() in item.domain.casefold() for domain in profile.blocked_domains
            )
        ):
            states["domain"] = "contradicted"
        elif profile.blocked_domains:
            states["domain"] = "unknown" if item.domain is None else "present"
        if (
            profile.seniority_min is not Seniority.UNKNOWN
            or profile.seniority_max is not Seniority.UNKNOWN
        ):
            if item.seniority is Seniority.UNKNOWN:
                states["seniority"] = "unknown"
            elif (
                profile.seniority_min is not Seniority.UNKNOWN
                and _SENIORITY_ORDER.get(item.seniority, -1)
                < _SENIORITY_ORDER[profile.seniority_min]
            ) or (
                profile.seniority_max is not Seniority.UNKNOWN
                and _SENIORITY_ORDER.get(item.seniority, -1)
                > _SENIORITY_ORDER[profile.seniority_max]
            ):
                states["seniority"] = "contradicted"
            else:
                states["seniority"] = "present"
        if profile.required_skills:
            explicit = {skill.canonical_name.casefold() for skill in item.skills_explicit}
            required = {skill.canonical_name.casefold() for skill in profile.required_skills}
            states["required_skills"] = "present" if required <= explicit else "unknown"
        return states

    def _seniority_score(self, seniority: Seniority, profile: SearchProfile) -> float:
        if seniority is Seniority.UNKNOWN:
            return 0.5
        if (
            profile.seniority_min is Seniority.UNKNOWN
            and profile.seniority_max is Seniority.UNKNOWN
        ):
            return 1.0
        current = _SENIORITY_ORDER[seniority]
        min_val = _SENIORITY_ORDER.get(profile.seniority_min, -1)
        max_val = _SENIORITY_ORDER.get(profile.seniority_max, 999)
        return 1.0 if min_val <= current <= max_val else 0.0

    def _region_score(self, item: JobRecord, profile: SearchProfile) -> float:
        values = tuple(
            value for value in (item.region, item.country, item.city, item.location) if value
        )
        haystack = " ".join(values).casefold()
        region_terms = (
            profile.preferred_regions + profile.preferred_countries + profile.preferred_cities
        )
        if not region_terms:
            return 1.0
        return 1.0 if any(term.casefold() in haystack for term in region_terms) else 0.0

    def _salary_score(self, item: JobRecord, profile: SearchProfile) -> float:
        if profile.salary_expectation is None or item.compensation is None:
            return 0.5
        if item.compensation.currency != profile.salary_expectation.currency:
            return 0.0
        floor = profile.salary_expectation.min_amount
        offered = item.compensation.max_amount or item.compensation.min_amount
        if floor is None or offered is None:
            return 0.5
        return 1.0 if offered >= floor else 0.0

    def _culture_score(self, item: JobRecord, profile: SearchProfile) -> float:
        if not profile.culture_preferences:
            return 0.5
        haystack = {signal.casefold() for signal in item.culture_signals}
        hits = sum(1 for pref in profile.culture_preferences if pref.casefold() in haystack)
        return min(1.0, hits / max(1, len(profile.culture_preferences)))
