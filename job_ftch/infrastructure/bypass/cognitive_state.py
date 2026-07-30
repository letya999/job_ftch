"""Cognitive state machine for realistic behavioral simulation.

Models cognitive state transitions that affect behavioral parameters:
- READING: slow scroll, long pauses, precise mouse movement
- SCANNING: fast scroll, short pauses, quick mouse movement
- THINKING: no scroll, medium pauses, mouse hovering
- DISTRACTED: no interaction, long pauses
- TIRED: slower movement, more errors, shorter sessions

Real users exhibit cognitive state changes that affect interaction
patterns. Anti-bot systems detect uniform behavioral patterns.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Any


class CognitiveState(Enum):
    """Cognitive states affecting behavioral parameters."""

    READING = "reading"
    SCANNING = "scanning"
    THINKING = "thinking"
    DISTRACTED = "distracted"
    TIRED = "tired"


@dataclass(frozen=True, slots=True)
class BehaviorParams:
    """Behavioral parameters for a cognitive state."""

    scroll_speed: float  # Pixels per second
    scroll_pause_mean: float  # Seconds between scrolls
    scroll_pause_std: float  # Standard deviation
    mouse_speed: float  # Pixels per second
    mouse_precision: float  # 0.0 to 1.0 (1.0 = perfect)
    click_delay_mean: float  # Seconds before click
    click_delay_std: float  # Standard deviation
    typing_speed: float  # Characters per second
    typing_variance: float  # Variance in typing speed
    session_duration_factor: float  # Multiplier for session length


# Default behavior parameters for each cognitive state
_STATE_PARAMS: dict[CognitiveState, BehaviorParams] = {
    CognitiveState.READING: BehaviorParams(
        scroll_speed=100.0,  # Slow scroll
        scroll_pause_mean=5.0,  # Long pauses
        scroll_pause_std=2.0,
        mouse_speed=200.0,  # Precise movement
        mouse_precision=0.95,
        click_delay_mean=0.3,
        click_delay_std=0.1,
        typing_speed=3.0,  # Slow typing
        typing_variance=0.5,
        session_duration_factor=1.2,  # Longer sessions
    ),
    CognitiveState.SCANNING: BehaviorParams(
        scroll_speed=500.0,  # Fast scroll
        scroll_pause_mean=1.0,  # Short pauses
        scroll_pause_std=0.5,
        mouse_speed=600.0,  # Quick movement
        mouse_precision=0.80,
        click_delay_mean=0.1,
        click_delay_std=0.05,
        typing_speed=6.0,  # Fast typing
        typing_variance=1.0,
        session_duration_factor=0.8,  # Shorter sessions
    ),
    CognitiveState.THINKING: BehaviorParams(
        scroll_speed=0.0,  # No scroll
        scroll_pause_mean=8.0,  # Long pauses
        scroll_pause_std=3.0,
        mouse_speed=100.0,  # Slow hovering
        mouse_precision=0.90,
        click_delay_mean=1.0,  # Long delay before click
        click_delay_std=0.5,
        typing_speed=2.0,  # Very slow typing
        typing_variance=0.8,
        session_duration_factor=1.0,
    ),
    CognitiveState.DISTRACTED: BehaviorParams(
        scroll_speed=0.0,  # No interaction
        scroll_pause_mean=15.0,  # Very long pauses
        scroll_pause_std=5.0,
        mouse_speed=50.0,  # Very slow
        mouse_precision=0.60,
        click_delay_mean=2.0,  # Very long delay
        click_delay_std=1.0,
        typing_speed=1.0,  # Very slow typing
        typing_variance=1.5,
        session_duration_factor=0.5,  # Much shorter sessions
    ),
    CognitiveState.TIRED: BehaviorParams(
        scroll_speed=80.0,  # Slower scroll
        scroll_pause_mean=4.0,  # Medium pauses
        scroll_pause_std=1.5,
        mouse_speed=150.0,  # Slower movement
        mouse_precision=0.70,  # Less precise
        click_delay_mean=0.5,
        click_delay_std=0.3,
        typing_speed=2.5,  # Slower typing
        typing_variance=1.2,  # More variance
        session_duration_factor=0.7,  # Shorter sessions
    ),
}


@dataclass
class UserEvent:
    """User event triggering state transition."""

    event_type: str  # "scroll", "click", "pause", "fast_scroll", "long_pause", "error"
    duration: float = 0.0  # Event duration in seconds
    magnitude: float = 0.0  # Event magnitude (e.g., scroll distance)


class CognitiveStateMachine:
    """Model cognitive state transitions affecting behavioral parameters.

    Usage:
        machine = CognitiveStateMachine(seed=42)
        machine.transition(UserEvent(event_type="long_pause", duration=10.0))
        params = machine.get_behavior_params()
        # params reflects new cognitive state
    """

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._current_state = CognitiveState.READING
        self._state_duration = 0.0  # Time in current state
        self._transition_count = 0

    @property
    def current_state(self) -> CognitiveState:
        """Get current cognitive state."""
        return self._current_state

    def transition(self, event: UserEvent) -> CognitiveState:
        """Transition to new state based on user event.

        Args:
            event: User event triggering transition

        Returns:
            New cognitive state
        """
        new_state = self._current_state
        new_state = self._determine_new_state(event)

        if new_state != new_state:
            self._transition_count += 1

        self._current_state = new_state
        self._state_duration = 0.0
        return new_state

    def _determine_new_state(self, event: UserEvent) -> CognitiveState:
        """Determine new state based on event."""
        # State transition rules
        if event.event_type == "long_pause":
            if event.duration > 10.0:
                return CognitiveState.DISTRACTED
            elif event.duration > 5.0:
                return CognitiveState.THINKING
            else:
                return self._current_state

        elif event.event_type == "fast_scroll":
            if event.magnitude > 500:
                return CognitiveState.SCANNING
            else:
                return CognitiveState.READING

        elif event.event_type == "scroll":
            return CognitiveState.READING

        elif event.event_type == "error":
            # Errors cause distraction
            return CognitiveState.DISTRACTED

        elif event.event_type == "click":
            # Clicks don't change state
            return self._current_state

        else:
            # Random transition with low probability
            if self._rng.random() < 0.1:
                return self._rng.choice(list(CognitiveState))
            return self._current_state

    def get_behavior_params(self) -> BehaviorParams:
        """Get behavioral parameters for current state."""
        return _STATE_PARAMS[self._current_state]

    def update_duration(self, delta_time: float) -> None:
        """Update time spent in current state.

        Args:
            delta_time: Time elapsed since last update (seconds)
        """
        self._state_duration += delta_time

        # Auto-transition after long duration in certain states
        if self._current_state == CognitiveState.DISTRACTED and self._state_duration > 30.0:
            # Return to reading after long distraction
            self._current_state = CognitiveState.READING
            self._state_duration = 0.0
            self._transition_count += 1

        elif self._current_state == CognitiveState.TIRED and self._state_duration > 600.0:
            # After 10 minutes of tired state, session likely ends
            # But we'll just reset to reading for now
            self._current_state = CognitiveState.READING
            self._state_duration = 0.0
            self._transition_count += 1

    def get_state_history(self) -> dict[str, Any]:
        """Get state machine history for debugging."""
        return {
            "current_state": self._current_state.value,
            "state_duration": self._state_duration,
            "transition_count": self._transition_count,
        }

    def force_state(self, state: CognitiveState) -> None:
        """Force transition to specific state (for testing)."""
        self._current_state = state
        self._state_duration = 0.0
        self._transition_count += 1
