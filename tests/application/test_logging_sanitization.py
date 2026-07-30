from __future__ import annotations

from job_ftch.application.logging import _sanitize_value


def test_log_url_drops_query_fragment_and_userinfo() -> None:
    assert (
        _sanitize_value(
            "url",
            "https://user:password@example.test/jobs?token=secret#fragment",
        )
        == "https://example.test/jobs"
    )


def test_log_sanitizer_masks_cookie_token_and_proxy_values() -> None:
    assert _sanitize_value("cookies", {"cf_clearance": "cookie-secret"}) == "***"
    assert _sanitize_value("captcha_token", "token-secret") == "***"
    assert _sanitize_value("proxy_url", "http://user:password@proxy.test") == "***"


def test_exception_string_redacts_dsn_password() -> None:
    assert (
        _sanitize_value("error", "Failed to connect to mysql://user:secret123@host:3306/db")
        == "Failed to connect to mysql://user:***@host:3306/db"
    )


def test_rejected_artifact_redacts_error_message_secret() -> None:
    assert (
        _sanitize_value(
            "error_message", "API returned 401 for Authorization: Bearer some-secret-token-here"
        )
        == "API returned 401 for Authorization: Bearer ***"
    )


def test_quarantine_artifact_redacts_details_secret() -> None:
    assert (
        _sanitize_value("details", "URL https://example.com/api?token=super-secret failed")
        == "URL https://example.com/api?token=*** failed"
    )
