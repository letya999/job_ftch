"""Property-based tests for SanitizeNode using Hypothesis.

Key invariant: SanitizeNode must NEVER raise anything other than
RawItemRejected on arbitrary (potentially adversarial) text input.
"""

from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from job_ftch.application.rejections import RawItemRejected
from job_ftch.domain import RawItem, SourceKind
from job_ftch.nodes import SanitizeNode


@pytest.mark.unit
@given(text=st.text(max_size=50_000))
@settings(max_examples=500)  # type: ignore[misc]
def test_sanitize_node_never_crashes_on_arbitrary_text(text: str) -> None:
    """SanitizeNode must raise only RawItemRejected or return RawItem.

    No other exception is acceptable — input comes from untrusted external channels.
    """
    import asyncio

    node = SanitizeNode()
    item = RawItem.model_construct(
        stable_id="",
        source_kind=SourceKind.DEBUG,
        source_name="src",
        external_id="1",
        url=None,
        text=text,
        metadata={},
    )
    try:
        result = asyncio.run(node.process(item))
        assert result is None or isinstance(result, RawItem)
    except RawItemRejected:
        pass  # expected


@pytest.mark.unit
@given(
    title=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
        max_size=100,
    )
)
@settings(max_examples=300)  # type: ignore[misc]
def test_ontology_normalizer_never_crashes_on_arbitrary_title(title: str) -> None:
    """Ontology normalizer must return None or str — no crash."""
    try:
        from job_ftch.infrastructure.ontology.normalizer import get_default_normalizer

        norm = get_default_normalizer()
        result = norm.infer_role_family(title, language="en")
        assert result is None or isinstance(result, str)
    except ImportError:
        pytest.skip("ontology normalizer not available")
