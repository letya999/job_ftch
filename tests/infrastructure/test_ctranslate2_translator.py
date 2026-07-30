from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from job_ftch.infrastructure.language.translator import CTranslate2Translator


class _Tokenizer:
    def EncodeAsPieces(self, text: str) -> list[str]:
        return text.split()

    def DecodePieces(self, tokens: list[str]) -> str:
        return " ".join(tokens)


class _Translator:
    def translate_batch(self, batches: list[list[str]]):
        return [SimpleNamespace(hypotheses=[list(reversed(batches[0]))])]


class _SentencePiece:
    def __init__(self) -> None:
        self.loaded_path: str | None = None

    def Load(self, path: str) -> None:
        self.loaded_path = path


def _install_model_modules(
    monkeypatch, model_dir, *, downloads: list[dict[str, object]]
) -> list[_SentencePiece]:
    tokenizers: list[_SentencePiece] = []

    def sentencepiece_processor() -> _SentencePiece:
        tokenizer = _SentencePiece()
        tokenizers.append(tokenizer)
        return tokenizer

    def snapshot_download(**kwargs: object) -> str:
        downloads.append(kwargs)
        return str(model_dir)

    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(Translator=lambda *args, **kwargs: _Translator()),
    )
    monkeypatch.setitem(
        sys.modules,
        "sentencepiece",
        SimpleNamespace(SentencePieceProcessor=sentencepiece_processor),
    )
    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot_download)
    )
    return tokenizers


def test_ctranslate2_translator_supports_only_configured_pairs(tmp_path) -> None:
    translator = CTranslate2Translator(tmp_path)

    assert translator.supports("ru", "en") is True
    assert translator.supports("en", "ru") is True
    assert translator.supports("kk", "en") is False


@pytest.mark.asyncio
async def test_ctranslate2_translator_skips_empty_and_unsupported_text(tmp_path) -> None:
    translator = CTranslate2Translator(tmp_path)

    assert await translator.translate("   ", "ru", "en") == "   "
    assert await translator.translate("Сәлем", "kk", "en") == "Сәлем"
    assert await translator.translate("same", "ru", "ru") == "same"


@pytest.mark.asyncio
async def test_ctranslate2_translator_uses_loaded_model(monkeypatch, tmp_path) -> None:
    translator = CTranslate2Translator(tmp_path)
    monkeypatch.setattr(
        translator, "_load_model", lambda source, target: (_Translator(), _Tokenizer())
    )

    result = await translator.translate("hello world", "en", "ru")

    assert result == "world hello"


@pytest.mark.asyncio
async def test_ctranslate2_translator_returns_original_text_when_model_fails(
    monkeypatch, tmp_path
) -> None:
    translator = CTranslate2Translator(tmp_path)

    def _fail(source: str, target: str):
        del source, target
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(translator, "_load_model", _fail)

    assert await translator.translate("hello", "en", "ru") == "hello"


def test_ctranslate2_load_model_downloads_pinned_revision_and_caches(monkeypatch, tmp_path) -> None:
    model_dir = tmp_path / "downloaded"
    model_dir.mkdir()
    (model_dir / "tokenizer.model").write_text("fixture", encoding="utf-8")
    downloads: list[dict[str, object]] = []
    tokenizers = _install_model_modules(monkeypatch, model_dir, downloads=downloads)
    translator = CTranslate2Translator(tmp_path / "cache")

    loaded = translator._load_model("ru", "en")

    cached = translator._load_model("ru", "en")
    assert cached[0] is loaded[0]
    assert cached[1] is loaded[1]
    assert (
        downloads
        == [
            {
                "repo_id": "Helsinki-NLP/opus-mt-ru-en",
                "local_dir": str(tmp_path / "cache" / "Helsinki-NLP_opus-mt-ru-en"),
                "revision": "fbd6dc73284f95536648512cc21d57f19191961a",  # pragma: allowlist secret - public model revision
            }
        ]
    )
    assert tokenizers[0].loaded_path == str(model_dir / "tokenizer.model")


def test_ctranslate2_load_model_rejects_download_without_sentencepiece(
    monkeypatch, tmp_path
) -> None:
    model_dir = tmp_path / "empty-model"
    model_dir.mkdir()
    downloads: list[dict[str, object]] = []
    _install_model_modules(monkeypatch, model_dir, downloads=downloads)

    with pytest.raises(FileNotFoundError, match="No sentencepiece"):
        CTranslate2Translator(tmp_path)._load_model("en", "ru")
