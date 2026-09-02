"""Tests for ADR-075 advanced anti-bot evasion techniques.

Tests cover:
1. FingerprintGenerator — exponential fingerprint space
2. TemporalShaper — statistical timing distributions
3. BehavioralNoise — microscopic action noise
4. SessionMemory — persistent session state
5. DistributedSimulator — Poisson traffic simulation
"""

from __future__ import annotations

import tempfile
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

from job_ftch.infrastructure.bypass.behavioral_noise import BehavioralNoise
from job_ftch.infrastructure.bypass.distributed_simulator import DistributedSimulator
from job_ftch.infrastructure.bypass.fingerprint_generator import (
    FingerprintGenerator,
)
from job_ftch.infrastructure.bypass.session_memory import SessionMemory, SessionState
from job_ftch.infrastructure.bypass.temporal_shaper import TemporalShaper


class TestFingerprintGenerator:
    """Test exponential fingerprint space generation."""

    def test_generate_unique_fingerprints(self):
        """Each call generates a unique fingerprint."""
        gen = FingerprintGenerator(seed=42)
        fp1 = gen.generate(os="windows")
        fp2 = gen.generate(os="windows")
        assert fp1.hash() != fp2.hash()

    def test_generate_batch(self):
        """Batch generation produces unique fingerprints."""
        gen = FingerprintGenerator(seed=42)
        fingerprints = gen.generate_batch(100, os="windows")
        hashes = [fp.hash() for fp in fingerprints]
        assert len(set(hashes)) == 100  # All unique

    def test_fingerprint_components(self):
        """Fingerprint has all required components."""
        gen = FingerprintGenerator(seed=42)
        fp = gen.generate(os="windows")

        assert isinstance(fp.canvas_seed, int)
        assert 1000 <= fp.canvas_seed <= 99999
        assert isinstance(fp.audio_seed, int)
        assert 1000 <= fp.audio_seed <= 99999
        assert isinstance(fp.webgl_renderer, str)
        assert isinstance(fp.font_list, list)
        assert len(fp.font_list) >= 10
        assert isinstance(fp.screen_width, int)
        assert isinstance(fp.screen_height, int)
        assert isinstance(fp.battery_charging, bool)
        assert 0.0 <= fp.battery_level <= 1.0
        assert fp.hardware_concurrency in [2, 4, 6, 8, 12, 16]
        assert fp.device_memory in [2, 4, 8, 16]
        assert 1 <= fp.font_spacing_seed <= 999999

    def test_os_specific_fonts(self):
        """Font list is OS-specific."""
        gen = FingerprintGenerator(seed=42)
        fp_win = gen.generate(os="windows")
        fp_mac = gen.generate(os="macos")
        fp_lin = gen.generate(os="linux")

        # All should have fonts
        assert len(fp_win.font_list) > 0
        assert len(fp_mac.font_list) > 0
        assert len(fp_lin.font_list) > 0


class TestTemporalShaper:
    """Test statistical timing distributions."""

    def test_reading_time_lognormal(self):
        """Reading time follows LogNormal distribution."""
        shaper = TemporalShaper(seed=42)
        times = [shaper.reading_time() for _ in range(1000)]

        # LogNormal(μ=3.5, σ=0.8) → median ≈ 33s
        median = sorted(times)[500]
        assert 20 < median < 50  # Reasonable range

        # Most values should be positive
        assert all(t > 0 for t in times)

    def test_thinking_time_pareto(self):
        """Thinking time follows Pareto distribution (heavy-tailed)."""
        shaper = TemporalShaper(seed=42)
        times = [shaper.thinking_time() for _ in range(1000)]

        # Pareto(scale=5, shape=2) → median ≈ 7s
        median = sorted(times)[500]
        assert 5 < median < 15

        # Heavy-tailed: some values should be large
        assert max(times) > 50

    def test_scroll_pause_gamma(self):
        """Scroll pause follows Gamma distribution."""
        shaper = TemporalShaper(seed=42)
        pauses = [shaper.scroll_pause() for _ in range(1000)]

        # Gamma(shape=2, scale=1.5) → mean ≈ 3s
        mean = sum(pauses) / len(pauses)
        assert 2 < mean < 5

        # All positive
        assert all(p > 0 for p in pauses)

    def test_inter_arrival_exponential(self):
        """Inter-arrival follows Exponential distribution."""
        shaper = TemporalShaper(seed=42)
        arrivals = [shaper.inter_arrival(rate_per_minute=10) for _ in range(1000)]

        # Exponential(λ=10/60) → mean ≈ 6s
        mean = sum(arrivals) / len(arrivals)
        assert 4 < mean < 8

    def test_deterministic_with_seed(self):
        """Same seed produces same results."""
        shaper1 = TemporalShaper(seed=42)
        shaper2 = TemporalShaper(seed=42)

        assert shaper1.reading_time() == shaper2.reading_time()
        assert shaper1.thinking_time() == shaper2.thinking_time()


