"""Integration test: fingerprint coherence across personas (Wave 0.1).

Validates that:
- Each persona produces a consistent fingerprint (no tamper detection)
- UA ↔ GPU ↔ fonts ↔ tz are internally coherent
- No more than 2 personas share the same hardware tuple
- Baseline store round-trips correctly

Marked @pytest.mark.network for the live-browser probe variant;
the static variant runs in CI without network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from job_ftch.infrastructure.bypass.fingerprint_baseline import (
    BaselineRecord,
    FingerprintBaselineStore,
    compare_fingerprint,
    pairwise_hardware_duplicates,
)
from job_ftch.infrastructure.bypass.persona import PERSONA_POOL

if TYPE_CHECKING:
    from pathlib import Path


class TestBaselineStoreFileFallback:
    """Baseline store works with file fallback (no PG)."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> FingerprintBaselineStore:
        return FingerprintBaselineStore(fallback_dir=tmp_path)

    @pytest.mark.asyncio
    async def test_save_and_load_round_trip(self, store: FingerprintBaselineStore) -> None:
        record = BaselineRecord(
            persona_name="test_persona",
            scope="fingerprint",
            generated_at="2026-07-21T00:00:00Z",
            payload={"hardware_concurrency": 8, "device_memory": 8},
        )
        await store.save(record)
        loaded = await store.load("test_persona", "fingerprint")
        assert loaded is not None
        assert loaded.persona_name == "test_persona"
        assert loaded.payload["hardware_concurrency"] == 8

    @pytest.mark.asyncio
    async def test_load_missing_returns_none(self, store: FingerprintBaselineStore) -> None:
        loaded = await store.load("nonexistent", "fingerprint")
        assert loaded is None

    @pytest.mark.asyncio
    async def test_save_creates_directory(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "nested"
        store = FingerprintBaselineStore(fallback_dir=nested)
        record = BaselineRecord(
            persona_name="p1",
            scope="tls",
            generated_at="2026-07-21T00:00:00Z",
            payload={"ja4": "test"},
        )
        await store.save(record)
        assert (nested / "tls_p1.json").exists()


class TestBaselineRecordSerialization:
    """BaselineRecord JSON round-trips correctly."""

    def test_to_json_and_from_json(self) -> None:
        record = BaselineRecord(
            persona_name="chrome_131_win",
            scope="fingerprint",
            generated_at="2026-07-21T00:00:00Z",
            payload={
                "hardware_concurrency": 8,
                "webgl_renderer": "ANGLE (Intel)",
                "tamper_detected": False,
            },
        )
        raw = record.to_json()
        restored = BaselineRecord.from_json(raw)
        assert restored.persona_name == record.persona_name
        assert restored.scope == record.scope
        assert restored.payload == record.payload

    def test_key_format(self) -> None:
        record = BaselineRecord(
            persona_name="test",
            scope="tls",
            generated_at="",
            payload={},
        )
        assert record.key == "fp_baseline:tls:test"


class TestCompareFingerprint:
    """compare_fingerprint detects regressions."""

    def test_identical_fingerprints_match(self) -> None:
        payload = {
            "hardware_concurrency": 8,
            "device_memory": 8,
            "webgl_renderer": "ANGLE (Intel)",
            "tamper_detected": False,
        }
        baseline = BaselineRecord(
            persona_name="p1",
            scope="fingerprint",
            generated_at="",
            payload=payload,
        )
        diff = compare_fingerprint(baseline, dict(payload))
        assert diff.matched is True
        assert not diff.diffs

    def test_hardware_concurrency_mismatch_detected(self) -> None:
        baseline = BaselineRecord(
            persona_name="p1",
            scope="fingerprint",
            generated_at="",
            payload={"hardware_concurrency": 8},
        )
        live = {"hardware_concurrency": 4}
        diff = compare_fingerprint(baseline, live)
        assert diff.matched is False
        assert "hardware_concurrency" in diff.diffs

    def test_tamper_detected_is_flagged(self) -> None:
        baseline = BaselineRecord(
            persona_name="p1",
            scope="fingerprint",
            generated_at="",
            payload={"tamper_detected": False},
        )
        live = {"tamper_detected": True}
        diff = compare_fingerprint(baseline, live)
        assert diff.matched is False
        assert "tamper_detected" in diff.diffs

    def test_webgl_renderer_drift_detected(self) -> None:
        baseline = BaselineRecord(
            persona_name="p1",
            scope="fingerprint",
            generated_at="",
            payload={"webgl_renderer": "ANGLE (Intel UHD 630)"},
        )
        live = {"webgl_renderer": "Google SwiftShader"}
        diff = compare_fingerprint(baseline, live)
        assert diff.matched is False
        assert "webgl_renderer" in diff.diffs


class TestPairwiseHardwareDuplicates:
    """No more than 2 personas should share the same hardware tuple."""

    def test_no_duplicates_in_persona_pool(self) -> None:
        records = [
            {
                "persona_name": p.name,
                "browser_family": p.browser_family,
                "hardware_concurrency": p.hardware_concurrency,
                "device_memory": p.device_memory,
                "webgl_renderer": p.webgl_renderer,
            }
            for p in PERSONA_POOL
        ]
        dupes = pairwise_hardware_duplicates(records)
        assert dupes == [], (
            f"Hardware tuple shared by >2 personas: {dupes}. "
            f"Each (family, cores, memory, renderer) combo should be unique "
            f"across the persona pool to avoid fingerprint clustering."
        )

    def test_detects_triplicate(self) -> None:
        records = [
            {
                "persona_name": f"p{i}",
                "browser_family": "chromium",
                "hardware_concurrency": 8,
                "device_memory": 8,
                "webgl_renderer": "ANGLE",
            }
            for i in range(3)
        ]
        dupes = pairwise_hardware_duplicates(records)
        assert len(dupes) == 1
        assert len(dupes[0]) == 3


class TestPersonaCoherence:
    """Static coherence checks on the persona pool."""

    def test_ua_matches_browser_family(self) -> None:
        for p in PERSONA_POOL:
            if p.browser_family == "chromium":
                assert "Chrome/" in p.ua or "Chromium/" in p.ua, (
                    f"{p.name}: chromium persona should have Chrome in UA"
                )
            elif p.browser_family == "firefox":
                assert "Firefox/" in p.ua, f"{p.name}: firefox persona should have Firefox in UA"
            elif p.browser_family == "safari":
                assert "Safari/" in p.ua, f"{p.name}: safari persona should have Safari in UA"

    def test_viewport_within_screen(self) -> None:
        for p in PERSONA_POOL:
            assert p.viewport_width <= p.screen_width, f"{p.name}: viewport_width > screen_width"
            assert p.viewport_height <= p.screen_height, (
                f"{p.name}: viewport_height > screen_height"
            )

    def test_chromium_has_sec_ch_ua(self) -> None:
        for p in PERSONA_POOL:
            if p.browser_family == "chromium":
                assert p.sec_ch_ua, f"{p.name}: chromium must have sec_ch_ua"
                assert p.sec_ch_ua_platform, f"{p.name}: chromium must have platform"

    def test_chromium_has_high_entropy_hints(self) -> None:
        for p in PERSONA_POOL:
            if p.browser_family == "chromium":
                assert p.platform_version, f"{p.name}: missing platform_version"
                assert p.architecture, f"{p.name}: missing architecture"
                assert p.bitness, f"{p.name}: missing bitness"

    def test_timezone_locale_consistency(self) -> None:
        # Basic check: US personas should have en-US locale and America/ timezone
        for p in PERSONA_POOL:
            if p.locale.startswith("en-US"):
                assert p.timezone.startswith("America/"), (
                    f"{p.name}: en-US locale but timezone is {p.timezone}"
                )

    def test_hardware_concurrency_reasonable(self) -> None:
        for p in PERSONA_POOL:
            assert p.hardware_concurrency in {2, 4, 6, 8, 10, 12, 16, 20, 24, 32}, (
                f"{p.name}: unusual hardware_concurrency={p.hardware_concurrency}"
            )
            assert p.device_memory in {2, 4, 8, 16, 32, 64}, (
                f"{p.name}: unusual device_memory={p.device_memory}"
            )

    def test_canvas_seeds_unique(self) -> None:
        seeds = [p.canvas_seed for p in PERSONA_POOL]
        assert len(seeds) == len(set(seeds)), "Canvas seeds should be unique per persona"
