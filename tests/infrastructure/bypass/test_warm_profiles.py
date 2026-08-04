"""TRACK B - warm profile behaviors: per-domain profiles, session-memory keying,
cold-only warm-up navigation, and top-level-only Referer scoping."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from job_ftch.infrastructure.bypass.adaptive import (
    AdaptiveBypassManager,
    _domain_profile_key,
    _gc_profile_root,
)


def _mgr(tmp_path: Path, *, domain: str = "jobs.example.com") -> AdaptiveBypassManager:
    mgr = AdaptiveBypassManager({}, adaptive_enabled=True)
    mgr._profile_root = tmp_path / "profiles"
    mgr._profile_per_domain = True
    mgr._context = SimpleNamespace(
        persona=SimpleNamespace(name="persona_b"),
        domain=domain,
    )
    return mgr


class TestPerDomainProfileDir:
    def test_same_domain_reuses_dir(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        prepared = mgr.prepare_browser_config({"persistent_context": True})
        first = prepared["_profile_dir"]
        expected = mgr._profile_root / _domain_profile_key("jobs.example.com")
        assert Path(first) == expected
        assert Path(first).is_dir()
        # A second controller for the same domain lands on the same dir.
        mgr2 = _mgr(tmp_path)
        second = mgr2.prepare_browser_config({"persistent_context": True})["_profile_dir"]
        assert Path(second) == expected

    def test_different_domains_differ(self, tmp_path: Path) -> None:
        a = _mgr(tmp_path, domain="a.example.com")
        b = _mgr(tmp_path, domain="b.example.com")
        pa = a.prepare_browser_config({"persistent_context": True})["_profile_dir"]
        pb = b.prepare_browser_config({"persistent_context": True})["_profile_dir"]
        assert pa != pb

    @pytest.mark.asyncio
    async def test_close_keeps_per_domain_dir(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        profile = Path(mgr.prepare_browser_config({"persistent_context": True})["_profile_dir"])
        assert profile.is_dir()
        await mgr.close()
        # A reused per-domain profile must survive close() (not owned).
        assert profile.is_dir()

    def test_per_domain_disabled_falls_back_to_tempdir(self, tmp_path: Path) -> None:
        mgr = _mgr(tmp_path)
        mgr._profile_per_domain = False
        profile = mgr.prepare_browser_config({"persistent_context": True})["_profile_dir"]
        assert mgr._profile_root not in Path(profile).parents

    @pytest.mark.skipif("os.name == 'nt'")
    def test_profile_dir_is_private(self, tmp_path: Path) -> None:
        import os

        mgr = _mgr(tmp_path)
        profile = Path(mgr.prepare_browser_config({"persistent_context": True})["_profile_dir"])
        assert (profile.stat().st_mode & 0o777) == 0o700
        assert os.name != "nt"


class TestProfileGc:
    def test_ttl_eviction(self, tmp_path: Path) -> None:
        root = tmp_path / "profiles"
        root.mkdir()
        old = root / "old"
        old.mkdir()
        (old / "f").write_bytes(b"x")
        stale = time.time() - 40 * 86400
        import os

        os.utime(old, (stale, stale))
        _gc_profile_root(root, max_bytes=10**9, ttl_days=30, keep=None)
        assert not old.exists()

    def test_lru_eviction_over_budget(self, tmp_path: Path) -> None:
        root = tmp_path / "profiles"
        root.mkdir()
        import os

        now = time.time()
        for idx, name in enumerate(("oldest", "newest")):
            d = root / name
            d.mkdir()
            (d / "blob").write_bytes(b"x" * 1000)
            os.utime(d, (now - (10 - idx) * 3600, now - (10 - idx) * 3600))
        # Budget only fits one dir -> the least-recently-used one is evicted.
        _gc_profile_root(root, max_bytes=1500, ttl_days=365, keep=None)
        assert not (root / "oldest").exists()
        assert (root / "newest").exists()

    def test_keep_is_never_removed(self, tmp_path: Path) -> None:
        root = tmp_path / "profiles"
        root.mkdir()
        keep = root / "keepme"
        keep.mkdir()
        (keep / "blob").write_bytes(b"x" * 5000)
        import os

        stale = time.time() - 999 * 86400
        os.utime(keep, (stale, stale))
        _gc_profile_root(root, max_bytes=1, ttl_days=1, keep="keepme")
        assert keep.exists()


class TestSessionMemoryKeying:
    def test_persona_domain_files_are_distinct(self, tmp_path: Path) -> None:
        from job_ftch.infrastructure.bypass.session_memory import SessionMemory

        a = SessionMemory("p", storage_dir=tmp_path, domain="a.example.com")
        b = SessionMemory("p", storage_dir=tmp_path, domain="b.example.com")
        assert a._storage_path != b._storage_path
        a.state.cookies = [{"name": "cf_clearance", "value": "A"}]
        a.save()
        assert not b._storage_path.exists()

    def test_domain_none_is_backward_compatible(self, tmp_path: Path) -> None:
        from job_ftch.infrastructure.bypass.session_memory import SessionMemory

        legacy = SessionMemory("p", storage_dir=tmp_path)
        assert legacy._storage_path == tmp_path / "p.json"


class TestColdWarmup:
    def test_cold_profile_warms_once(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import job_ftch.infrastructure.bypass.session_memory as sm

        monkeypatch.setattr(sm, "_DEFAULT_STORAGE_DIR", str(tmp_path / "mem"))
        mgr = _mgr(tmp_path)
        assert mgr.is_cold_profile() is True
        prepared = mgr.prepare_browser_config(
            {"persistent_context": True, "url": "https://jobs.example.com/careers/list"}
        )
        assert prepared["warmup_url"] == "https://jobs.example.com/"
        # A retry within the same run must not re-warm.
        again = mgr.prepare_browser_config(
            {"persistent_context": True, "url": "https://jobs.example.com/careers/list"}
        )
        assert "warmup_url" not in again

    def test_warm_profile_skips_warmup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import job_ftch.infrastructure.bypass.session_memory as sm

        monkeypatch.setattr(sm, "_DEFAULT_STORAGE_DIR", str(tmp_path / "mem"))
        mgr = _mgr(tmp_path)
        memory = mgr._persona_session_memory()
        memory.state.visit_count = 3  # returning visitor -> warm
        assert mgr.is_cold_profile() is False
        prepared = mgr.prepare_browser_config(
            {"persistent_context": True, "url": "https://jobs.example.com/careers/list"}
        )
        assert "warmup_url" not in prepared

    def test_root_listing_is_not_warmed_to_itself(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import job_ftch.infrastructure.bypass.session_memory as sm

        monkeypatch.setattr(sm, "_DEFAULT_STORAGE_DIR", str(tmp_path / "mem"))
        mgr = _mgr(tmp_path)
        prepared = mgr.prepare_browser_config(
            {"persistent_context": True, "url": "https://jobs.example.com/"}
        )
        assert "warmup_url" not in prepared


class _FakeRequest:
    def __init__(self, resource_type: str) -> None:
        self.resource_type = resource_type
        self.headers = {"user-agent": "ua"}


class _FakeRoute:
    def __init__(self, resource_type: str) -> None:
        self.request = _FakeRequest(resource_type)
        self.continue_headers: dict[str, Any] | None = None
        self.continued = False

    async def continue_(self, headers: dict[str, Any] | None = None) -> None:
        self.continued = True
        self.continue_headers = headers


class _FakePage:
    def __init__(self) -> None:
        self.handler: Any = None
        self.unrouted = False

    async def route(self, _pattern: str, handler: Any) -> None:
        self.handler = handler

    async def unroute(self, _pattern: str, _handler: Any) -> None:
        self.unrouted = True


class TestRefererScoping:
    @pytest.mark.asyncio
    async def test_referer_only_on_document(self) -> None:
        from job_ftch.infrastructure.bypass.multi_layer_obfuscation import (
            ObfuscationContext,
            ReferrerChainLayer,
        )

        page = _FakePage()
        ctx = ObfuscationContext(
            domain="jobs.example.com",
            persona_name="chrome_146_win_a",
            browser_family="chromium",
            transport="browser",
            page=page,
        )
        result = await ReferrerChainLayer().apply(ctx)
        assert result.applied is True
        assert page.handler is not None

        # Document request gets the forged Referer, then the route removes itself.
        doc = _FakeRoute("document")
        await page.handler(doc)
        assert doc.continue_headers is not None
        assert doc.continue_headers.get("referer") == ctx.metadata["referrer"]["url"]
        assert page.unrouted is True

        # A subresource keeps the browser's native referrer (no injected header).
        sub = _FakeRoute("image")
        await page.handler(sub)
        assert sub.continued is True
        assert sub.continue_headers is None
