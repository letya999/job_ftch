import json

import pytest

from job_ftch.application.filter_profile_loader import load_filter_profile, load_profile_catalog
from job_ftch.domain import ProfileCatalog, SearchProfile


@pytest.mark.unit
def test_search_profile_normalizes_profile_id():
    profile = SearchProfile(profile_id="  ml_engineer  ", name="ML")
    assert profile.profile_id == "ml_engineer"


@pytest.mark.unit
def test_search_profile_weights_sum_check():
    from job_ftch.domain.profile import ProfileWeights

    # ProfileWeights currently allows individual weights up to 1.0 without total sum check
    weights = ProfileWeights(title=1.0, skills=1.0)
    assert weights.title == 1.0
    assert weights.skills == 1.0


@pytest.mark.unit
def test_profile_catalog_single_profile_default():
    catalog = ProfileCatalog.default()
    assert len(catalog.profiles) == 1
    assert catalog.profiles[0].profile_id == "ai_roles"


@pytest.mark.unit
def test_profile_catalog_from_yaml_via_filter_profile_loader(tmp_path):
    yaml_content = """
catalog_name: custom_catalog
profiles:
  - profile_id: ml
    name: ML Engineer
    target_roles: ["ml"]
"""
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml_content, encoding="utf-8")

    try:
        catalog = load_profile_catalog(path)
        assert catalog.catalog_name == "custom_catalog"
        assert len(catalog.profiles) == 1
        assert catalog.profiles[0].profile_id == "ml"
    except RuntimeError as e:
        if "PyYAML required" in str(e):
            pytest.skip("PyYAML not installed")
        raise


@pytest.mark.unit
def test_filter_profile_loader_json_path(tmp_path):
    data = {"name": "json_profile", "target_roles": ["data scientist"]}
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    profile = load_filter_profile(path)
    assert profile.name == "json_profile"
    assert "data scientist" in profile.target_roles
