"""Tests for the ontology merge semantics in builder.merge_derived_ontology.

The original builder replaced the user's profile.target_roles with
the static list from ``fixtures/shots/derived_ontology.json`` on
every pipeline run. A later additive fix kept the user roles but
also appended broad ontology roles. The current contract is stricter:
ontology roles are vocabulary, not the user's target-role boundary.
"""

from __future__ import annotations

from job_ftch.application.builder import merge_derived_ontology
from job_ftch.domain import SearchProfile, SkillTag
from job_ftch.domain.profile import ProfileCatalog


def _catalog(
    *,
    target_roles: tuple[str, ...] = (),
    soft_preferences: tuple[str, ...] = (),
) -> ProfileCatalog:
    sp = SearchProfile(
        profile_id="user_test",
        target_roles=target_roles,
        soft_preferences=soft_preferences,
    )
    return ProfileCatalog(
        catalog_name="test",
        profiles=(sp,),
    )


# ---------------------------------------------------------------------------
# target_roles: user roles survive a derived-ontology merge
# ---------------------------------------------------------------------------


def test_user_target_roles_survive_derived_merge() -> None:
    """The original bug: derived_ontology's roles overwrote the
    user's. The user's roles are kept, but ontology roles are not
    appended to the explicit target-role contract.
    """
    catalog = _catalog(target_roles=("Senior LLM Engineer", "AI Automation Specialist"))
    seed: dict[str, list[str]] = {"roles": ["AI Engineer", "ML Engineer", "Senior LLM Engineer"]}
    merged = merge_derived_ontology(catalog, seed)
    roles = merged.profiles[0].target_roles
    # User's "Senior LLM Engineer" must be in the result
    assert "Senior LLM Engineer" in roles
    # User's "AI Automation Specialist" must be in the result
    assert "AI Automation Specialist" in roles
    # Seed/runtime ontology roles are not target roles.
    assert "AI Engineer" not in roles
    assert "ML Engineer" not in roles
    # No duplicates
    assert len(roles) == len(set(roles))


def test_derived_merge_does_not_drop_user_role() -> None:
    """Regression: even when the seed's roles list is empty,
    the user's role must survive the merge.
    """
    catalog = _catalog(target_roles=("AI Integration Engineer",))
    seed: dict[str, list[str]] = {"roles": []}
    merged = merge_derived_ontology(catalog, seed)
    roles = merged.profiles[0].target_roles
    assert "AI Integration Engineer" in roles


def test_derived_merge_case_insensitive_dedup() -> None:
    """A role added by the user and the same role added by the
    seed (with different case) must collapse to one entry.
    """
    catalog = _catalog(target_roles=("LLM Engineer",))
    seed: dict[str, list[str]] = {"roles": ["llm engineer", "AI Engineer"]}
    merged = merge_derived_ontology(catalog, seed)
    roles = merged.profiles[0].target_roles
    # "LLM Engineer" is the user-provided canonical form.
    assert roles == ("LLM Engineer",)


# ---------------------------------------------------------------------------
# preferred_skills: user skills survive a derived-ontology merge
# ---------------------------------------------------------------------------


def test_user_preferred_skills_survive_derived_merge() -> None:
    """User's soft_preferences must survive the merge with the
    static seed's positive_keywords.

    The builder uses ``soft_preferences`` as the per-profile
    bucket for the seed's positive keywords (the legacy
    ``positive_relevance_keywords`` field does not exist on
    ``SearchProfile``). The merge must be additive so a user's
    own ``soft_preferences`` are kept and the seed's
    ``positive_keywords`` are appended.
    """
    catalog = _catalog(soft_preferences=("python", "fastapi", "n8n"))
    seed = {
        "positive_keywords": [
            {"term": "python", "weight": 5},
            {"term": "kubernetes", "weight": 3},
            {"term": "docker", "weight": 3},
        ],
    }
    merged = merge_derived_ontology(catalog, seed)
    soft = merged.profiles[0].soft_preferences
    # All three user soft_preferences are present.
    for s in ("python", "fastapi", "n8n"):
        assert s in soft
    # Seed's positive keywords are appended.
    for s in ("kubernetes", "docker"):
        assert s in soft
    # No duplicates of "python".
    assert sum(1 for s in soft if s == "python") == 1


def test_empty_preferred_skills_merge_stays_skill_tags() -> None:
    catalog = ProfileCatalog(
        catalog_name="test",
        profiles=(SearchProfile(profile_id="user_test", preferred_skills=()),),
    )
    seed: dict[str, list[str]] = {"skills": ["python", "llm"]}

    merged = merge_derived_ontology(catalog, seed)
    skills = merged.profiles[0].preferred_skills

    assert skills == (
        SkillTag(canonical_name="python", source="derived_shots"),
        SkillTag(canonical_name="llm", source="derived_shots"),
    )


# ---------------------------------------------------------------------------
# Anti-patterns and negative keywords: append-only
# ---------------------------------------------------------------------------


def test_anti_patterns_and_neg_keywords_are_appended() -> None:
    """Anti-patterns and negative keywords are *added* to the user's
    profile; they are not exclusive. The builder used to set them
    via the keyword-list path, but the original ``merge_derived``
    also overwrote the user's own anti-preferences. We now merge.
    """
    catalog = _catalog()
    seed = {
        "anti_patterns": ["training models from scratch", "marketing role"],
        "negative_keywords": [
            {"term": "junior"},
            {"term": "marketing"},
            {"term": "qa manual"},
        ],
    }
    merged = merge_derived_ontology(catalog, seed)
    profile = merged.profiles[0]
    # anti_preferences is the merged union of anti_patterns and
    # negative_keywords. Both buckets are appended to the user's
    # existing anti_preferences (which is empty here).
    assert "training models from scratch" in profile.anti_preferences
    assert "marketing role" in profile.anti_preferences
    assert "junior" in profile.anti_preferences
    assert "marketing" in profile.anti_preferences
    assert "qa manual" in profile.anti_preferences


# ---------------------------------------------------------------------------
# Empty seed: no-op (user's profile is unchanged)
# ---------------------------------------------------------------------------


def test_empty_seed_is_noop() -> None:
    """An empty seed must not clobber the user's roles."""
    catalog = _catalog(target_roles=("Senior LLM Engineer",))
    merged = merge_derived_ontology(catalog, {})
    roles = merged.profiles[0].target_roles
    assert "Senior LLM Engineer" in roles
