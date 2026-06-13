"""
Ontology correctness tests.
Verifies role family and skill mappings are correct, not just self-consistent.
"""

import pytest

from job_ftch.domain.models import SkillTag
from job_ftch.infrastructure.ontology.normalizer import get_default_normalizer


@pytest.fixture
def norm():
    return get_default_normalizer()


# ---------------------------------------------------------------------------
# Role family correctness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected_family",
    [
        # Engineering titles -> "engineering"
        ("ML Engineer", "engineering"),  # fixed from "data"
        ("Machine Learning Engineer", "engineering"),
        ("Software Engineer", "engineering"),
        ("Backend Developer", "engineering"),
        ("Frontend Engineer", "engineering"),
        # Data titles -> "data"
        ("Data Scientist", "data"),
        ("Data Engineer", "data"),
        # Analytics titles -> "analytics"
        ("Data Analyst", "analytics"),
        ("Business Analyst", "analytics"),
        # Research titles -> "research"
        ("ML Researcher", "research"),
        ("Applied Scientist", "research"),
        # DevOps titles -> "devops"
        ("DevOps Engineer", "devops"),
        ("SRE", "devops"),
        # QA titles -> "qa"
        ("QA Engineer", "qa"),
        ("Test Engineer", "qa"),
        # Product titles -> "product"
        ("Product Manager", "product"),
        ("Product Owner", "product"),
    ],
)
def test_role_family_mapping(norm, title, expected_family):
    """Verifies role_family mapping is semantically correct."""
    result = norm.infer_role_family(title, language="en")
    assert result == expected_family, (
        f"Title {title!r}: expected family={expected_family!r}, got {result!r}"
    )


@pytest.mark.parametrize(
    "title,expected_family",
    [
        # Russian titles
        ("Разработчик Python", "engineering"),
        ("Аналитик данных", "analytics"),
        ("DevOps инженер", "devops"),
        ("Продуктовый менеджер", "product"),
        ("ml-инженер", "engineering"),
    ],
)
def test_role_family_russian_titles(norm, title, expected_family):
    result = norm.infer_role_family(title, language="ru")
    assert result == expected_family, (
        f"RU title {title!r}: expected {expected_family}, got {result}"
    )


def test_mixed_language_title(norm):
    """'Senior Python разработчик' - mixed en/ru title must not crash and should resolve."""
    result = norm.infer_role_family("Senior Python разработчик", language="ru")
    # 'разработчик' is in engineering
    assert result == "engineering"


def test_unknown_title_returns_none(norm):
    result = norm.infer_role_family("Completely Unknown Role XYZ123", language="en")
    assert result is None


# ---------------------------------------------------------------------------
# Seniority correctness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected_seniority",
    [
        ("Senior Python Developer", "senior"),
        ("Junior QA Engineer", "junior"),
        ("Lead Engineer", "lead"),
        ("Staff Engineer", "staff"),
        ("Principal Architect", "principal"),
        (
            "Python Developer",
            None,
        ),  # no seniority signal -> None (default is UNKNOWN in model, but normalizer returns None)
        ("ML Engineer", None),  # no seniority prefix
    ],
)
def test_seniority_inference(norm, title, expected_seniority):
    result = norm.infer_seniority(title)
    assert result == expected_seniority, (
        f"Title {title!r}: expected seniority={expected_seniority!r}, got {result!r}"
    )


# ---------------------------------------------------------------------------
# Skill normalization: negative cases
# ---------------------------------------------------------------------------


def test_skill_normalization_no_cross_contamination(norm):
    """Normalizing Python must not accidentally produce a TypeScript canonical."""
    tags = norm.normalize_skills(
        (
            SkillTag(canonical_name="Python"),
            SkillTag(canonical_name="TypeScript"),
        )
    )
    names = {t.canonical_name.lower() for t in tags}
    assert "python" in names
    assert "typescript" in names

    ids = {t.skill_id for t in tags if t.skill_id}
    assert "python" in ids
    assert "typescript" in ids
    assert len(names) == 2


def test_unknown_skill_returns_raw_name(norm):
    """A skill with no alias match must return the raw name as-is."""
    tags = norm.normalize_skills((SkillTag(canonical_name="UnknownFrameworkXYZ999"),))
    assert len(tags) == 1
    assert tags[0].canonical_name == "UnknownFrameworkXYZ999"
    assert tags[0].skill_id is None


def test_normalize_skills_empty_list(norm):
    tags = norm.normalize_skills(())
    assert tags == ()
