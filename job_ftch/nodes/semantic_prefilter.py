"""Profile-aware semantic prefilter before expensive extraction."""

from __future__ import annotations

import re
from collections.abc import Iterable  # noqa: TC003

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import RawItem, TriageRejectionReason
from job_ftch.domain.profile import ProfileCatalog, SearchProfile  # noqa: TC001

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9+#-]+")


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(text)}


def _overlap_score(tokens: set[str], phrases: Iterable[str]) -> float:
    normalized = [phrase.casefold().strip() for phrase in phrases if phrase.strip()]
    if not normalized:
        return 0.0
    best = 0.0
    for phrase in normalized:
        phrase_tokens = {part for part in phrase.split() if part}
        if not phrase_tokens:
            continue
        overlap = len(phrase_tokens & tokens) / len(phrase_tokens)
        best = max(best, overlap)
    return min(1.0, round(best, 4))


class SemanticPrefilterNode:
    def __init__(
        self,
        catalog: ProfileCatalog,
        *,
        uncertain_ratio: float = 0.75,
    ) -> None:
        self._catalog = catalog
        self._uncertain_ratio = uncertain_ratio

    async def process(self, item: RawItem) -> RawItem | None:
        tokens = _tokens(item.text)
        scores: list[tuple[str, float]] = []
        for profile in self._catalog.profiles:
            score = self._score_profile(profile, tokens, item.text.casefold())
            scores.append((profile.profile_id, score))

        best_profile_id, best_score = max(scores, key=lambda pair: pair[1], default=("default", 0.0))
        threshold = max(profile.relevance_threshold for profile in self._catalog.profiles)
        if best_score < threshold * self._uncertain_ratio:
            raise RawItemDropped(
                reason=TriageRejectionReason.TELEGRAM_LOW_SIGNAL,
                details=(
                    "Semantic prefilter found no sufficiently strong profile match. "
                    f"best_profile={best_profile_id!r} best_score={best_score:.2f}"
                ),
                item=item,
            )

        metadata = {
            **item.metadata,
            "semantic_prefilter_best_profile": best_profile_id,
            "semantic_prefilter_best_score": f"{best_score:.2f}",
            "semantic_prefilter_scores": {
                profile_id: round(score, 4) for profile_id, score in scores
            },
        }
        return item.model_copy(update={"metadata": metadata})

    def _score_profile(self, profile: SearchProfile, tokens: set[str], lowered_text: str) -> float:
        title_score = _overlap_score(tokens, profile.target_roles)
        domain_score = _overlap_score(tokens, profile.target_domains)
        hard_score = _overlap_score(tokens, profile.hard_requirements)
        soft_score = _overlap_score(tokens, profile.soft_preferences)
        anti_score = _overlap_score(tokens, profile.anti_preferences)
        profile_desc_bonus = 0.0
        if profile.profile_description and any(token in lowered_text for token in profile.profile_description.casefold().split()):
            profile_desc_bonus = 0.15
        score = (
            profile.weights.title * title_score
            + profile.weights.domain * domain_score
            + 0.25 * hard_score
            + 0.25 * soft_score
            + profile_desc_bonus
            - 0.35 * anti_score
        )
        return max(0.0, min(1.0, round(score, 4)))
