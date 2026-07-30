from job_ftch.application.garbage import garbage_reason


def test_garbage_reason_returns_none_for_real_job():
    assert garbage_reason("Python Engineer wanted, remote, salary 200k") is None


def test_garbage_reason_returns_freelancer_for_service_ad():
    assert garbage_reason("#помогу создаю: под ключ") == "freelancer service ad"


def test_garbage_reason_project_spec_prefix():
    assert garbage_reason("Идея: разработать систему мониторинга...") == "project spec"


def test_garbage_reason_chat_rules():
    reason = garbage_reason("Правила чата: не спамить...")
    assert isinstance(reason, str)
    assert reason is not None


def test_garbage_reason_job_tag_overrides_service_signal():
    assert garbage_reason("#вакансия #помогу создаю:") is None


def test_garbage_reason_drops_course_ad_without_hiring_markers():
    text = (
        "На Stepik вышел курс — «RAG-системы на векторных базах данных». "
        "Скидка 25% действует 72 часа. Открыть курс. Реклама. erid 123"
    )
    assert garbage_reason(text) == "course ad"


def test_garbage_reason_keeps_job_that_mentions_courses():
    text = (
        "We are looking for an AI Education Engineer. "
        "ML Academy offers online courses in AI/ML. "
        "Responsibilities: create educational content and LLM exercises."
    )
    assert garbage_reason(text) is None


def test_garbage_reason_keeps_non_ai_job_post_with_course_like_words():
    text = (
        "Ищу Старшего дизайнера в Делимобиль. Деньги 120-140к на руки. "
        "Перспектива роста и корпоративные курсы."
    )
    assert garbage_reason(text) is None


def test_garbage_reason_drops_external_blog_article_without_hiring_markers():
    text = (
        "https://claude.com/blog/new-in-claude-managed-agents\n"
        "Anthropic завезли новые managed agents и self-review outcomes."
    )
    assert garbage_reason(text) == "external article"


def test_garbage_reason_drops_short_non_job_chatter():
    text = (
        "приятно знать что у Сэма появились деньги чтобы делать инструменты\nкодексом 5.3 я доволен"
    )
    assert garbage_reason(text) == "short non-job chatter"


def test_garbage_reason_keeps_short_hiring_post():
    text = "Ищем AI Automation Engineer, remote, n8n + OpenAI API."
    assert garbage_reason(text) is None


def test_garbage_reason_empty_string_returns_none():
    assert garbage_reason("") is None
