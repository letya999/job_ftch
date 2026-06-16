"""Profile-aware semantic prefilter before expensive extraction."""

from __future__ import annotations

import re
from collections.abc import Iterable  # noqa: TC003

from job_ftch.application.drops import RawItemDropped
from job_ftch.domain import RawItem, SourceKind, TriageRejectionReason
from job_ftch.domain.profile import ProfileCatalog, SearchProfile  # noqa: TC001

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9+#-]+")

# Common stop-words that carry no signal in a profile description context.
_DESC_SKIP_WORDS = frozenset({
    "and", "or", "the", "for", "with", "from", "into", "across",
    "their", "that", "this", "also", "are", "its", "our", "your",
    "who", "how", "what", "when", "will", "have", "been", "about",
    "in", "on", "at", "to", "of", "a", "an", "is", "it",
})

_CYRILLIC_RE = re.compile(r'[А-Яа-яЁё]')
_LATIN_RE = re.compile(r'[A-Za-z]')


def _cyrillic_ratio(text: str) -> float:
    """Return fraction of letter characters that are Cyrillic."""
    cyrillic = len(_CYRILLIC_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    total = cyrillic + latin
    return cyrillic / total if total > 0 else 0.0


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
        # Career-site sources are already narrowed by source-level discovery and URL
        # selection. Applying Telegram-style keyword overlap here regresses recall for
        # valid vacancies like hh/tbank/yandex pages whose titles omit AI markers.
        if item.source_kind is SourceKind.CAREER_SITE:
            return item

        # Lower the drop threshold for Russian text: profile keywords are English so
        # token overlap against Russian posts is systematically underestimated.
        ratio = self._uncertain_ratio
        if _cyrillic_ratio(item.text) > 0.4:
            ratio = min(self._uncertain_ratio, 0.5)

        tokens = _tokens(item.text)
        scores: list[tuple[str, float]] = []
        for profile in self._catalog.profiles:
            score = self._score_profile(profile, tokens, item.text.casefold())
            scores.append((profile.profile_id, score))

        best_profile_id, best_score = max(
            scores, key=lambda pair: pair[1], default=("default", 0.0)
        )
        threshold = max(profile.relevance_threshold for profile in self._catalog.profiles)

        # OR-logic: the JobBERT embedding prefilter (runs upstream) may have rescued a
        # cross-lingual / low-token-overlap item. Honour the stronger of the two signals.
        embedding_match = item.metadata.get("embedding_role_match")
        effective_score = best_score
        if isinstance(embedding_match, (int, float)):
            effective_score = max(best_score, float(embedding_match))

        if effective_score < threshold * ratio:
            raise RawItemDropped(
                reason=TriageRejectionReason.LOW_RELEVANCE_PREFILTER,
                details=(
                    "Semantic prefilter found no sufficiently strong profile match. "
                    f"best_profile={best_profile_id!r} best_score={best_score:.2f} "
                    f"embedding={embedding_match if embedding_match is not None else 'n/a'}"
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

        # Fallback: if profile has no soft_preferences, use required_skills names
        if not profile.soft_preferences and profile.required_skills:
            skill_names = tuple(s.canonical_name for s in profile.required_skills)
            soft_score = _overlap_score(tokens, skill_names)

        profile_desc_bonus = 0.0
        if profile.profile_description:
            desc_tokens = [
                tok
                for raw in profile.profile_description.casefold().split()
                for tok in [raw.strip(".,;:!?()\"""")]
                if len(tok) >= 2 and tok not in _DESC_SKIP_WORDS
            ]
            if any(tok in lowered_text for tok in desc_tokens if tok):
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
