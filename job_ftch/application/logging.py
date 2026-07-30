"""Structured logging helpers."""

from __future__ import annotations

import logging
import re
import sys
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

import structlog

_DSN_PASSWORD_RE = re.compile(
    r"(?P<scheme>[a-zA-Z0-9+.-]+://[^:]+:)(?P<password>[^@]+)(?P<rest>@.*)"
)
_TOKEN_QUERY_RE = re.compile(r"([?&](?:token|api_key|secret|password)=)[^&\s]+")
_AUTH_HEADER_RE = re.compile(
    r"(Authorization:\s*(?:Bearer|Basic|Token)\s+)[^\s\\'\"]+", re.IGNORECASE
)


def sanitize_string(text: str) -> str:
    text = _DSN_PASSWORD_RE.sub(r"\g<scheme>***\g<rest>", text)
    text = _TOKEN_QUERY_RE.sub(r"\g<1>***", text)
    text = _AUTH_HEADER_RE.sub(r"\g<1>***", text)
    return text


if TYPE_CHECKING:
    from collections.abc import MutableMapping

_LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "token",
        "secret",
        "password",
        "dsn",
        "auth_hash",
        "authorization",
        "cookie",
        "cookies",
        "set_cookie",
        "headers",
        "proxy",
        "proxy_url",
        "captcha_token",
    }
)
_MASK = "***"
_MAX_LOG_STRING = 4_000
_MAX_LOG_COLLECTION = 50


def _mask_sensitive(
    logger: Any, method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    del logger, method
    for key, value in tuple(event_dict.items()):
        event_dict[key] = _sanitize_value(key, value)
    return event_dict


def _sanitize_value(key: str, value: Any) -> Any:
    normalized_key = key.casefold().replace("-", "_")
    if normalized_key in _SENSITIVE_KEYS or any(
        marker in normalized_key
        for marker in ("password", "secret", "authorization", "api_key", "access_token")
    ):
        return _MASK
    if (normalized_key == "url" or normalized_key.endswith("_url")) and isinstance(value, str):
        return _safe_log_url(value)
    if isinstance(value, str):
        value = sanitize_string(value)
        if len(value) <= _MAX_LOG_STRING:
            return value
        return f"{value[:_MAX_LOG_STRING]}…[truncated {len(value) - _MAX_LOG_STRING} chars]"
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize_value(str(child_key), child_value)
            for child_key, child_value in list(value.items())[:_MAX_LOG_COLLECTION]
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(key, child) for child in list(value)[:_MAX_LOG_COLLECTION]]
    return value


def _safe_log_url(value: str) -> str:
    """Retain route diagnostics without query credentials or URL userinfo."""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        if hostname is None:
            return "[invalid-url]"
        netloc = hostname
        if parsed.port is not None:
            netloc = f"{hostname}:{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except (TypeError, ValueError):
        return "[invalid-url]"


def configure_logging(level_name: str) -> None:
    level = _LOG_LEVELS[level_name.upper()]
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level)
    root_logger.setLevel(level)
    # Dependency DEBUG logs contain transport headers, retries and model hub
    # payloads.  Application events remain structured at the configured level.
    for noisy_logger in (
        "httpx",
        "httpcore",
        "urllib3",
        "huggingface_hub",
        "sentence_transformers",
        "transformers",
        "openai",
        "telethon.network",
        "asyncio",
    ):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.stdlib.add_log_level,
            _mask_sensitive,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
