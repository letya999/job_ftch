"""Tests for ADR-076 cognitive state machine."""

from __future__ import annotations

from job_ftch.infrastructure.bypass.cognitive_state import (
    BehaviorParams,
    CognitiveState,
    CognitiveStateMachine,
    UserEvent,
)


class TestCognitiveStateMachine:
    """Test cognitive state machine transitions."""

    def test_initial_state_is_reading(self):
        """Initial state is READING."""
        machine = CognitiveStateMachine(seed=42)
        assert machine.current_state == CognitiveState.READING

    def test_long_pause_transitions_to_distracted(self):
        """Long pause (>10s) transitions to DISTRACTED."""
        machine = CognitiveStateMachine(seed=42)
        event = UserEvent(event_type="long_pause", duration=15.0)
        new_state = machine.transition(event)
        assert new_state == CognitiveState.DISTRACTED

    def test_medium_pause_transitions_to_thinking(self):
        """Medium pause (5-10s) transitions to THINKING."""
        machine = CognitiveStateMachine(seed=42)
        event = UserEvent(event_type="long_pause", duration=7.0)
        new_state = machine.transition(event)
        assert new_state == CognitiveState.THINKING

    def test_fast_scroll_transitions_to_scanning(self):
        """Fast scroll (>500px) transitions to SCANNING."""
        machine = CognitiveStateMachine(seed=42)
        event = UserEvent(event_type="fast_scroll", magnitude=600.0)
        new_state = machine.transition(event)
        assert new_state == CognitiveState.SCANNING

    def test_normal_scroll_stays_in_reading(self):
        """Normal scroll stays in READING."""
        machine = CognitiveStateMachine(seed=42)
        event = UserEvent(event_type="scroll", magnitude=200.0)
        new_state = machine.transition(event)
        assert new_state == CognitiveState.READING

    def test_error_transitions_to_distracted(self):
        """Error transitions to DISTRACTED."""
        machine = CognitiveStateMachine(seed=42)
        event = UserEvent(event_type="error")
        new_state = machine.transition(event)
        assert new_state == CognitiveState.DISTRACTED

    def test_click_does_not_change_state(self):
        """Click does not change state."""
        machine = CognitiveStateMachine(seed=42)
        initial_state = machine.current_state
        event = UserEvent(event_type="click")
        new_state = machine.transition(event)
        assert new_state == initial_state

    def test_get_behavior_params_returns_params(self):
        """Get behavior params returns BehaviorParams."""
        machine = CognitiveStateMachine(seed=42)
        params = machine.get_behavior_params()
        assert isinstance(params, BehaviorParams)
        assert params.scroll_speed > 0
        assert params.mouse_speed > 0

    def test_behavior_params_vary_by_state(self):
        """Behavior params vary by cognitive state."""
        machine = CognitiveStateMachine(seed=42)

        # Reading state
        reading_params = machine.get_behavior_params()

        # Transition to scanning
        machine.transition(UserEvent(event_type="fast_scroll", magnitude=600.0))
        scanning_params = machine.get_behavior_params()

        # Scanning should have faster scroll speed
        assert scanning_params.scroll_speed > reading_params.scroll_speed

    def test_update_duration(self):
        """Update duration tracks time in state."""
        machine = CognitiveStateMachine(seed=42)
        machine.update_duration(5.0)
        history = machine.get_state_history()
        assert history["state_duration"] == 5.0

    def test_auto_transition_after_long_distraction(self):
        """Auto-transition after long distraction (>30s)."""
        machine = CognitiveStateMachine(seed=42)
        machine.force_state(CognitiveState.DISTRACTED)
        machine.update_duration(35.0)
        assert machine.current_state == CognitiveState.READING

    def test_force_state(self):
        """Force state transitions to specific state."""
        machine = CognitiveStateMachine(seed=42)
        machine.force_state(CognitiveState.TIRED)
        assert machine.current_state == CognitiveState.TIRED

    def test_get_state_history(self):
        """Get state history returns dict."""
        machine = CognitiveStateMachine(seed=42)
        history = machine.get_state_history()
        assert "current_state" in history
        assert "state_duration" in history
        assert "transition_count" in history

    def test_deterministic_with_seed(self):
        """Same seed produces same transitions."""
        machine1 = CognitiveStateMachine(seed=42)
        machine2 = CognitiveStateMachine(seed=42)

        event = UserEvent(event_type="long_pause", duration=15.0)
        state1 = machine1.transition(event)
        state2 = machine2.transition(event)

        assert state1 == state2


class TestBehaviorParams:
    """Test behavior parameters for different states."""

    def test_reading_params(self):
        """Reading state has slow scroll, long pauses."""
        machine = CognitiveStateMachine(seed=42)
        machine.force_state(CognitiveState.READING)
        params = machine.get_behavior_params()

        assert params.scroll_speed < 200  # Slow
        assert params.scroll_pause_mean > 3  # Long pauses

    def test_scanning_params(self):
        """Scanning state has fast scroll, short pauses."""
        machine = CognitiveStateMachine(seed=42)
        machine.force_state(CognitiveState.SCANNING)
        params = machine.get_behavior_params()

        assert params.scroll_speed > 400  # Fast
        assert params.scroll_pause_mean < 2  # Short pauses

    def test_thinking_params(self):
        """Thinking state has no scroll, long pauses."""
        machine = CognitiveStateMachine(seed=42)
        machine.force_state(CognitiveState.THINKING)
        params = machine.get_behavior_params()

        assert params.scroll_speed == 0  # No scroll
        assert params.scroll_pause_mean > 5  # Long pauses

    def test_distracted_params(self):
        """Distracted state has no interaction, very long pauses."""
        machine = CognitiveStateMachine(seed=42)
        machine.force_state(CognitiveState.DISTRACTED)
        params = machine.get_behavior_params()

        assert params.scroll_speed == 0  # No interaction
        assert params.scroll_pause_mean > 10  # Very long pauses

    def test_tired_params(self):
        """Tired state has slower movement, less precision."""
        machine = CognitiveStateMachine(seed=42)
        machine.force_state(CognitiveState.TIRED)
        params = machine.get_behavior_params()

        assert params.mouse_precision < 0.8  # Less precise
        assert params.session_duration_factor < 1.0  # Shorter sessions
