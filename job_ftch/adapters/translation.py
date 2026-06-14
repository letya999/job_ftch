"""Machine translation adapter using ctranslate2 + Helsinki-NLP opus-mt models."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Supported language pairs: {(source, target): hf_model_name}
_SUPPORTED_PAIRS: dict[tuple[str, str], str] = {
    ("ru", "en"): "Helsinki-NLP/opus-mt-ru-en",
    ("en", "ru"): "Helsinki-NLP/opus-mt-en-ru",
}


class CTranslate2Translator:
    """TranslatorPort using ctranslate2 + Helsinki-NLP opus-mt models.

    Models are downloaded from HuggingFace on first use and cached locally.
    Translation runs in a thread executor to avoid blocking the event loop.
    KZ and other unsupported language pairs are silently skipped (returns original text).
    """

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir else Path(".runtime/translation_models")
        self._models: dict[tuple[str, str], tuple[Any, Any]] = {}  # (src, tgt) -> (translator, tokenizer)

    def supports(self, source_lang: str, target_lang: str) -> bool:
        """Return True only for explicitly supported pairs."""
        return (source_lang, target_lang) in _SUPPORTED_PAIRS

    def _load_model(self, source_lang: str, target_lang: str) -> tuple[Any, Any]:
        """Load or return cached (translator, tokenizer) for a language pair."""
        pair = (source_lang, target_lang)
        if pair in self._models:
            return self._models[pair]

        hf_model_name = _SUPPORTED_PAIRS[pair]
        try:
            import ctranslate2
            import sentencepiece as spm
            from huggingface_hub import snapshot_download

            model_dir = snapshot_download(
                repo_id=hf_model_name,
                local_dir=str(self._cache_dir / hf_model_name.replace("/", "_")),
            )
            # Find the sentencepiece model file
            model_path = Path(model_dir)
            sp_files = list(model_path.glob("*.spm")) + list(model_path.glob("source.spm"))
            if not sp_files:
                # Try HuggingFace tokenizer
                sp_files = list(model_path.glob("tokenizer.model"))

            if sp_files:
                tokenizer = spm.SentencePieceProcessor()
                tokenizer.Load(str(sp_files[0]))
            else:
                raise FileNotFoundError(f"No sentencepiece model found in {model_dir}")

            translator = ctranslate2.Translator(model_dir, device="cpu")
            self._models[pair] = (translator, tokenizer)
            logger.info("translation_model_loaded", source=source_lang, target=target_lang, model=hf_model_name)
            return translator, tokenizer

        except ImportError as exc:
            logger.warning("translation_dependencies_missing", error=str(exc))
            raise
        except Exception as exc:
            logger.error("translation_model_load_failed", pair=pair, error=str(exc))
            raise

    async def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate text. Returns original text if translation fails or pair unsupported."""
        if not text.strip():
            return text
        if not self.supports(source_lang, target_lang):
            return text  # silently return original for unsupported pairs (e.g. KZ)
        if source_lang == target_lang:
            return text

        def _sync_translate() -> str:
            translator, tokenizer = self._load_model(source_lang, target_lang)
            # Tokenize
            tokens = tokenizer.EncodeAsPieces(text)
            # Translate
            results = translator.translate_batch([tokens])
            # Decode
            output_tokens = results[0].hypotheses[0]
            return tokenizer.DecodePieces(output_tokens)  # type: ignore[no-any-return]

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _sync_translate)
        except Exception as exc:
            logger.warning("translation_failed", source=source_lang, target=target_lang, error=str(exc))
            return text  # graceful fallback: return original text
