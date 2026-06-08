from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from application.contracts import ClassificationResult
from domain import PostType  # noqa: TCH001

if TYPE_CHECKING:
    from application.contracts import LLMProvider


class _PostTypeSchema(BaseModel):
    post_type: PostType
    ai_relevance: float = Field(ge=0.0, le=1.0)
    reasoning: str  # brief explanation, not stored but helps LLM quality


class LLMClassifierProvider:
    model_id = "llm_classifier_v1"

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def classify(self, text: str) -> ClassificationResult:
        prompt = (
            "Classify this text. Is it a job posting (job_posting), "
            "a person seeking work (candidate_seeking), an announcement/event (announcement), "
            "or spam/unrelated (spam)? "
            "Also rate ai_relevance: 0.0=not AI/ML role, 1.0=clearly AI/ML role.\n\n"
            f"Text:\n{text[:600]}"
        )
        try:
            result = await self._llm.extract(prompt, _PostTypeSchema)
            label = result.post_type.value
            confidence = result.ai_relevance if label == "job_posting" else 0.9
            return ClassificationResult(label, confidence, self.model_id)
        except Exception:
            return ClassificationResult("unknown", 0.0, self.model_id)

    async def classify_batch(self, texts: list[str]) -> list[ClassificationResult]:
        results = []
        for text in texts:
            results.append(await self.classify(text))
        return results
