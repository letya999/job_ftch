from __future__ import annotations

import re

from job_ftch.application.contracts import ClassificationResult
from job_ftch.domain import FilterProfile
from job_ftch.infrastructure.classifiers.keyword_lists import load_job_posting_strong_tokens

_ANNOUNCEMENT_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bдайджест\b",
        r"\bподборк",
        r"\bобзор\b",
        r"\bпрямой\s+эфир\b",
        r"\bзапись\s+(?:стрим|эфир|вебинар)",
        r"\bтрансляц",
        r"\bонлайн.конференц",
        r"\bанонс\b",
        r"\bанонсируем\b",
        r"\bвебинар\b",
        r"\bконференц",
        r"\bдискусси",
        r"\bпанельная\b",
        r"\bпанель\b",
        r"\bворкшоп\b",
        r"\bсаммит\b",
        r"\bмитап\b",
        r"\bdigest\b",
        r"\bpodcast\b",
        r"\bstream\b",
        r"\bbroadcast\b",
        r"\bworkshop\b",
        r"\bsummit\b",
        r"\bpanel\s+discussion\b",
        r"\bwebinar\b",
        r"\bделимся\b",
        r"\bрасскажем\b",
        r"\bрассказываем\b",
        r"\bнаши\s+(?:результаты|успехи|новости)\b",
    ]
]


class KeywordClassifierProvider:
    model_id = "keyword_v1"

    def __init__(self, *, profile: FilterProfile | None = None) -> None:
        self._profile = profile or FilterProfile.default()

    async def classify(self, text: str) -> ClassificationResult:
        lowered = text.casefold()
        # Check spam patterns (regex-based)
        for pattern in self._profile.spam_signal_patterns:
            if re.search(pattern, lowered):
                return ClassificationResult("spam", 0.95, self.model_id)

        # Strong job-posting signals override incidental event mentions.
        # A real job post that mentions a hackathon/workshop must not be
        # mis-routed to announcement and hard-dropped before extraction.
        if any(token in lowered for token in load_job_posting_strong_tokens()):
            return ClassificationResult("job_posting", 0.90, self.model_id)

        # Check announcement patterns (fast, no LLM)
        for ann_pattern in _ANNOUNCEMENT_PATTERNS:
            if ann_pattern.search(lowered):
                return ClassificationResult("announcement", 0.88, self.model_id)

        # Check candidate patterns (substring)
        if any(p.casefold() in lowered for p in self._profile.candidate_signal_patterns):
            return ClassificationResult("candidate_seeking", 0.90, self.model_id)

        return ClassificationResult("unknown", 0.5, self.model_id)

    async def classify_batch(self, texts: list[str]) -> list[ClassificationResult]:
        results = []
        for text in texts:
            results.append(await self.classify(text))
        return results
