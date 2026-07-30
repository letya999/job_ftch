"""BGE-M3 embedding provider: dense + sparse in a single forward pass."""

from __future__ import annotations

import os
import threading
from typing import Any

import numpy as np

_DIM_DENSE = 1024
_MODEL_CACHE: dict[tuple[str, bool], tuple[Any, threading.Lock]] = {}
_MODEL_CACHE_LOCK = threading.Lock()


class BgeMThreeProvider:
    """Lazy, thread-safe BGE-M3 encoder.

    Produces dense (1024-d) and sparse (lexical weights) vectors for each text.
    A single encode call covers both; downstream nodes read from metadata.
    """

    def __init__(self, model_name: str = "BAAI/bge-m3", *, use_fp16: bool | None = None) -> None:
        self._model_name = model_name
        self._use_fp16 = _resolve_fp16(use_fp16)
        self._model = None
        self._lock = threading.Lock()
        self._encode_lock = threading.Lock()

    def _ensure(self) -> None:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    key = (self._model_name, self._use_fp16)
                    with _MODEL_CACHE_LOCK:
                        cached = _MODEL_CACHE.get(key)
                        if cached is None:
                            from FlagEmbedding import BGEM3FlagModel

                            if not self._use_fp16:
                                import torch

                                torch.set_num_threads(min(4, max(1, os.cpu_count() or 1)))
                            cached = (
                                BGEM3FlagModel(self._model_name, use_fp16=self._use_fp16),
                                threading.Lock(),
                            )
                            _MODEL_CACHE[key] = cached
                    self._model, self._encode_lock = cached

    @property
    def dim(self) -> int:
        return _DIM_DENSE

    def encode(
        self, text: str, *, max_length: int = 512, return_sparse: bool = False
    ) -> dict[str, Any]:
        """Return {'dense': np.ndarray[1024]} and optionally 'sparse': dict[int, float]."""
        self._ensure()
        assert self._model is not None
        with self._encode_lock:
            out = self._model.encode(
                [text],
                batch_size=1,
                max_length=max_length,
                return_dense=True,
                return_sparse=return_sparse,
                return_colbert_vecs=False,
            )
        dense = np.asarray(out["dense_vecs"][0], dtype=np.float32)
        result: dict[str, Any] = {"dense": dense}
        if return_sparse:
            result["sparse"] = {int(k): float(v) for k, v in out["lexical_weights"][0].items()}
        return result

    def encode_batch(self, texts: list[str], *, max_length: int = 512) -> list[dict]:  # type: ignore
        """Encode multiple texts; returns list of {'dense', 'sparse'} dicts."""
        self._ensure()
        assert self._model is not None
        with self._encode_lock:
            out = self._model.encode(
                texts,
                batch_size=32,
                max_length=max_length,
                return_dense=True,
                return_sparse=True,
                return_colbert_vecs=False,
            )
        return [
            {
                "dense": np.asarray(out["dense_vecs"][i], dtype=np.float32),
                "sparse": {int(k): float(v) for k, v in out["lexical_weights"][i].items()},
            }
            for i in range(len(texts))
        ]


def _resolve_fp16(requested: bool | None) -> bool:
    if requested is not None:
        return requested
    try:
        import torch

        return bool(torch.cuda.is_available())
    except ImportError:
        return False