class TestBehavioralNoise:
    """Test microscopic noise injection."""

    def test_mouse_jitter(self):
        """Mouse trajectory gets jitter."""
        noise = BehavioralNoise(seed=42)
        trajectory = [(100, 200), (150, 250), (200, 300)]
        jittered = noise.add_mouse_jitter(trajectory)

        assert len(jittered) == len(trajectory)
        # Should be different (noise added)
        assert jittered != trajectory
        # But close (small jitter)
        for (x1, y1), (x2, y2) in zip(trajectory, jittered, strict=True):
            assert abs(x1 - x2) < 5
            assert abs(y1 - y2) < 5

    def test_typing_delay(self):
        """Typing delay is realistic."""
        noise = BehavioralNoise(seed=42)
        delays = [noise.typing_delay() for _ in range(100)]

        # Mean ≈ 120ms
        mean = sum(delays) / len(delays)
        assert 0.08 < mean < 0.16

        # All positive
        assert all(d > 0 for d in delays)

    def test_click_duration(self):
        """Click duration is realistic."""
        noise = BehavioralNoise(seed=42)
        durations = [noise.click_duration() for _ in range(100)]

        # Uniform(50-150ms)
        assert all(0.05 <= d <= 0.15 for d in durations)

    def test_scroll_amount(self):
        """Scroll amount is realistic."""
        noise = BehavioralNoise(seed=42)
        amounts = [noise.scroll_amount(720) for _ in range(100)]

        # Most should be 200-600px
        normal = [a for a in amounts if 200 <= a <= 600]
        assert len(normal) > 70  # At least 70%

        # Some should be full viewport
        full_viewport = [a for a in amounts if a == 720]
        assert len(full_viewport) > 10  # At least 10%


class TestSessionMemory:
    """Test session state persistence."""

    def test_session_state_serialization(self):
        """Session state can be serialized and deserialized."""
        state = SessionState(
            persona_id="test_persona",
            cookies=[{"name": "cf_clearance", "value": "abc123"}],
            localStorage={"key": "value"},
            visit_count=5,
            last_visit_timestamp=1234567890.0,
        )

        data = state.to_dict()
        restored = SessionState.from_dict(data)

        assert restored.persona_id == state.persona_id
        assert restored.cookies == state.cookies
        assert restored.localStorage == state.localStorage
        assert restored.visit_count == state.visit_count

    def test_session_memory_persistence(self):
        """Session memory persists to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = SessionMemory("test_persona", storage_dir=tmpdir)
            memory.state.visit_count = 5
            memory.state.cookies = [{"name": "test", "value": "value"}]
            memory.save()

            # Load in new instance
            memory2 = SessionMemory("test_persona", storage_dir=tmpdir)
            assert memory2.state.visit_count == 5
            assert len(memory2.state.cookies) == 1

    def test_default_storage_uses_runtime_directory(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        memory = SessionMemory("test_persona")
        memory.save()
        assert (tmp_path / ".runtime/session_memory/test_persona.json").is_file()

    def test_behavioral_profile_update(self):
        """Behavioral profile updates with exponential moving average."""
        memory = SessionMemory("test_persona")
        initial_reading_time = memory.state.avg_reading_time

        memory.update_behavioral_profile(reading_time=60.0, scroll_count=10)

        # Should move towards new values
        assert memory.state.avg_reading_time != initial_reading_time
        assert memory.state.avg_scroll_count > 0


class TestDistributedSimulator:
    """Test Poisson traffic simulation."""

    def test_generate_arrival_times(self):
        """Arrival times follow Poisson process."""
        simulator = DistributedSimulator(target_rpm=10, seed=42)
        arrivals = simulator.generate_arrival_times(100)

        assert len(arrivals) == 100
        # Should be monotonically increasing
        assert all(arrivals[i] < arrivals[i + 1] for i in range(len(arrivals) - 1))

        # Mean inter-arrival ≈ 6s (for 10 rpm)
        inter_arrivals = [arrivals[i + 1] - arrivals[i] for i in range(len(arrivals) - 1)]
        mean_inter = sum(inter_arrivals) / len(inter_arrivals)
        assert 4 < mean_inter < 8

    def test_estimate_completion_time(self):
        """Completion time estimation is reasonable."""
        simulator = DistributedSimulator(target_rpm=10, seed=42)
        estimated = simulator.estimate_completion_time(100)

        # 100 tasks at 10 rpm ≈ 10 minutes = 600s
        assert 400 < estimated < 800

    @pytest.mark.asyncio
    async def test_execute_with_poisson_timing(self):
        """Tasks execute with Poisson timing."""
        simulator = DistributedSimulator(target_rpm=60, seed=42)  # 1 per second

        executed: list[int] = []

        async def task() -> int:
            executed.append(len(executed))
            return len(executed)

        tasks: list[Callable[[], Awaitable[Any]]] = [task for _ in range(5)]
        results = await simulator.execute_with_poisson_timing(tasks, max_concurrent=2)

        assert len(results) == 5
        assert len(executed) == 5

    @pytest.mark.asyncio
    async def test_execute_with_progress_callback(self):
        """Progress callback is called."""
        simulator = DistributedSimulator(target_rpm=60, seed=42)

        progress_calls: list[tuple[int, int]] = []

        async def task() -> str:
            return "done"

        async def progress(completed: int, total: int) -> None:
            progress_calls.append((completed, total))

        tasks: list[Callable[[], Awaitable[Any]]] = [task for _ in range(3)]
        await simulator.execute_with_poisson_timing(
            tasks, max_concurrent=1, progress_callback=progress
        )

        assert len(progress_calls) == 3
        assert progress_calls[-1] == (3, 3)
