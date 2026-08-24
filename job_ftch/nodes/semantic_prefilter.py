"""Profile-aware semantic prefilter before expensive extraction."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable  # noqa: TC003

from opentelemetry import trace

from job_ftch.application.graph.params import float_param
from job_ftch.domain import ObservationKind, RawItem
from job_ftch.domain.profile import ProfileCatalog, SearchProfile  # noqa: TC001

_tracer = trace.get_tracer("job_ftch.nodes")
_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9+#-]+")

# Common stop-words that carry no signal in a profile description context.
_DESC_SKIP_WORDS = frozenset(
    {
        "and",
        "or",
        "the",
        "for",
        "with",
        "from",
        "into",
        "across",
        "their",
        "that",
        "this",
        "also",
        "are",
        "its",
        "our",
        "your",
        "who",
        "how",
        "what",
        "when",
        "will",
        "have",
        "been",
        "about",
        "in",
        "on",
        "at",
        "to",
        "of",
        "a",
        "an",
        "is",
        "it",
    }
)

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_SPECIFIC_AI_BUILD_SIGNAL_RE = re.compile(
    r"\b(rag|mcp|langgraph|agentic|ai[-\s]?agent|ai[-\s]?agents|vibe[-\s]?coder|n8n)\b|"
    r"(ai[-\s]?агент|ai[-\s]?агенты|ии[-\s]?агент|ии[-\s]?агенты|"
    r"llm[-\s]?агент|llm[-\s]?агенты|rag[-\s]?систем|вайб[-\s]?код)",
    re.IGNORECASE,
)
_CORE_AI_SIGNAL_RE = re.compile(
    r"\b(llm|gpt|openai|anthropic|gemini|ai[-\s]?automation|ai[-\s]?product)\b|"
    r"(llm|ai[-\s]?автомат|ии[-\s]?автомат)",
    re.IGNORECASE,
)
_TARGET_BUILD_ROLE_RE = re.compile(
    r"\b(engineer|developer|architect|automation|automator|builder|coder)\b|"
    r"(инженер|разработчик|архитектор|автоматиз|вайб)",
    re.IGNORECASE,
)


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


def _has_strong_ai_signal(text: str) -> bool:
    if _SPECIFIC_AI_BUILD_SIGNAL_RE.search(text):
        return True
    return bool(_CORE_AI_SIGNAL_RE.search(text) and _TARGET_BUILD_ROLE_RE.search(text))


def _has_any_positive_signal(metadata: dict[str, object]) -> bool:
    for key in ("embedding_role_match", "semantic_prefilter_best_score"):
        value = metadata.get(key)
        try:
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0.0:
                return True
        except (TypeError, ValueError):
            continue
    return bool(metadata.get("preclassified_post_type") == "job_posting")


def _is_guaranteed_detail_vacancy(item: RawItem) -> bool:
    """Allow the career-site bypass only for a source-confirmed detail page.

    A career-site source kind alone also covers boards and search results, so
    it is not evidence of a vacancy.  The adapter must explicitly attest that
    it parsed a detail page and the canonical source identity must agree.
    """
    identity = item.source_identity
    return (
        identity is not None
        and identity.observation_kind is ObservationKind.VACANCY_DETAIL
        and item.metadata.get("detail_vacancy_confirmed") is True
    )


class SemanticPrefilterNode:
    def __init__(
        self,
        catalog: ProfileCatalog,
        *,
        uncertain_ratio: float = 0.75,
        relevance_scorer: object | None = None,
        relevance_threshold: float = 0.0,
    ) -> None:
        self._catalog = catalog
        self._uncertain_ratio = uncertain_ratio
        # Optional DB-backed shot-anchor scorer (duck-typed: exposes
        # .score_text(text) -> object with a .margin float). When provided it
        # replaces the YAML token-overlap heuristic with example-posting
        # embedding similarity, including for career-site items (semantic
        # embeddings do not regress career recall the way token overlap did).
        self._relevance_scorer = relevance_scorer
        self._relevance_threshold = relevance_threshold
        self._rescue_logic = "strong_ai_signal"
        self._audit_mode = False

    def enable_audit_mode(self) -> None:
        self._audit_mode = True

    def configure_graph_params(self, params: dict[str, object]) -> None:
        if "dense_margin_threshold" in params:
            self._relevance_threshold = float_param(
                params, "dense_margin_threshold", self._relevance_threshold
            )
        if "rescue_logic" in params:
            self._rescue_logic = str(params["rescue_logic"])

    async def process(self, item: RawItem) -> RawItem | None:
        with _tracer.start_as_current_span("semantic_prefilter.check") as span:
            span.set_attribute("job_ftch.node", "SemanticPrefilterNode")

            if self._audit_mode:
                span.set_attribute("job_ftch.node.result", "audit_passthrough")
                return item

            if self._relevance_scorer is not None:
                # `score_text` may run a sentence-transformers encode, which is
                # blocking CPU work. Keep it off the event loop the Telegram
                # long-poll shares, or the bot stops answering for the whole run.
                return await asyncio.to_thread(self._process_with_shots, item, span)

            # A source kind alone is not proof of a vacancy: it also includes
            # career boards and listing/search pages.  Only a source-confirmed
            # detail vacancy can bypass this weak relevance observation.
            if _is_guaranteed_detail_vacancy(item):
                span.set_attribute("job_ftch.semantic_prefilter.bypass", "detail_vacancy")
                span.set_attribute("job_ftch.node.result", "pass_bypass")
                return item

            # No profiles configured → nothing to filter against; pass through.
            if not self._catalog.profiles:
                span.set_attribute("job_ftch.node.result", "pass")
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

            # OR-logic: the embedding prefilter (runs upstream) may have rescued a
            # cross-lingual / low-token-overlap item. Honour the stronger of the two signals.
            embedding_match = item.metadata.get("embedding_role_match")
            effective_score = best_score
            if isinstance(embedding_match, (int, float)):
                effective_score = max(best_score, float(embedding_match))

            span.set_attribute("job_ftch.semantic_prefilter.overlap_score", float(best_score))
            span.set_attribute(
                "job_ftch.semantic_prefilter.effective_score", float(effective_score)
            )
            span.set_attribute("job_ftch.semantic_prefilter.threshold", float(threshold * ratio))
            span.set_attribute("job_ftch.semantic_prefilter.best_profile", best_profile_id)

            span.set_attribute(
                "job_ftch.node.result",
                "evidence" if effective_score < threshold * ratio else "pass",
            )
            metadata = {
                **item.metadata,
                "semantic_prefilter_best_profile": best_profile_id,
                "semantic_prefilter_best_score": f"{best_score:.2f}",
                "semantic_prefilter_scores": {
                    profile_id: round(score, 4) for profile_id, score in scores
                },
                "semantic_prefilter_uncertain": effective_score < threshold * ratio,
            }
            return item.model_copy(update={"metadata": metadata})

    def _process_with_shots(self, item: RawItem, span: object) -> RawItem | None:
        """Relevance via DB shot-anchor embeddings (set when a scorer is injected)."""
        assert self._relevance_scorer is not None
        # BgeMThreeShotScorer reads pre-computed vectors from metadata;
        # ShotRelevanceScorer encodes the text on the fly.
        score_from_meta = getattr(self._relevance_scorer, "score_from_metadata", None)
        if score_from_meta is not None:
            score = score_from_meta(item.metadata)
            if score is None:
                # bgem3_dense absent (BgeMThreeNode failed upstream): pass through
                # without shot filtering to preserve recall.
                score_text_fn = getattr(self._relevance_scorer, "score_text", None)
                if score_text_fn is None:
                    return item.model_copy(
                        update={
                            "metadata": {**item.metadata, "semantic_prefilter_shot_margin": "n/a"}
                        }
                    )
                score = score_text_fn(item.text)
        else:
            score = self._relevance_scorer.score_text(item.text)  # type: ignore[attr-defined]
        margin = float(score.margin)
        _set = getattr(span, "set_attribute", lambda *_: None)
        _set("job_ftch.semantic_prefilter.backend", "shots")
        _set("job_ftch.semantic_prefilter.shot_margin", margin)
        _set("job_ftch.semantic_prefilter.threshold", float(self._relevance_threshold))
        if margin < self._relevance_threshold:
            rescue = (
                self._rescue_logic == "strong_ai_signal" and _has_strong_ai_signal(item.text)
            ) or (self._rescue_logic == "any_positive" and _has_any_positive_signal(item.metadata))
            if rescue:
                _set("job_ftch.semantic_prefilter.override", "strong_ai_signal")
                _set("job_ftch.node.result", "pass_override")
                metadata = {
                    **item.metadata,
                    "semantic_prefilter_shot_margin": f"{margin:.4f}",
                    "semantic_prefilter_override": self._rescue_logic,
                }
                return item.model_copy(update={"metadata": metadata})
            _set("job_ftch.node.result", "evidence")
        else:
            _set("job_ftch.node.result", "pass")
        metadata = {
            **item.metadata,
            "semantic_prefilter_shot_margin": f"{margin:.4f}",
            "semantic_prefilter_uncertain": margin < self._relevance_threshold,
        }
        return item.model_copy(update={"metadata": metadata})

    def _score_profile(self, profile: SearchProfile, tokens: set[str], lowered_text: str) -> float:
        title_score = _overlap_score(tokens, profile.target_roles)
        domain_score = _overlap_score(tokens, profile.target_domains)
        hard_score = _overlap_score(tokens, profile.hard_requirements)
        soft_score = _overlap_score(tokens, profile.soft_preferences)
        anti_score = _overlap_score(tokens, profile.anti_preferences)

        # Substring match for multi-word phrases that token-set overlap misses.
        # Example: "инженер по машинному обучению" in text matches target_role
        # "инженер по машинному обучению" as a substring even though individual
        # tokens ("по", "по") don't appear in the token-overlap set.
        for role in profile.target_roles:
            if len(role.split()) > 1 and role.casefold() in lowered_text:
                title_score = max(title_score, 0.85)
                break

        for domain in profile.target_domains:
            if len(domain.split()) > 1 and domain.casefold() in lowered_text:
                domain_score = max(domain_score, 0.7)
                break

        # Fallback: if profile has no soft_preferences, use required_skills names
        if not profile.soft_preferences and profile.required_skills:
            skill_names = tuple(s.canonical_name for s in profile.required_skills)
            soft_score = _overlap_score(tokens, skill_names)

        profile_desc_bonus = 0.0
        if profile.profile_description:
            desc_tokens = [
                tok
                for raw in profile.profile_description.casefold().split()
                for tok in [raw.strip('.,;:!?()"')]
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
