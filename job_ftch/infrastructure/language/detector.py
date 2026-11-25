"""Language detection adapter using lingua-language-detector."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class LinguaLanguageDetector:
    """LanguageDetectorPort implementation using the lingua-language-detector library.

    Returns ISO language codes and abstains on ambiguous text.
    """

    def __init__(self) -> None:
        self._detector: Any = None  # lazy init

    def _get_detector(self) -> Any:
        if self._detector is None:
            from lingua import LanguageDetectorBuilder

            self._detector = (
                LanguageDetectorBuilder.from_all_languages()
                .with_preloaded_language_models()
                .build()
            )
            logger.info("lingua_language_detector_loaded")
        return self._detector

    def detect(self, text: str) -> str:
        """Detect the source language without using the writing system as proof."""
        if not text or not text.strip():
            return "unknown"
        try:
            detector = self._get_detector()
            # Use confidence values for better accuracy
            results = detector.compute_language_confidence_values(text[:500])  # limit for speed
            if not results:
                return "unknown"
            best = results[0]
            lang_name: str = best.language.iso_code_639_1.name.lower()
            confidence: float = best.value

            return lang_name if confidence >= 0.8 else "unknown"
        except Exception as exc:
            logger.warning("language_detection_failed", error=str(exc))
            return "unknown"
