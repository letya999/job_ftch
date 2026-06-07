from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .models import SourceKind  # noqa: TC001


class FilterProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = "default"
    required_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    allowed_source_kinds: list[SourceKind] | None = None
    min_text_tokens: int = Field(default=3, ge=1)
    min_text_chars: int = Field(default=18, ge=1)
    positive_relevance_keywords: list[str] = Field(default_factory=list)
    negative_relevance_keywords: list[str] = Field(default_factory=list)
    relevance_threshold: float = Field(default=0.0, ge=0.0, le=1.0)

    @classmethod
    def default(cls) -> FilterProfile:
        # Hardcoded defaults that mirror pre-Phase-12 behaviour exactly.
        # positive_relevance_keywords mirrors _POSITIVE_KEYWORDS from nodes/relevance.py
        # negative_relevance_keywords mirrors _NEGATIVE_KEYWORDS from nodes/relevance.py
        # exclude_keywords mirrors _IRRELEVANT_PATTERNS from nodes/triage.py
        # min_text_tokens=3, min_text_chars=18 mirror HeuristicTriageNode constructor defaults
        return cls(
            name="default",
            required_keywords=[],
            exclude_keywords=[
                "subscribe",
                "follow us",
                "webinar",
                "meetup",
                "conference",
                "course",
                "training",
                "newsletter",
                "digest",
                "news",
                "podcast",
                "like and share",
            ],
            allowed_source_kinds=None,
            min_text_tokens=3,
            min_text_chars=18,
            positive_relevance_keywords=[
                "ai",
                "llm",
                "genai",
                "mlops",
                "ml ",
                "machine learning",
                "agent",
                "rag",
                "prompt",
                "infra",
                "platform",
                "data scientist",
                "ai pm",
                "ai product",
            ],
            negative_relevance_keywords=[
                "sales",
                "account executive",
                "hr",
                "recruiter",
                "office manager",
                "marketing",
            ],
            relevance_threshold=0.0,
        )
