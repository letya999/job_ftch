"""Fast post-type classification before extraction.

The keyword lists used here are loaded from `job_ftch/infrastructure/classifiers/keyword_lists.yaml`
via `infrastructure/classifiers/keyword_lists.py`. Operators can extend
or override the four categories (announcement, job_posting, candidate,
spam) without touching code.
"""

from __future__ import annotations

import re

from opentelemetry import trace

from job_ftch.application.contracts import ClassificationResult, ClassifierProvider
from job_ftch.application.graph.params import float_param
from job_ftch.domain import (
    ObservationKind,
    PostType,
    RawItem,
    SourceFamily,
    source_identity_for_raw_item,
)

_ROLE_PATTERN = re.compile(
    r"\b(?:product\s+(?:manager|management)|project\s+manager|machine\s+learning\s+engineer|"
    r"ml\s+engineer|ai\s+engineer|data\s+scientist|tech\s+lead|team\s+lead|"
    r"engineer|developer|architect|analyst|specialist|director|head|lead|manager|"
    r"продакт(?:[-\s]?менеджер)?|менеджер|инженер|разработчик|архитектор|аналитик|"
    r"специалист|руководитель|директор|тимлид|техлид)\b",
    re.IGNORECASE,
)
_VACANCY_CONTEXT_PATTERN = re.compile(
    r"(?:\b(?:company|team|salary|remote|office|hybrid|responsibilit(?:y|ies)|"
    r"requirements?|apply|contact|resume|location|job\s+description|more\s+details)\b|"
    r"\b(?:компания|команда|зарплата|удален(?:но|ка)|офис|гибрид|обязанност[ьи]|"
    r"требовани[яй]|отклик|контакт[ыа]?|резюме|локация|ищ(?:ем|ет|ут))\b|"
    r"[\w.+-]+@[\w.-]+\.[a-z]{2,}|@[a-z0-9_]{4,})",
    re.IGNORECASE,
)


def has_hiring_role_signal(text: str) -> bool:
    """Return whether source text explicitly names a plausible hiring role."""
    return _ROLE_PATTERN.search(text) is not None


class PostTypeClassificationNode:
    def __init__(
        self,
        classifier: ClassifierProvider | None = None,
        *,
        confidence_threshold: float = 0.8,
        announcement_tokens: tuple[str, ...] = (),
        job_posting_tokens: tuple[str, ...] = (),
        job_posting_strong_tokens: tuple[str, ...] = (),
        candidate_tokens: tuple[str, ...] = (),
        spam_tokens: tuple[str, ...] = (),
    ) -> None:
        self._classifier = classifier
        self._confidence_threshold = confidence_threshold
        self._announcement_tokens = announcement_tokens
        self._job_posting_tokens = job_posting_tokens
        self._job_posting_strong_tokens = job_posting_strong_tokens
        self._candidate_tokens = candidate_tokens
        self._spam_tokens = spam_tokens

    def configure_graph_params(self, params: dict[str, object]) -> None:
        if "confidence_threshold" in params:
            self._confidence_threshold = float_param(
                params, "confidence_threshold", self._confidence_threshold
            )

    async def process(self, item: RawItem) -> RawItem | None:
        result = await self._classify(item)
        metadata = {
            **item.metadata,
            "preclassified_post_type": result.label,
            "preclassified_confidence": f"{result.confidence:.2f}",
            "preclassified_model": result.model_id,
            "post_type_distribution": {
                result.label: round(result.confidence, 4),
                PostType.UNKNOWN.value: round(1.0 - result.confidence, 4),
            },
            "post_type_evidence": f"{result.model_id}:{result.label}",
        }
        return item.model_copy(update={"metadata": metadata})

    async def _classify(self, item: RawItem) -> ClassificationResult:
        lowered = item.text.casefold()
        identity = source_identity_for_raw_item(item)

        # A trusted adapter has already proved that this is a concrete vacancy
        # detail/record. Incidental words such as "резюме", "курс" or a spam
        # token in footer text must not override that source contract.
        if (
            identity.observation_kind
            in {ObservationKind.VACANCY_DETAIL, ObservationKind.STRUCTURED_RECORD}
            and item.metadata.get("detail_vacancy_confirmed") is True
        ):
            return ClassificationResult(PostType.JOB_POSTING.value, 0.99, "source_contract_v1")

        # Candidate self-promotion ("ищу работу") is unambiguous.  A spam
        # token is not: legitimate employers can operate in betting, gaming,
        # crypto, or another domain that also appears in the spam dictionary.
        # Therefore vacancy intent and source shape must be evaluated before
        # the broad spam vocabulary.
        if any(token in lowered for token in self._candidate_tokens):
            return ClassificationResult(PostType.CANDIDATE_SEEKING.value, 0.95, "rules_v2")

        # Strong vacancy intent overrides incidental event mentions: a real job
        # post that mentions a hackathon/meetup/course must NOT be classified as
        # announcement (which hard-drops it before extraction).
        if any(token in lowered for token in self._job_posting_strong_tokens):
            return ClassificationResult(PostType.JOB_POSTING.value, 0.9, "rules_v2")

        # Career pages and Telegram posts often omit the literal word
        # "vacancy".  A role plus concrete hiring context is stronger than an
        # incidental announcement token such as "internship" or "course".
        if (
            identity.family in {SourceFamily.TELEGRAM, SourceFamily.CAREER_WEB}
            and has_hiring_role_signal(item.text)
            and _VACANCY_CONTEXT_PATTERN.search(item.text)
        ):
            model_id = (
                "telegram_shape_v1"
                if identity.family is SourceFamily.TELEGRAM
                else "career_shape_v1"
            )
            return ClassificationResult(PostType.JOB_POSTING.value, 0.92, model_id)

        if any(token in lowered for token in self._spam_tokens):
            return ClassificationResult(PostType.SPAM.value, 0.95, "rules_v2")

        if self._classifier is not None:
            from job_ftch.config import get_settings

            settings = get_settings()
            tracer = trace.get_tracer("job_ftch.nodes")

            with tracer.start_as_current_span("post_type.classify") as span:
                span.set_attribute("langfuse.observation.type", "generation")

                if settings.tracing_capture_payloads:
                    span.set_attribute("langfuse.observation.input", item.text)

                result = await self._classifier.classify(item.text)

                if result.model_id:
                    span.set_attribute("gen_ai.request.model", result.model_id)
                if settings.tracing_capture_payloads:
                    span.set_attribute("langfuse.observation.output", result.label)

                if result.confidence >= self._confidence_threshold:
                    return result

        if any(token in lowered for token in self._announcement_tokens):
            return ClassificationResult(PostType.ANNOUNCEMENT.value, 0.9, "rules_v2")

        if any(token in lowered for token in self._job_posting_tokens):
            return ClassificationResult(PostType.JOB_POSTING.value, 0.85, "rules_v2")

        return ClassificationResult(PostType.UNKNOWN.value, 0.5, "rules_v2")
