"""Pipeline node: encode job text with BGE-M3, store dense+sparse in metadata."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from job_ftch.domain.bgem3_card import build_bgem3_card

if TYPE_CHECKING:
    from job_ftch.domain import RawItem


logger = structlog.get_logger("job_ftch.nodes.bge_embed")


class BgeMThreeNode:
    """Encodes job text with BGE-M3 and stores dense + sparse vectors in metadata.

    Inserts early in the pipeline. Downstream nodes read bgem3_dense / bgem3_sparse
    from metadata instead of re-encoding, keeping compute to one forward pass per job.
    """

    def __init__(self, provider: Any, *, max_chars: int = 4096, max_length: int = 1024) -> None:
        self._provider = provider
        self._max_chars = max_chars
        self._max_length = max_length
        self._cache: dict[str, dict[str, Any]] = {}

    def configure_graph_params(self, params: dict[str, Any]) -> None:
        requested_model = params.get("model")
        actual_model = getattr(self._provider, "_model_name", None)
        if (
            requested_model is not None
            and actual_model is not None
            and requested_model != actual_model
        ):
            raise ValueError(
                f"BGE graph model {requested_model!r} != runtime model {actual_model!r}"
            )
        if "max_chars" in params:
            self._max_chars = int(params["max_chars"])
        if "max_length" in params:
            self._max_length = int(params["max_length"])

    async def process(self, item: RawItem) -> RawItem:
        text = self._build_text(item)
        if not text:
            return item
        try:
            # Dense and sparse are one encoding contract. Calling the provider
            # with its sparse default disabled silently erased lexical evidence.
            result: dict[str, Any] | None = self._cache.get(text)
            if result is None:
                encoded = await asyncio.to_thread(
                    self._provider.encode,
                    text,
                    max_length=self._max_length,
                    return_sparse=True,
                )
                if not isinstance(encoded, dict):
                    logger.warning(
                        "bge_embed_node_encode_returned_invalid",
                        item_id=item.external_id or item.stable_id,
                    )
                    return item
                result = encoded
                self._cache[text] = result
            if "dense" not in result or "sparse" not in result:
                raise ValueError("BGE provider must return both dense and sparse vectors")
            new_metadata = {
                **item.metadata,
                "bgem3_dense": result["dense"].tolist(),
                "bgem3_sparse": result.get("sparse", {}),
            }
            return item.model_copy(update={"metadata": new_metadata})
        except Exception as exc:
            # Encoding failure is non-fatal: downstream nodes fall back to keyword scoring.
            logger.warning(
                "bge_embed_node_encode_failed",
                item_id=item.external_id or item.stable_id,
                error=str(exc),
            )
            return item

    def _build_text(self, item: RawItem) -> str:
        return build_bgem3_card(item.text, metadata=item.metadata, max_chars=self._max_chars)
