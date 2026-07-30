from __future__ import annotations

import pytest

from job_ftch.domain import SourceFamily, SourceKind
from job_ftch.nodes.language_context import SourceContextNode


@pytest.mark.anyio
async def test_source_context_canonicalizes_legacy_career_family(make_raw_item) -> None:
    item = make_raw_item(
        source_kind=SourceKind.CAREER_SITE,
        source_name="hh_llm",
        metadata={"source_family": "career_site"},
    )

    result = await SourceContextNode().process(item)

    assert result is not None
    assert result.metadata["source_family"] == SourceFamily.CAREER_WEB.value
    assert result.source_identity is not None
    assert result.source_identity.family is SourceFamily.CAREER_WEB


@pytest.mark.anyio
async def test_source_context_preserves_canonical_telegram_identity(make_raw_item) -> None:
    item = make_raw_item(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="forproducts",
    )

    result = await SourceContextNode().process(item)

    assert result is not None
    assert result.metadata["source_family"] == SourceFamily.TELEGRAM.value
    assert result.source_identity is not None
    assert result.source_identity.family is SourceFamily.TELEGRAM
