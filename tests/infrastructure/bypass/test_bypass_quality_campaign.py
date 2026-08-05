from __future__ import annotations

from scripts.eval.run_bypass_quality_campaign import classify_campaign_result, summarize


def test_campaign_classifies_typed_challenge_without_empty_success() -> None:
    result = classify_campaign_result(
        {
            "url": "https://owned.example.test/jobs",
            "parse_status": "parsed_failed",
            "failure_bucket": "waf_challenge",
            "bypass_route_transitions": [
                {"axis": "engine", "from_engine": "noop", "to_engine": "stealth_browser"}
            ],
            "stats": {
                "detected_captcha_types": ["recaptcha"],
                "challenge_events": [
                    {
                        "surface": "monitor",
                        "type": "recaptcha",
                        "confidence": 0.92,
                        "evidence_hash": "abc123",
                    }
                ],
            },
            "fingerprint_audit": {"coherent": True},
            "parser_outcome": {"items_extracted": 0},
        }
    )

    assert result["outcome"] == "classified_challenge"
    assert result["issues"] == []
    assert result["telemetry"]["steps"] == [
        {
            "tier": "noop",
            "network": None,
            "session": None,
            "challenge_state": None,
            "source": "transition_from",
        },
        {
            "tier": "stealth_browser",
            "network": None,
            "session": None,
            "challenge_state": None,
            "source": "transition_to",
        },
    ]


def test_campaign_reports_final_route_as_ladder_step_when_attempts_are_empty() -> None:
    result = classify_campaign_result(
        {
            "url": "https://owned.example.test/jobs",
            "parse_status": "parsed_ok",
            "item_count": 1,
            "bypass_final_tier": "cloak",
            "bypass_final_network": "direct",
            "bypass_final_session": "sticky",
            "bypass_final_challenge_state": "solving",
            "bypass_attempts": [],
            "bypass_route_transitions": [
                {
                    "axis": "engine",
                    "from_engine": "camoufox",
                    "to_engine": "cloak",
                    "network": "direct",
                    "session": "fresh",
                    "challenge": "none",
                }
            ],
            "stats": {},
            "fingerprint_audit": {"coherent": True},
            "parser_outcome": {"items_extracted": 1},
        }
    )

    assert result["outcome"] == "parsed_ok"
    assert result["telemetry"]["attempts"] == []
    assert result["telemetry"]["steps"] == [
        {
            "tier": "camoufox",
            "network": "direct",
            "session": "fresh",
            "challenge_state": "none",
            "source": "transition_from",
        },
        {
            "tier": "cloak",
            "network": "direct",
            "session": "fresh",
            "challenge_state": "none",
            "source": "transition_to",
        },
    ]


def test_campaign_flags_unknown_challenge_and_no_escalation() -> None:
    result = classify_campaign_result(
        {
            "url": "https://owned.example.test/jobs",
            "parse_status": "parsed_failed",
            "failure_bucket": "waf_challenge",
            "bypass_route_transitions": [],
            "stats": {
                "monitor_failure_without_escalation": 1,
                "challenge_events": [{"surface": "monitor", "type": "unknown"}],
            },
            "fingerprint_audit": {"coherent": True},
            "parser_outcome": {"items_extracted": 0},
        }
    )

    assert result["outcome"] == "classified_challenge"
    assert "unknown_challenge_with_waf_or_captcha_evidence" in result["issues"]
    assert "monitor_failure_without_escalation" in result["issues"]


def test_campaign_summary_fails_on_fingerprint_incoherence() -> None:
    report = summarize(
        [
            {
                "url": "https://owned.example.test/jobs",
                "parse_status": "parsed_ok",
                "item_count": 1,
                "stats": {},
                "fingerprint_audit": {
                    "coherent": False,
                    "issues": ["I1:ua_vs_client_hints"],
                },
                "parser_outcome": {"items_extracted": 1},
            }
        ]
    )

    assert report["passed"] is False
    assert report["issues"] == {"suspicious_fingerprint_incoherence": 1}


def test_campaign_summary_fails_on_deadline_timeout() -> None:
    report = summarize(
        [
            {
                "url": "https://owned.example.test/jobs",
                "parse_status": "parsed_failed",
                "failure_bucket": "timeout_global",
                "deadline_exceeded": True,
                "stats": {},
                "fingerprint_audit": {},
                "parser_outcome": {"items_extracted": 0},
            }
        ]
    )

    assert report["passed"] is False
    assert report["issues"] == {"non_terminal_deadline_or_timeout": 1}
