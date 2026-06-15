"""Fast post-type classification before extraction."""

from __future__ import annotations

from job_ftch.application.contracts import ClassificationResult, ClassifierProvider
from job_ftch.domain import PostType, RawItem

_ANNOUNCEMENT_TOKENS = (
    # Russian
    "дайджест",
    "подборка",
    "обзор недели",
    "стрим",
    "прямой эфир",
    "трансляц",
    "анонс",
    "вебинар",
    "конференц",
    "дискусси",
    "панельная",
    "воркшоп",
    "саммит",
    "митап",
    "записали",
    "запись стрим",
    "запись эфир",
    "делимся результат",
    "приходите",
    "присоединяйтесь",
    # English
    "webinar",
    "meetup",
    "course",
    "workshop",
    "summit",
    "podcast",
    "broadcast",
    "panel discussion",
    "digest",
    "roundup",
    "newsletter",
    "recording of",
    "watch the",
)

_JOB_POSTING_TOKENS = (
    # Strong Russian signals
    "вакансия",
    "вакансии",
    "вакан",
    "требуется",
    "открыта позиция",
    "открыта вакансия",
    "ищем специалист",
    "нужен разработчик",
    "нужен инженер",
    "нужен специалист",
    "нанимаем",
    "нанимает",
    "оффер",
    "#вакансия",
    "#hiring",
    "#job",
    "#vacancy",
    # English signals
    "we're hiring",
    "we are hiring",
    "hiring for",
    "join our team",
    "looking for a",
    "looking for an",
    "job opportunity", "open position",
    "apply now",
    "salary",
    "vacancy",
    "hiring",
    # Weak but present signals
    "remote",
    "relocation",
)


class PostTypeClassificationNode:
    def __init__(
        self,
        classifier: ClassifierProvider | None = None,
        *,
        confidence_threshold: float = 0.8,
    ) -> None:
        self._classifier = classifier
        self._confidence_threshold = confidence_threshold

    async def process(self, item: RawItem) -> RawItem | None:
        result = await self._classify(item)
        metadata = {
            **item.metadata,
            "preclassified_post_type": result.label,
            "preclassified_confidence": f"{result.confidence:.2f}",
            "preclassified_model": result.model_id,
        }
        return item.model_copy(update={"metadata": metadata})

    async def _classify(self, item: RawItem) -> ClassificationResult:
        if self._classifier is not None:
            result = await self._classifier.classify(item.text)
            if result.confidence >= self._confidence_threshold:
                return result

        lowered = item.text.casefold()
        if any(
            token in lowered for token in ("#candidate", "#резюме", "open to work", "ищу работу")
        ):
            return ClassificationResult(PostType.CANDIDATE_SEEKING.value, 0.95, "rules_v2")

        if any(token in lowered for token in _ANNOUNCEMENT_TOKENS):
            return ClassificationResult(PostType.ANNOUNCEMENT.value, 0.9, "rules_v2")

        if any(token in lowered for token in ("casino", "betting", "odds", "букмекер")):
            return ClassificationResult(PostType.SPAM.value, 0.95, "rules_v2")

        if any(token in lowered for token in _JOB_POSTING_TOKENS):
            return ClassificationResult(PostType.JOB_POSTING.value, 0.85, "rules_v2")

        return ClassificationResult(PostType.UNKNOWN.value, 0.5, "rules_v2")
