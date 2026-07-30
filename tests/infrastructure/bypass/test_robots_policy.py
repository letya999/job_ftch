from job_ftch.infrastructure.bypass.robots_policy import RobotsPolicy


def test_ats_domain_always_allowed():
    policy = RobotsPolicy(enforce=True, ats_domains=frozenset(["ats.example.com"]))
    verdict = policy.check("https://ats.example.com/jobs")
    assert verdict.allowed is True
    assert verdict.reason == "ats_exempt"


def test_enforcement_disabled_always_allows():
    policy = RobotsPolicy(enforce=False)
    # Even if robots.txt says disallow, it should allow if enforcement is disabled
    policy.load_robots("example.com", "User-agent: *\nDisallow: /")
    verdict = policy.check("https://example.com/jobs")
    assert verdict.allowed is True
    assert verdict.reason == "enforcement_disabled"


def test_enforcement_enabled_blocks_disallowed():
    policy = RobotsPolicy(enforce=True)
    policy.load_robots("example.com", "User-agent: *\nDisallow: /jobs")

    verdict_blocked = policy.check("https://example.com/jobs")
    assert verdict_blocked.allowed is False
    assert verdict_blocked.reason == "disallowed_by_robots"

    verdict_allowed = policy.check("https://example.com/about")
    assert verdict_allowed.allowed is True
    assert verdict_allowed.reason == "allowed"


def test_load_robots_parses_correctly():
    policy = RobotsPolicy(enforce=True, user_agent="testbot")
    content = "User-agent: testbot\nDisallow: /secret\n\nUser-agent: *\nAllow: /"
    policy.load_robots("example.com", content)

    assert "example.com" in policy.cached_domains

    verdict = policy.check("https://example.com/secret")
    assert verdict.allowed is False

    verdict_all = policy.check("https://example.com/public")
    assert verdict_all.allowed is True


def test_no_robots_txt_allows_by_default():
    policy = RobotsPolicy(enforce=True)
    verdict = policy.check("https://example.com/jobs")
    assert verdict.allowed is True
    assert verdict.reason == "no_robots_txt"


def test_add_ats_domain():
    policy = RobotsPolicy(enforce=True)
    policy.add_ats_domain("ats.example.com")

    verdict = policy.check("https://ats.example.com/jobs")
    assert verdict.allowed is True
    assert verdict.reason == "ats_exempt"


def test_env_var_enables_enforcement(monkeypatch):
    monkeypatch.setenv("JOB_FTCH_ROBOTS_ENFORCE", "1")
    policy = RobotsPolicy()
    assert policy.enforce is True

    monkeypatch.setenv("JOB_FTCH_ROBOTS_ENFORCE", "false")
    policy2 = RobotsPolicy()
    assert policy2.enforce is False
