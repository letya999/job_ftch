"""Tests for the runtime ontology priority in builder.merge_derived_ontology.

The original pipeline only ever saw the static
``fixtures/shots/derived_ontology.json`` seed file. Roles and
skills the LLM extracted from the user's shots were saved to
``jf_ontology_*`` tables but never read back. The fix is to read
the live ontology first and merge it on top of the seed so a
user-added role like ``Senior LLM Engineer`` survives a
``/run``.
"""

from __future__ import annotations

import pytest

from job_ftch.application.builder import (
    _build_runtime_ontology_payload,
    _merge_ontology_dicts,
)

# ---------------------------------------------------------------------------
# _merge_ontology_dicts
# ---------------------------------------------------------------------------


def test_merge_ontology_dicts_concatenates_lists() -> None:
    seed = {"roles": ["AI Engineer", "ML Engineer"], "anti_patterns": ["x"]}
    runtime = {"roles": ["Senior LLM Engineer"], "anti_patterns": ["y"]}
    merged = _merge_ontology_dicts(seed, runtime)
    assert "AI Engineer" in merged["roles"]
    assert "ML Engineer" in merged["roles"]
    assert "Senior LLM Engineer" in merged["roles"]
    assert "x" in merged["anti_patterns"]
    assert "y" in merged["anti_patterns"]


def test_merge_ontology_dicts_case_insensitive_dedup() -> None:
    """A role in both the seed and runtime layer must collapse to
    one entry; the runtime form is the canonical one.
    """
    seed = {"roles": ["AI Engineer", "ML Engineer"]}
    runtime = {"roles": ["AI Engineer", "Senior LLM Engineer"]}
    merged = _merge_ontology_dicts(seed, runtime)
    # The merged list contains each role at most once.
    lower_count = sum(1 for r in merged["roles"] if r.lower() == "ai engineer")
    assert lower_count == 1
    assert "Senior LLM Engineer" in merged["roles"]


def test_merge_ontology_dicts_empty_seed_does_not_drop_runtime() -> None:
    """A new tenant (no seed) must still get the runtime ontology."""
    runtime = {"roles": ["Senior LLM Engineer"], "skills": ["fastapi"]}
    merged = _merge_ontology_dicts({}, runtime)
    assert merged["roles"] == ["Senior LLM Engineer"]
    assert merged["skills"] == ["fastapi"]


def test_merge_ontology_dicts_dedup_positive_keywords_by_term() -> None:
    """``positive_keywords`` is a list of ``{term, weight}`` dicts;
    dedup must match on the term field, not the dict identity.
    """
    seed = {
        "positive_keywords": [
            {"term": "python", "weight": 5},
            {"term": "fastapi", "weight": 3},
        ]
    }
    runtime = {
        "positive_keywords": [
            {"term": "python", "weight": 4},
            {"term": "kubernetes", "weight": 3},
        ]
    }
    merged = _merge_ontology_dicts(seed, runtime)
    terms = [p["term"] for p in merged["positive_keywords"]]
    assert terms.count("python") == 1
    assert "fastapi" in terms
    assert "kubernetes" in terms


def test_merge_ontology_dicts_missing_keys_handled() -> None:
    """The seed file and the runtime store may have different keys;
    the merge must not crash on missing ones."""
    seed = {"roles": ["AI Engineer"]}
    runtime = {"anti_patterns": ["marketing"]}
    merged = _merge_ontology_dicts(seed, runtime)
    assert merged["roles"] == ["AI Engineer"]
    assert merged["anti_patterns"] == ["marketing"]
    # Other keys default to empty list.
    assert merged["skills"] == []


# ---------------------------------------------------------------------------
# _build_runtime_ontology_payload
# ---------------------------------------------------------------------------


class _StubOntologyStore:
    """In-memory stub for the live ontology.

    Mirrors the methods the builder reads from
    ``PostgresOntologyStore`` / ``DBOntologyStore`` (async).
    """

    def __init__(
        self,
        *,
        skills: tuple[str, ...] = (),
        roles: tuple[str, ...] = (),
        positive_keywords: tuple[dict[str, object], ...] = (),
        negative_keywords: tuple[dict[str, object], ...] = (),
        anti: tuple[str, ...] = (),
        seniority: tuple[str, ...] = (),
    ) -> None:
        self._skills = skills
        self._roles = roles
        self._positive_keywords = positive_keywords
        self._negative_keywords = negative_keywords
        self._anti = anti
        self._seniority = seniority

    async def list_skills(self, lang: str | None = None) -> tuple[str, ...]:
        return self._skills

    async def list_roles(self, lang: str | None = None) -> tuple[str, ...]:
        return self._roles

    async def list_anti_patterns(self) -> tuple[str, ...]:
        return self._anti

    async def list_seniority(self) -> tuple[str, ...]:
        return self._seniority

    async def list_positive_keywords(self) -> tuple[dict[str, object], ...]:
        return self._positive_keywords

    async def list_negative_keywords(self) -> tuple[dict[str, object], ...]:
        return self._negative_keywords


@pytest.mark.anyio
async def test_build_runtime_ontology_payload_reads_live_tables() -> None:
    """The builder must read skills, roles, anti_patterns from
    the live store and return them in the seed's dict shape.
    """
    store = _StubOntologyStore(
        skills=("python", "fastapi"),
        roles=("Senior LLM Engineer", "AI Automation Specialist"),
        positive_keywords=({"term": "llm", "weight": 5},),
        negative_keywords=({"term": "etl only", "weight": 3},),
        anti=("training models from scratch",),
        seniority=("senior",),
    )
    payload = await _build_runtime_ontology_payload(store)
    assert "python" in payload["skills"]
    assert "fastapi" in payload["skills"]
    assert "Senior LLM Engineer" in payload["roles"]
    assert "AI Automation Specialist" in payload["roles"]
    assert payload["positive_keywords"] == [{"term": "llm", "weight": 5}]
    assert payload["negative_keywords"] == [{"term": "etl only", "weight": 3}]
    assert "training models from scratch" in payload["anti_patterns"]
    assert "senior" in payload["seniority"]


@pytest.mark.anyio
async def test_build_runtime_ontology_payload_returns_empty_for_none() -> None:
    """A legacy / file backend has no live store. The builder
    must return an empty dict so the seed is used as-is.
    """
    payload = await _build_runtime_ontology_payload(None)
    assert payload == {}


@pytest.mark.anyio
async def test_build_runtime_ontology_payload_tolerates_partial_store() -> None:
    """A custom backend might implement only some of the methods.
    The builder must skip missing methods, not crash.
    """

    class _PartialStore:
        async def list_skills(self) -> tuple[str, ...]:
            return ("python",)

        # list_roles / list_anti_patterns / list_seniority are missing.

    payload = await _build_runtime_ontology_payload(_PartialStore())
    assert payload["skills"] == ["python"]
    # The other keys are present (empty) so the merge dict has them.
    assert payload["roles"] == []
    assert payload["anti_patterns"] == []
