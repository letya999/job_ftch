from __future__ import annotations

from types import SimpleNamespace

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.composite import SourceFetchResult, _capture_source_stats


def _board_gone_source() -> SimpleNamespace:
    return SimpleNamespace(
        stats=SimpleNamespace(
            zero_reason=SimpleNamespace(value="board_gone"),
            source_partial=False,
            truncated=False,
            monitor_truncated=0,
        )
    )


def test_board_gone_survives_composite_as_primary_outcome() -> None:
    result = SourceFetchResult(
        source_id="career_site:gone",
        source_kind="career_site",
        source_name="gone",
    )
    _capture_source_stats(_board_gone_source(), result)
    assert result.zero_reason == "board_gone"
    assert result.terminal_outcome == "board_gone"
    assert not result.deadline_exceeded


def test_primary_outcome_and_deadline_flags_are_independent() -> None:
    result = SourceFetchResult(
        source_id="career_site:gone",
        source_kind="career_site",
        source_name="gone",
        soft_deadline_hit=True,
    )
    _capture_source_stats(_board_gone_source(), result)
    assert result.terminal_outcome == "board_gone"
    assert result.soft_deadline_hit
    assert not result.hard_deadline_hit


def test_bypass_config_accepts_typed_nested_json_values() -> None:
    spec = CareerSiteSpec(
        url="https://example.test/jobs",
        bypass_config={
            "allow_adr_073": False,
            "captcha_max_attempts": 2,
            "providers": ["browser_wait", "capsolver"],
            "limits": {"cost": 1.5, "enabled": True},
        },
    )
    assert spec.bypass_config["allow_adr_073"] is False
    assert spec.bypass_config["limits"] == {"cost": 1.5, "enabled": True}
