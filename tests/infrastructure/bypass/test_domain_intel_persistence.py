from __future__ import annotations

import json
import time

from job_ftch.infrastructure.bypass.domain_intel import DomainIntelligence


def test_stale_route_recommendation_expires() -> None:
    intel = DomainIntelligence(cache_path=None)
    intel.record_success("example.test", "camoufox")
    intel.get("example.test").last_updated = time.time() - 8 * 24 * 60 * 60
    assert intel.get_recommended_tier("example.test") is None


def test_api_endpoint_persists_shape_without_credentials_or_values(tmp_path) -> None:
    path = tmp_path / "domain-intel.json"
    intel = DomainIntelligence(path)
    intel.add_api_endpoint(
        "example.test",
        "https://user:password@example.test/api/jobs?page=2&token=secret#private",
    )
    intel.save()
    raw = path.read_text(encoding="utf-8")
    assert "password" not in raw
    assert "secret" not in raw
    assert "user" not in raw
    assert "page=" in raw and "token=" in raw


def test_cookie_cache_is_allowlisted_and_never_persisted(tmp_path) -> None:
    path = tmp_path / "domain-intel.json"
    intel = DomainIntelligence(path)
    intel.cache_cookie("example.test", "session", "must-not-store")
    intel.cache_cookie("example.test", "cf_clearance", "clearance-secret")
    intel.record_success("example.test", "stealth_browser")
    intel.save()
    raw = path.read_text(encoding="utf-8")
    assert "must-not-store" not in raw
    assert "clearance-secret" not in raw
    assert intel.get_cookies("example.test") == {"cf_clearance": "clearance-secret"}


def test_separate_instances_merge_pending_updates_without_loss(tmp_path) -> None:
    path = tmp_path / "domain-intel.json"
    first = DomainIntelligence(path)
    second = DomainIntelligence(path)
    first.load()
    second.load()
    first.record_success("one.test", "curl_stealth")
    second.record_success("two.test", "camoufox")
    first.save()
    second.save()

    restored = DomainIntelligence(path)
    restored.load()
    assert restored.get_recommended_tier("one.test") == "curl_stealth"
    assert restored.get_recommended_tier("two.test") == "camoufox"


def test_repeated_cross_instance_counter_updates_are_additive(tmp_path) -> None:
    path = tmp_path / "domain-intel.json"
    first = DomainIntelligence(path)
    second = DomainIntelligence(path)
    first.record_failure_kind("example.test", "curl_stealth", "blocked_ip")
    second.record_failure_kind("example.test", "curl_stealth", "tls_error")
    first.save()
    second.save()

    data = json.loads(path.read_text(encoding="utf-8"))
    stats = data["domains"][0]["tier_stats"]["curl_stealth"]
    assert stats["by_kind"] == {"blocked_ip": 1, "tls_error": 1}


def test_proxy_route_stats_and_recommendation_preserve_network(tmp_path) -> None:
    path = tmp_path / "domain-intel.json"
    intel = DomainIntelligence(path)
    intel.record_failure_kind(
        "example.test",
        "curl_stealth",
        "blocked_ip",
        network="proxy",
    )
    intel.record_success("example.test", "curl_stealth", network="proxy")
    intel.save()

    restored = DomainIntelligence(path)
    restored.load()
    assert restored.get_recommended_route("example.test") == ("curl_stealth", "proxy")
    assert "curl_stealth@proxy" in restored.get("example.test").tier_stats


def test_successful_monitor_scraper_pair_is_bounded_and_persisted(tmp_path) -> None:
    path = tmp_path / "domain-intel.json"
    intel = DomainIntelligence(path)
    for index in range(25):
        intel.record_monitor_scraper("example.test", f"monitor-{index}", "maintext")
    intel.save()

    restored = DomainIntelligence(path)
    restored.load()
    pairs = restored.get("example.test").successful_monitor_scrapers
    assert len(pairs) == 20
    assert pairs[-1] == "monitor-24:maintext"


def test_repeated_classified_failures_invalidate_cached_route() -> None:
    intel = DomainIntelligence(cache_path=None)
    intel.record_success("example.test", "camoufox")
    for _ in range(3):
        intel.record_failure_kind(
            "example.test",
            "camoufox",
            "blocked_fingerprint",
        )
    assert intel.get_recommended_route("example.test") is None


def test_corrupt_cache_recovers_with_atomic_valid_json(tmp_path) -> None:
    path = tmp_path / "domain-intel.json"
    path.write_text('{"domains": [', encoding="utf-8")
    intel = DomainIntelligence(path)
    intel.record_success("example.test", "noop")
    intel.save()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["domains"][0]["domain"] == "example.test"
    assert not list(tmp_path.glob("*.tmp"))
