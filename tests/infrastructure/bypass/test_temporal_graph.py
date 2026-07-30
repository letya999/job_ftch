"""Tests for temporal consistency graph (ADR-076)."""

from __future__ import annotations

from job_ftch.infrastructure.bypass.temporal_graph import (
    SessionRecord,
    TemporalConsistencyGraph,
)


def _make_session(
    session_id: str = "s1",
    persona_id: str = "p1",
    domain: str = "example.com",
    timestamp: float = 1000.0,
    page_count: int = 5,
    scroll_depth_avg: float = 0.6,
    dwell_time_seconds: float = 120.0,
    actions: tuple[str, ...] = (),
) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        persona_id=persona_id,
        domain=domain,
        timestamp=timestamp,
        page_count=page_count,
        scroll_depth_avg=scroll_depth_avg,
        dwell_time_seconds=dwell_time_seconds,
        actions=actions,
    )


class TestTemporalConsistencyGraph:
    """Test temporal consistency checks."""

    def test_add_session_stores_record(self) -> None:
        graph = TemporalConsistencyGraph()
        graph.add_session(_make_session())
        assert graph.total_sessions == 1
        assert graph.persona_count == 1

    def test_check_consistency_first_session_is_consistent(self) -> None:
        graph = TemporalConsistencyGraph()
        verdict = graph.check_consistency(_make_session())
        assert verdict.consistent is True
        assert verdict.score == 1.0

    def test_check_consistency_similar_session_passes(self) -> None:
        graph = TemporalConsistencyGraph()
        # Build history with consistent sessions.
        for i in range(5):
            graph.add_session(
                _make_session(
                    session_id=f"s{i}",
                    timestamp=1000.0 + i * 3600,  # 1 hour apart
                    dwell_time_seconds=120.0 + i * 5,
                    page_count=5,
                    scroll_depth_avg=0.6,
                )
            )
        # Check a similar candidate.
        verdict = graph.check_consistency(
            _make_session(
                session_id="candidate",
                timestamp=1000.0 + 6 * 3600,
                dwell_time_seconds=125.0,
                page_count=5,
                scroll_depth_avg=0.6,
            )
        )
        assert verdict.consistent is True
        assert verdict.score > 0.8

    def test_check_consistency_wildly_different_fails(self) -> None:
        graph = TemporalConsistencyGraph()
        # Build history with consistent short sessions.
        for i in range(5):
            graph.add_session(
                _make_session(
                    session_id=f"s{i}",
                    timestamp=1000.0 + i * 3600,
                    dwell_time_seconds=30.0,
                    page_count=3,
                    scroll_depth_avg=0.2,
                )
            )
        # Candidate has wildly different dwell time.
        verdict = graph.check_consistency(
            _make_session(
                session_id="candidate",
                timestamp=1000.0 + 6 * 3600,
                dwell_time_seconds=3600.0,  # 1 hour vs 30 seconds
                page_count=50,
                scroll_depth_avg=0.99,
            )
        )
        assert verdict.consistent is False
        assert len(verdict.violations) > 0

    def test_check_consistency_too_fast_fails(self) -> None:
        graph = TemporalConsistencyGraph()
        graph.add_session(_make_session(session_id="s1", timestamp=1000.0))
        # Session 0.5 seconds later — too fast.
        verdict = graph.check_consistency(
            _make_session(
                session_id="s2",
                timestamp=1000.5,
            )
        )
        assert verdict.consistent is False
        assert any("time_gap" in v for v in verdict.violations)

    def test_get_persona_history_returns_ordered(self) -> None:
        graph = TemporalConsistencyGraph()
        graph.add_session(_make_session(session_id="s3", timestamp=3000))
        graph.add_session(_make_session(session_id="s1", timestamp=1000))
        graph.add_session(_make_session(session_id="s2", timestamp=2000))
        history = graph.get_persona_history("p1")
        timestamps = [s.timestamp for s in history]
        assert timestamps == [1000, 2000, 3000]

    def test_max_sessions_per_persona_evicts_newest(self) -> None:
        graph = TemporalConsistencyGraph(max_sessions_per_persona=3)
        for i in range(5):
            graph.add_session(
                _make_session(
                    session_id=f"s{i}",
                    timestamp=float(i * 100),
                )
            )
        history = graph.get_persona_history("p1")
        assert len(history) == 3
        # Oldest (s0, s1) should be evicted.
        ids = [s.session_id for s in history]
        assert "s0" not in ids
        assert "s1" not in ids

    def test_suggest_parameters_uses_ema(self) -> None:
        graph = TemporalConsistencyGraph()
        for i in range(5):
            graph.add_session(
                _make_session(
                    session_id=f"s{i}",
                    timestamp=float(i * 3600),
                    dwell_time_seconds=100.0 + i * 10,
                    page_count=5 + i,
                    scroll_depth_avg=0.5 + i * 0.05,
                )
            )
        params = graph.suggest_parameters("p1", "example.com")
        assert "dwell_time_seconds" in params
        assert "scroll_depth_avg" in params
        assert "page_count" in params
        # EMA should weight recent values more.
        assert params["dwell_time_seconds"] > 100

    def test_suggest_parameters_unknown_persona(self) -> None:
        graph = TemporalConsistencyGraph()
        params = graph.suggest_parameters("unknown", "example.com")
        # Should return defaults.
        assert params["dwell_time_seconds"] == 120.0

    def test_clear_persona_removes_all(self) -> None:
        graph = TemporalConsistencyGraph()
        for i in range(3):
            graph.add_session(
                _make_session(
                    session_id=f"s{i}",
                    timestamp=float(i * 100),
                )
            )
        assert graph.total_sessions == 3
        graph.clear_persona("p1")
        assert graph.total_sessions == 0
        assert graph.persona_count == 0

    def test_persona_count_and_total_sessions(self) -> None:
        graph = TemporalConsistencyGraph()
        graph.add_session(_make_session(persona_id="p1", timestamp=1000))
        graph.add_session(_make_session(persona_id="p1", timestamp=2000))
        graph.add_session(
            _make_session(
                session_id="s2",
                persona_id="p2",
                timestamp=3000,
            )
        )
        assert graph.persona_count == 2
        assert graph.total_sessions == 3

    def test_get_anomalies_detects_uniform_gaps(self) -> None:
        graph = TemporalConsistencyGraph()
        # All sessions exactly 100 seconds apart (suspiciously uniform).
        for i in range(10):
            graph.add_session(
                _make_session(
                    session_id=f"s{i}",
                    timestamp=1000.0 + i * 100,
                    dwell_time_seconds=50.0,
                )
            )
        anomalies = graph.get_anomalies("p1")
        assert any("uniform_time_gaps" in a for a in anomalies)

    def test_get_anomalies_no_anomalies_with_variance(self) -> None:
        import random

        rng = random.Random(42)
        graph = TemporalConsistencyGraph()
        t = 1000.0
        for i in range(10):
            t += rng.uniform(60, 7200)  # Varied gaps.
            graph.add_session(
                _make_session(
                    session_id=f"s{i}",
                    timestamp=t,
                    dwell_time_seconds=rng.uniform(30, 300),
                )
            )
        anomalies = graph.get_anomalies("p1")
        # Should not flag uniform gaps with varied input.
        assert not any("uniform_time_gaps" in a for a in anomalies)
