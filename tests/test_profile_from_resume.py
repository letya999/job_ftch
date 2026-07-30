from job_ftch.application.profile_inputs import build_profile_from_resume_text
from job_ftch.application.resume_extraction import merge_resume_profile


def test_heuristic_extraction():
    text = """
    Ivan Ivanov / Иван Иванов
    Senior Python Developer
    Skills: Python, SQL, Postgres, Kubernetes, Docker.
    Roles: Developer, Lead.
    Language: Russian, English.
    Опыт работы: Разработка на Python.
    """
    profile_managed = build_profile_from_resume_text(text, user_id="123")
    profile = profile_managed.profile

    assert profile.identity.display_name == "Ivan Ivanov / Иван Иванов"

    assert len(profile.search_profiles[0].required_skills) == 0

    assert "ru" in profile.search_profiles[0].languages_of_interest
    assert "en" in profile.search_profiles[0].languages_of_interest
    assert profile.search_profiles[0].relevance_threshold == 0.35
    assert profile.resume.raw_text == text[:5000].strip()


def test_add_example_to_profile():
    from job_ftch.application.profile_inputs import add_example_to_profile

    text = "Senior Python Developer"
    managed = build_profile_from_resume_text(text, user_id="123")

    # Add positive example
    example1 = "Job description for a great role"
    managed2 = add_example_to_profile(managed, example1, kind="positive_job")
    assert example1 in managed2.profile.search_profiles[0].positive_job_example_texts

    # Add negative example
    example2 = "Spam job"
    managed3 = add_example_to_profile(managed2, example2, kind="negative_job")
    assert example2 in managed3.profile.search_profiles[0].negative_job_example_texts
    assert example1 in managed3.profile.search_profiles[0].positive_job_example_texts


def test_negative_resume_does_not_expand_positive_target_roles() -> None:
    existing = build_profile_from_resume_text(
        "AI Integration Engineer with Python, APIs and automation experience",
        user_id="123",
    )
    extracted_negative = build_profile_from_resume_text(
        "Senior Data Scientist focused on churn, LTV, forecasting and A/B testing",
        user_id="123",
    )

    merged = merge_resume_profile(existing, extracted_negative, is_negative=True)
    sp = merged.profile.search_profiles[0]

    roles_lower = tuple(role.casefold() for role in sp.target_roles)
    assert any("ai integration engineer" in role for role in roles_lower)
    assert not any("senior data scientist" in role for role in roles_lower)
    assert sp.negative_example_texts
