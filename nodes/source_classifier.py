from __future__ import annotations

from typing import TYPE_CHECKING

from application.drops import RawItemDropped
from domain import RawItem, TriageRejectionReason

if TYPE_CHECKING:
    from application.contracts import ClassifierProvider

_REJECTED_LABELS = frozenset({"candidate_seeking", "spam"})


class SourceClassifierNode:
    def __init__(
        self,
        classifier: ClassifierProvider,
        *,
        confidence_threshold: float = 0.80,
    ) -> None:
        self._classifier = classifier
        self._threshold = confidence_threshold

    async def process(self, item: RawItem) -> RawItem | None:
        result = await self._classifier.classify(item.text)
        if result.label in _REJECTED_LABELS and result.confidence >= self._threshold:
            raise RawItemDropped(
                reason=TriageRejectionReason.IRRELEVANT_CONTENT,
                details=(
                    f"Classifier rejected item: label={result.label!r} "
                    f"confidence={result.confidence:.2f} model={result.model_id!r}"
                ),
                item=item,
            )
        return item
