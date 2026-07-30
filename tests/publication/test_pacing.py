"""Tests for drip-publication pacing."""

from __future__ import annotations

from job_ftch.publication.pacing import Burst, PacingConfig, plan_bursts


class TestPacing:
    def test_empty_queue(self) -> None:
        assert plan_bursts(0) == []

    def test_small_queue(self) -> None:
        bursts = plan_bursts(3)
        total_items = sum(len(b.items) for b in bursts)
        assert total_items == 3

    def test_respects_target_per_window(self) -> None:
        cfg = PacingConfig(target_per_window=5)
        bursts = plan_bursts(20, cfg)
        total_items = sum(len(b.items) for b in bursts)
        assert total_items == 5

    def test_burst_sizes_within_bounds(self) -> None:
        cfg = PacingConfig(burst_min=2, burst_max=4, target_per_window=20)
        bursts = plan_bursts(20, cfg)
        for burst in bursts:
            assert 1 <= len(burst.items) <= 4

    def test_offsets_non_decreasing(self) -> None:
        bursts = plan_bursts(12)
        offsets = [b.offset_seconds for b in bursts]
        for i in range(1, len(offsets)):
            assert offsets[i] >= offsets[i - 1]

    def test_first_burst_starts_at_zero(self) -> None:
        bursts = plan_bursts(5)
        assert bursts[0].offset_seconds == 0.0

    def test_offsets_within_spread_window(self) -> None:
        cfg = PacingConfig(window_seconds=14400, spread_fraction=0.6)
        bursts = plan_bursts(12, cfg)
        max_offset = max(b.offset_seconds for b in bursts)
        assert max_offset <= 14400 * 0.6

    def test_item_indices_are_sequential(self) -> None:
        bursts = plan_bursts(8)
        all_items = []
        for burst in bursts:
            all_items.extend(burst.items)
        assert all_items == list(range(len(all_items)))

    def test_config_defaults(self) -> None:
        cfg = PacingConfig()
        assert cfg.window_seconds == 14400
        assert cfg.target_per_window == 12
        assert cfg.burst_min == 2
        assert cfg.burst_max == 4
        assert cfg.msg_throttle_seconds == 3.5

    def test_burst_dataclass(self) -> None:
        b = Burst(items=[0, 1, 2], offset_seconds=120.0)
        assert len(b.items) == 3
        assert b.offset_seconds == 120.0
