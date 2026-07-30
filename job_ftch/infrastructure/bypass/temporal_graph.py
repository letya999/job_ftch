"""Temporal consistency graph (ADR-076).

Dict-based graph tracking session history per persona. Ensures that
returning-user sessions are temporally coherent: similar dwell times,
action patterns, and time gaps.

Anti-bot systems track returning users and flag sessions that deviate
wildly from a persona's established behavioural baseline.
"""

from __future__ import annotations

import random
import statistics
from collections import defaultdict
from dataclasses import dataclass

import structlog

logger = structlog.get_logger("job_ftch.bypass.temporal_graph")


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """A single session observation for a persona on a domain."""

    session_id: str
    persona_id: str
    domain: str
    timestamp: float  # epoch seconds or monotonic
    page_count: int = 0
    scroll_depth_avg: float = 0.0
    dwell_time_seconds: float = 0.0
    actions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConsistencyVerdict:
    """Result of checking a candidate session against persona history."""

    consistent: bool
    score: float  # 0.0 (inconsistent) to 1.0 (perfect match)
    violations: tuple[str, ...] = ()


class TemporalConsistencyGraph:
    """Track and validate temporal consistency of persona sessions.

    Usage::

        graph = TemporalConsistencyGraph()
        graph.add_session(SessionRecord(session_id="s1", persona_id="p1",
                                        domain="example.com", timestamp=1000,
                                        dwell_time_seconds=120))
        verdict = graph.check_consistency(
            SessionRecord(session_id="s2", persona_id="p1",
                          domain="example.com", timestamp=2000,
                          dwell_time_seconds=130)
        )
        assert verdict.consistent
    """

    def __init__(
        self,
        *,
        max_sessions_per_persona: int = 50,
        seed: int | None = None,
    ) -> None:
        self._max_sessions = max_sessions_per_persona
        self._rng = random.Random(seed)
        # persona_id -> list[SessionRecord], ordered by timestamp.
        self._sessions: dict[str, list[SessionRecord]] = defaultdict(list)

    def add_session(self, record: SessionRecord) -> None:
        """Store a session observation for a persona."""
        history = self._sessions[record.persona_id]
        history.append(record)
        # Keep sorted by timestamp.
        history.sort(key=lambda s: s.timestamp)
        # Evict newest if over limit.
        while len(history) > self._max_sessions:
            history.pop(0)

    def check_consistency(self, candidate: SessionRecord) -> ConsistencyVerdict:
        """Check if a candidate session is consistent with persona history.

        A session is consistent if:
        1. Time gap since last session is >= 1 second (not inhumanly fast).
        2. Dwell time is within 2σ of the persona's historical mean.
        3. Page count is within 2σ of the persona's domain-specific mean.
        4. Scroll depth is within 2σ of the persona's domain-specific mean.
        """
        history = self._sessions.get(candidate.persona_id, [])

        # First session for persona: always consistent.
        if not history:
            return ConsistencyVerdict(consistent=True, score=1.0)

        violations: list[str] = []
        scores: list[float] = []

        # 1. Time gap check.
        last = history[-1]
        gap = candidate.timestamp - last.timestamp
        if gap < 1.0:
            violations.append(f"time_gap_too_small: {gap:.2f}s (minimum 1s)")
            scores.append(0.0)
        else:
            scores.append(1.0)

        # 2. Dwell time consistency.
        dwell_times = [s.dwell_time_seconds for s in history if s.dwell_time_seconds > 0]
        if len(dwell_times) >= 2:
            mean_dwell = statistics.mean(dwell_times)
            std_dwell = statistics.stdev(dwell_times)
            if std_dwell > 0:
                z = abs(candidate.dwell_time_seconds - mean_dwell) / std_dwell
                if z > 2.0:
                    violations.append(
                        f"dwell_time_outlier: z={z:.1f} "
                        f"(value={candidate.dwell_time_seconds:.0f}s, "
                        f"mean={mean_dwell:.0f}s, std={std_dwell:.0f}s)"
                    )
                    scores.append(max(0.0, 1.0 - (z - 2.0) / 2.0))
                else:
                    scores.append(1.0)
            elif abs(candidate.dwell_time_seconds - mean_dwell) > mean_dwell * 0.5:
                # Zero stdev but candidate differs by >50% from the constant.
                violations.append(
                    f"dwell_time_outlier: constant_history={mean_dwell:.0f}s, "
                    f"value={candidate.dwell_time_seconds:.0f}s"
                )
                scores.append(0.0)
            else:
                scores.append(1.0)
        else:
            scores.append(1.0)

        # 3. Page count consistency (domain-specific).
        domain_sessions = [s for s in history if s.domain == candidate.domain and s.page_count > 0]
        if len(domain_sessions) >= 2:
            page_counts = [s.page_count for s in domain_sessions]
            mean_pages = statistics.mean(page_counts)
            std_pages = statistics.stdev(page_counts)
            if std_pages > 0 and candidate.page_count > 0:
                z = abs(candidate.page_count - mean_pages) / std_pages
                if z > 2.0:
                    violations.append(
                        f"page_count_outlier: z={z:.1f} "
                        f"(value={candidate.page_count}, mean={mean_pages:.0f})"
                    )
                    scores.append(max(0.0, 1.0 - (z - 2.0) / 2.0))
                else:
                    scores.append(1.0)
            elif std_pages == 0 and candidate.page_count > 0 and mean_pages > 0:
                if abs(candidate.page_count - mean_pages) > mean_pages * 0.5:
                    violations.append(
                        f"page_count_outlier: constant_history={mean_pages:.0f}, "
                        f"value={candidate.page_count}"
                    )
                    scores.append(0.0)
                else:
                    scores.append(1.0)
            else:
                scores.append(1.0)
        else:
            scores.append(1.0)

        # 4. Scroll depth consistency (domain-specific).
        if len(domain_sessions) >= 2:
            scroll_depths = [s.scroll_depth_avg for s in domain_sessions if s.scroll_depth_avg > 0]
            if len(scroll_depths) >= 2:
                mean_scroll = statistics.mean(scroll_depths)
                std_scroll = statistics.stdev(scroll_depths)
                if std_scroll > 0 and candidate.scroll_depth_avg > 0:
                    z = abs(candidate.scroll_depth_avg - mean_scroll) / std_scroll
                    if z > 2.0:
                        violations.append(f"scroll_depth_outlier: z={z:.1f}")
                        scores.append(max(0.0, 1.0 - (z - 2.0) / 2.0))
                    else:
                        scores.append(1.0)
                elif std_scroll == 0 and candidate.scroll_depth_avg > 0 and mean_scroll > 0:
                    if abs(candidate.scroll_depth_avg - mean_scroll) > mean_scroll * 0.5:
                        violations.append(
                            f"scroll_depth_outlier: constant_history={mean_scroll:.2f}, "
                            f"value={candidate.scroll_depth_avg:.2f}"
                        )
                        scores.append(0.0)
                    else:
                        scores.append(1.0)
                else:
                    scores.append(1.0)
            else:
                scores.append(1.0)
        else:
            scores.append(1.0)

        overall_score = statistics.mean(scores) if scores else 1.0
        consistent = len(violations) == 0

        return ConsistencyVerdict(
            consistent=consistent,
            score=round(overall_score, 3),
            violations=tuple(violations),
        )

    def get_persona_history(self, persona_id: str) -> list[SessionRecord]:
        """Return all sessions for a persona, ordered by timestamp."""
        return list(self._sessions.get(persona_id, []))

    def get_anomalies(self, persona_id: str) -> list[str]:
        """Detect anomalies in a persona's session history."""
        history = self._sessions.get(persona_id, [])
        if len(history) < 3:
            return []

        anomalies: list[str] = []

        # Check for time gaps that are too uniform (bot-like).
        gaps = [history[i + 1].timestamp - history[i].timestamp for i in range(len(history) - 1)]
        if len(gaps) >= 3:
            cv = statistics.stdev(gaps) / statistics.mean(gaps) if statistics.mean(gaps) > 0 else 0
            if cv < 0.05:
                anomalies.append(f"uniform_time_gaps: cv={cv:.3f} (suspiciously regular)")

        # Check for identical dwell times.
        dwell_times = [s.dwell_time_seconds for s in history if s.dwell_time_seconds > 0]
        if len(dwell_times) >= 3:
            unique_ratio = len(set(dwell_times)) / len(dwell_times)
            if unique_ratio < 0.3:
                anomalies.append(f"low_dwell_variance: unique_ratio={unique_ratio:.2f}")

        return anomalies

    def suggest_parameters(
        self,
        persona_id: str,
        domain: str,
    ) -> dict[str, float]:
        """Suggest session parameters based on persona history (EMA).

        Returns recommended dwell_time, scroll_depth, page_count
        based on exponential moving average of past sessions.
        """
        history = self._sessions.get(persona_id, [])
        domain_sessions = [s for s in history if s.domain == domain]

        if not domain_sessions:
            # Default suggestions for unknown persona/domain.
            return {
                "dwell_time_seconds": 120.0,
                "scroll_depth_avg": 0.6,
                "page_count": 5.0,
            }

        alpha = 0.3  # EMA smoothing factor.
        dwell = self._ema([s.dwell_time_seconds for s in domain_sessions], alpha)
        scroll = self._ema([s.scroll_depth_avg for s in domain_sessions], alpha)
        pages = self._ema([float(s.page_count) for s in domain_sessions], alpha)

        return {
            "dwell_time_seconds": round(dwell, 1),
            "scroll_depth_avg": round(scroll, 3),
            "page_count": round(pages, 1),
        }

    def clear_persona(self, persona_id: str) -> None:
        """Remove all session history for a persona."""
        self._sessions.pop(persona_id, None)

    @property
    def persona_count(self) -> int:
        return len(self._sessions)

    @property
    def total_sessions(self) -> int:
        return sum(len(v) for v in self._sessions.values())

    @staticmethod
    def _ema(values: list[float], alpha: float) -> float:
        """Exponential moving average."""
        if not values:
            return 0.0
        result = values[0]
        for v in values[1:]:
            result = alpha * v + (1 - alpha) * result
        return result
