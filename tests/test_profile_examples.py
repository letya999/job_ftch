"""Tests for profile example management functions."""

from __future__ import annotations

from job_ftch.application.profile_inputs import (
    add_example_to_profile,
    build_profile_from_resume_text,
    list_examples,
    remove_example_from_profile,
)


def _make_managed():
    return build_profile_from_resume_text("Python developer with 5 years experience", user_id="u1")


def test_add_positive_resume():
    managed = _make_managed()
    updated = add_example_to_profile(managed, "Senior engineer resume", kind="positive_resume")
    examples = list_examples(updated)
    assert any("Senior engineer" in t for t in examples["positive_resume"])


def test_remove_example_in_range():
    managed = _make_managed()
    managed = add_example_to_profile(managed, "Text A", kind="positive_resume")
    managed = add_example_to_profile(managed, "Text B", kind="positive_resume")
    sp_before = managed.profile.search_profiles[0]
    count_before = len(sp_before.positive_example_texts)
    updated = remove_example_from_profile(managed, "positive_resume", 0)
    sp_after = updated.profile.search_profiles[0]
    assert len(sp_after.positive_example_texts) == count_before - 1


def test_remove_example_out_of_range():
    managed = _make_managed()
    managed = add_example_to_profile(managed, "Text A", kind="positive_resume")
    # index 99 is out of range — should return unchanged
    updated = remove_example_from_profile(managed, "positive_resume", 99)
    sp_orig = managed.profile.search_profiles[0]
    sp_updated = updated.profile.search_profiles[0]
    assert sp_orig.positive_example_texts == sp_updated.positive_example_texts


def test_remove_negative_example():
    managed = _make_managed()
    managed = add_example_to_profile(managed, "Bad job example", kind="negative_job")
    managed = add_example_to_profile(managed, "Another bad one", kind="negative_resume")
    sp = managed.profile.search_profiles[0]
    count_before = len(sp.negative_example_texts)
    updated = remove_example_from_profile(managed, "negative_resume", 0)
    sp_after = updated.profile.search_profiles[0]
    assert len(sp_after.negative_example_texts) == count_before - 1


def test_list_examples_empty():
    managed = _make_managed()
    examples = list_examples(managed)
    assert isinstance(examples, dict)
    assert "positive_resume" in examples
    assert "negative_resume" in examples
