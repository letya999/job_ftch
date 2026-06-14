"""Pipeline node for on-the-fly job text translation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_ftch.application.contracts import TranslatorPort
    from job_ftch.domain import JobRecord


class TranslationNode:
    """Translates job title and description to a target language if needed.

    Uses detected_language from metadata (set by LanguageDetectionNode).
    Preserves original text in metadata['original_title'] and metadata['original_description'].
    Skips translation if:
    - detected_language == target_language (no-op)
    - language pair not supported by translator (e.g. KZ)
    - detected_language is 'unknown'
    """

    def __init__(self, translator: TranslatorPort, target_language: str = "ru") -> None:
        self._translator = translator
        self._target_language = target_language

    async def process(self, item: JobRecord) -> JobRecord:
        detected = item.metadata.get("detected_language", "unknown")

        # Skip if same language or unsupported pair or unknown
        if (
            detected == "unknown"
            or detected == self._target_language
            or not self._translator.supports(detected, self._target_language)
        ):
            return item

        # Translate title and description
        original_title = item.title or ""
        original_description = item.description or ""

        translated_title = await self._translator.translate(
            original_title, detected, self._target_language
        )
        translated_description = await self._translator.translate(
            original_description, detected, self._target_language
        )

        # Preserve originals in metadata
        updated_metadata = {
            **item.metadata,
            "original_title": original_title,
            "original_description": original_description,
            "translation_source_lang": detected,
            "translation_target_lang": self._target_language,
        }

        return item.model_copy(
            update={
                "title": translated_title or original_title,
                "description": translated_description or original_description,
                "metadata": updated_metadata,
            }
        )
