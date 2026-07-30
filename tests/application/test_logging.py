from __future__ import annotations

from job_ftch.application.logging import _mask_sensitive


def test_structured_log_payload_redacts_nested_credentials() -> None:
    payload = _mask_sensitive(
        None,
        "info",
        {
            "headers": {"Authorization": "Bearer secret"},
            "request": {"access_token": "secret", "status": 200},
        },
    )

    assert payload == {"headers": "***", "request": {"access_token": "***", "status": 200}}


def test_structured_log_payload_bounds_large_strings_and_collections() -> None:
    payload = _mask_sensitive(
        None,
        "info",
        {"body": "x" * 5_000, "items": list(range(100))},
    )

    assert payload["body"].endswith("[truncated 1000 chars]")
    assert len(payload["items"]) == 50
