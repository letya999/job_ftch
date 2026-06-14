"""Pipeline node for language detection of job postings."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_ftch.application.contracts import LanguageDetectorPort
    from job_ftch.domain import JobRecord


class LanguageDetectionNode:
    """Detects the language of each job posting and stores it in metadata.

    Uses the injected LanguageDetectorPort — no direct external imports.
    Stores detected language in job.metadata['detected_language'].
    """

    def __init__(self, detector: LanguageDetectorPort) -> None:
        self._detector = detector

    async def process(self, item: JobRecord) -> JobRecord:
        # Build detection text from title + first 300 chars of description
        parts = []
        if item.title:
            parts.append(item.title)
        if item.description:
            parts.append(item.description[:300])
        if not parts:
            return item
        sample = " ".join(parts)
        detected = self._detector.detect(sample)
        updated_metadata = {**item.metadata, "detected_language": detected}
        return item.model_copy(update={"metadata": updated_metadata})
