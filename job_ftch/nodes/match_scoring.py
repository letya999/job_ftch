"""Multi-profile match scoring for normalized jobs."""

from __future__ import annotations

import re

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
        overlap = sum(1 for token in option_tokens if token in value_tokens) / len(option_tokens)
        best = max(best, overlap)
    return round(best, 2)


def _skill_overlap(job_skills: tuple[SkillTag, ...], required: tuple[SkillTag, ...]) -> float:
    if not required:
        return 0.0
    names = {skill.canonical_name.casefold() for skill in job_skills}
    hits = sum(1 for skill in required if skill.canonical_name.casefold() in names)
    return min(1.0, hits / max(1, len(required)))


class MultiProfileMatchNode:
    def __init__(self, catalog: ProfileCatalog) -> None:
        self._catalog = catalog

    async def process(self, item: JobRecord) -> JobRecord | None:
        scores = tuple(self._score_profile(item, profile) for profile in self._catalog.profiles)
        best_match = max(scores, key=lambda score: score.final_score, default=None)
        best = best_match.final_score if best_match is not None else 0.0
        return item.model_copy(
            update={
                "profile_scores": scores,
                "relevance_score": best,
                "best_profile_id": best_match.profile_id if best_match is not None else None,
                "best_score": best if best_match is not None else None,
                "routing_decision": best_match.decision if best_match is not None else None,
            }
        )

    def _score_profile(self, item: JobRecord, profile: SearchProfile) -> ProfileMatchScore:
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
            _string_overlap_score(role_text, profile.target_roles + profile.target_domains),
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
        risk_penalty = min(1.0, 0.15 * len(item.risk_signals))
        vacancy_type_score = 1.0 if item.post_type.value == "job_posting" else 0.0

        weighted = (
            profile.weights.title * title_score
            + profile.weights.semantic_role * semantic_role_score
            + profile.weights.skills * skills_score
            + profile.weights.domain * domain_score
            + profile.weights.seniority * seniority_score
            + profile.weights.region * region_score
            + profile.weights.salary * salary_score
            + profile.weights.culture * culture_score
        )
        final_score = max(
            0.0, min(1.0, round(weighted + 0.1 * vacancy_type_score - risk_penalty, 2))
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
            f"domain={domain_score:.2f} seniority={seniority_score:.2f} region={region_score:.2f}"
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
            risk_penalty=risk_penalty,
            final_score=final_score,
            decision=decision,
            explanation=explanation,
        )

    def _passes_hard_constraints(self, item: JobRecord, profile: SearchProfile) -> bool:
        if profile.allowed_languages and item.language not in profile.allowed_languages:
            return False
        if profile.employment_types and item.employment_type not in profile.employment_types:
            return False
        if (
            profile.blocked_companies
            and item.company is not None
            and any(
                company.casefold() in item.company.casefold()
                for company in profile.blocked_companies
            )
        ):
            return False
        if (
            profile.blocked_domains
            and item.domain is not None
            and any(
                domain.casefold() in item.domain.casefold() for domain in profile.blocked_domains
            )
        ):
            return False
        if (
            profile.seniority_min is not Seniority.UNKNOWN
            and _SENIORITY_ORDER.get(item.seniority, -1) < _SENIORITY_ORDER[profile.seniority_min]
        ):
            return False
        return not (
            profile.seniority_max is not Seniority.UNKNOWN
            and _SENIORITY_ORDER.get(item.seniority, -1) > _SENIORITY_ORDER[profile.seniority_max]
        )

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
