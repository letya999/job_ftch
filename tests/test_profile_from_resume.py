from job_ftch.adapters.profile_inputs import build_profile_from_resume_text


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
    
    skills = [s.canonical_name for s in profile.search_profiles[0].required_skills]
    assert "Python" in skills
    assert "SQL" in skills
    assert "Postgres" in skills
    assert "Kubernetes" in skills
    assert "Docker" in skills
    
    assert "Developer" in profile.search_profiles[0].target_roles
    assert "Lead" in profile.search_profiles[0].target_roles
    
    assert "ru" in profile.search_profiles[0].languages_of_interest
    assert "en" in profile.search_profiles[0].languages_of_interest
    assert profile.search_profiles[0].relevance_threshold == 0.3
    assert profile.resume.raw_text == text[:5000].strip()
