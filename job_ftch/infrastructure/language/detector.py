"""Language detection adapter using lingua-language-detector."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Slavic languages that should map to 'ru' when detected with low confidence
# (Cyrillic scripts that share significant vocabulary overlap with Russian)
_SLAVIC_TO_RU = frozenset({"bg", "uk", "be", "mk", "sr"})
_SLAVIC_CONFIDENCE_THRESHOLD = 0.8


class LinguaLanguageDetector:
    """LanguageDetectorPort implementation using the lingua-language-detector library.

    Supports RU, EN, KZ and 72 other languages.
    On low-confidence Slavic language detection, normalises to 'ru' (pattern from support_rag).
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
        """Detect language of text. Returns ISO code: 'ru', 'en', 'kz', or 'unknown'."""
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

            # Check if it's a Slavic language with low confidence — remap to 'ru'
            if lang_name in _SLAVIC_TO_RU and confidence < _SLAVIC_CONFIDENCE_THRESHOLD:
                return "ru"

            # Common mappings
            lang_map = {
                "ru": "ru",
                "en": "en",
                "kk": "kz",  # lingua ISO 639-1 for Kazakh
                "de": "de",
                "fr": "fr",
            }
            return lang_map.get(lang_name, lang_name)
        except Exception as exc:
            logger.warning("language_detection_failed", error=str(exc))
            # Heuristic fallback: check for Cyrillic chars
            cyrillic_chars = sum(1 for c in text if "Ѐ" <= c <= "ӿ")
            kazakh_chars = sum(1 for c in text if c in "әғқңөұүһӘҒҚҢӨҰҮҺ")
            if kazakh_chars > 5:
                return "kz"
            if cyrillic_chars > len(text) * 0.3:
                return "ru"
            return "unknown"
