from __future__ import annotations


def test_proxy_bypass_apply_browser_args():
    from job_ftch.infrastructure.bypass.proxy_bypass import ProxyBypass, ProxyHealth

    bypass = ProxyBypass()
    bypass._current = ProxyHealth(url="http://1.2.3.4:8080")
    kwargs = bypass.apply_browser_args({})
    assert kwargs["proxy"] == {"server": "http://1.2.3.4:8080"}


def test_proxy_bypass_handle_failure_rotates():
    from job_ftch.infrastructure.bypass.proxy_bypass import ProxyBypass, ProxyHealth

    bypass = ProxyBypass()
    h1 = ProxyHealth(url="http://proxy1:8080")
    h2 = ProxyHealth(url="http://proxy2:8080")
    bypass._health_pool = [h1, h2]
    bypass._current = h1

    bypass.handle_failure("http://example.com", status_code=429, body=b"", error=None)
    assert bypass._current is not h1 or len(bypass._health_pool) >= 1


def test_proxy_bypass_health_scoring():
    from job_ftch.infrastructure.bypass.proxy_bypass import ProxyHealth

    h = ProxyHealth(url="http://proxy:8080")
    assert h.ema_score == 1.0
    h.record_failure()
    assert h.ema_score < 1.0
    assert h.consecutive_failures == 1
    h.record_success()
    assert h.consecutive_failures == 0
    assert h.successes == 1


def test_proxy_bypass_auto_prune():
    from job_ftch.infrastructure.bypass.proxy_bypass import ProxyBypass, ProxyHealth

    bypass = ProxyBypass()
    good = ProxyHealth(url="http://good:8080")
    bad = ProxyHealth(url="http://bad:8080")
    for _ in range(10):
        bad.record_failure()
    bypass._health_pool = [good, bad]
    pruned = bypass._prune_dead()
    assert pruned == 1
    assert bad not in bypass._health_pool
    assert good in bypass._health_pool


def test_proxy_bypass_yaml_and_env_loading(monkeypatch, tmp_path):
    import job_ftch.infrastructure.bypass.proxy_bypass as pb

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "proxies.yaml"
    config_file.write_text("proxies:\n  - http://yaml.proxy:8080\n", encoding="utf-8")

    monkeypatch.setattr(pb, "__file__", str(tmp_path / "a" / "b" / "c" / "d.py"))
    monkeypatch.setenv("JOB_FTCH_PROXY_LIST", "http://env.proxy:8080")
    monkeypatch.delenv("JOB_FTCH_TOR_SOCKS5", raising=False)
    monkeypatch.delenv("JOB_FTCH_TOR_ENABLED", raising=False)

    proxies = pb._load_proxies()
    assert proxies == ["http://yaml.proxy:8080", "http://env.proxy:8080"]


def test_proxy_bypass_tor_integration(monkeypatch, tmp_path):
    import job_ftch.infrastructure.bypass.proxy_bypass as pb

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "proxies.yaml").write_text("proxies: []\n", encoding="utf-8")

    monkeypatch.setattr(pb, "__file__", str(tmp_path / "a" / "b" / "c" / "d.py"))
    monkeypatch.setenv("JOB_FTCH_TOR_ENABLED", "true")
    monkeypatch.delenv("JOB_FTCH_TOR_SOCKS5", raising=False)
    monkeypatch.delenv("JOB_FTCH_PROXY_LIST", raising=False)

    proxies = pb._load_proxies()
    assert any("socks5://127.0.0.1:9050" in p for p in proxies)


def test_proxy_pool_stats():
    from job_ftch.infrastructure.bypass.proxy_bypass import ProxyBypass, ProxyHealth

    bypass = ProxyBypass()
    bypass._health_pool = [ProxyHealth(url="http://a:80"), ProxyHealth(url="http://b:80")]
    stats = bypass.pool_stats
    assert stats["active"] == 2
    assert stats["avg_score"] == 1.0
