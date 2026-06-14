"""Property-based tests for OntologyNormalizer using Hypothesis."""

from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given
from hypothesis import strategies as st


@pytest.mark.unit
@given(skills=st.lists(st.text(max_size=50), min_size=0, max_size=20))
def test_normalize_skills_idempotent(skills: list[str]) -> None:
    """normalize(normalize(x)) == normalize(x) — idempotency."""
    try:
        from job_ftch.domain.models import SkillTag
        from job_ftch.infrastructure.ontology.normalizer import get_default_normalizer

        norm = get_default_normalizer()
        tags = tuple(SkillTag(canonical_name=s) for s in skills if s.strip())
        first = norm.normalize_skills(tags)
        second = norm.normalize_skills(first)
        assert first == second
    except ImportError:
        pytest.skip("ontology normalizer not available")
