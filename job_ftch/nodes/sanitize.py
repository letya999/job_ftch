"""RawItem sanitation node for phase-0 and beyond."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from job_ftch.application.rejections import RawItemRejected
from job_ftch.domain import RawItemRejectionReason, SourceKind
from job_ftch.domain.structured_vacancy import is_structured_vacancy

if TYPE_CHECKING:
    from job_ftch.domain import RawItem

_WHITESPACE_RE = re.compile(r"\s+")
_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
_TELEGRAM_HOSTS = frozenset({"t.me", "www.t.me"})
_METADATA_URL_FIELDS = ("board_url", "job_url", "post_url")


def _strip_control_chars(value: str) -> str:
    return "".join(char for char in value if unicodedata.category(char)[0] != "C" or char in "\n\t")


def _normalize_unicode(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return normalized.replace("\ufeff", "").replace("\u200b", "")


def _normalize_whitespace(value: str) -> str:
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def _normalize_text(value: str) -> str:
    return _normalize_whitespace(_strip_control_chars(_normalize_unicode(value)))


def _normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "",
            parts.query,
            "",
        )
    )


def _host_allowed(host: str, allowed_hosts: set[str]) -> bool:
    return any(
        host == allowed_host or host.endswith(f".{allowed_host}") for allowed_host in allowed_hosts
    )


class SanitizeNode:
    # Marker for builder/pipeline guards. Custom sanitizers can opt in by
    # setting `_is_sanitizer = True` on their class. Subclassing SanitizeNode
    # also works because isinstance is checked.
    _is_sanitizer: bool = True

    def __init__(
        self,
        *,
        allowed_career_site_hosts: tuple[str, ...] = (),
        max_text_length: int = 20_000,
    ) -> None:
        self._allowed_career_site_hosts = {host.lower() for host in allowed_career_site_hosts}
        self._max_text_length = max_text_length

    async def process(self, item: RawItem) -> RawItem | None:
        sanitized_text = _normalize_text(str(item.text))
        sanitized_source_name = _normalize_text(str(item.source_name))
        sanitized_external_id = _normalize_text(str(item.external_id)) if item.external_id else None
        if not sanitized_text:
            raise RawItemRejected(
                reason=RawItemRejectionReason.EMPTY_TEXT,
                details="Sanitized text is empty.",
                item=item,
            )
        if len(sanitized_text) > self._max_text_length:
            # Truncate at the last whitespace before the limit instead of rejecting entirely.
            # Long career-site pages are common; wholesale rejection silently drops valid jobs.
            cutoff = sanitized_text.rfind(" ", 0, self._max_text_length)
            if cutoff <= 0:
                cutoff = self._max_text_length
            sanitized_text = sanitized_text[:cutoff]
        if not sanitized_source_name:
            raise RawItemRejected(
                reason=RawItemRejectionReason.EMPTY_SOURCE_NAME,
                details="Sanitized source_name is empty.",
                item=item,
            )
        normalized_url = self._validate_primary_url(item)
        if not sanitized_external_id and normalized_url is None:
            raise RawItemRejected(
                reason=RawItemRejectionReason.MISSING_LOCATOR,
                details="Raw item must keep external_id or url after sanitation.",
                item=item,
            )
        metadata = self._sanitize_metadata(item)
        updates: dict[str, object] = {
            "text": sanitized_text,
            "source_name": sanitized_source_name,
            "external_id": sanitized_external_id,
            "metadata": metadata,
        }
        if normalized_url is not None:
            updates["url"] = normalized_url
        candidate = item.model_copy(update=updates)
        try:
            return item.__class__.model_validate(
                candidate.model_dump(mode="python", warnings=False)
            )
        except ValidationError as exc:
            raise RawItemRejected(
                reason=RawItemRejectionReason.INVALID_RAW_ITEM,
                details=str(exc),
                item=item,
                snapshot=candidate.model_dump(mode="json", warnings=False),
            ) from exc

    def _sanitize_metadata(self, item: RawItem) -> dict[str, object]:
        sanitized: dict[str, object] = {}
        for key, value in item.metadata.items():
            if isinstance(value, str):
                if key in _METADATA_URL_FIELDS:
                    sanitized[key] = str(self._validate_origin_url(item, key, value))
                else:
                    sanitized[key] = _normalize_text(value)
                continue
            sanitized[key] = value
        if "original_posting_text" not in sanitized:
            sanitized["original_posting_text"] = item.text
        if not sanitized.get("extraction_source") and not sanitized.get("monitor_type"):
            structured, fields = is_structured_vacancy(item.text)
            if structured:
                sanitized["extraction_source"] = "telegram_structured"
                for field_name, value in fields.items():
                    if field_name != "description_marker" and field_name not in sanitized:
                        sanitized[field_name] = value
        return sanitized

    def _validate_primary_url(self, item: RawItem) -> AnyHttpUrl | None:
        if item.url is None:
            return None
        return self._validate_url(
            item=item,
            value=str(item.url),
            reason_on_invalid=RawItemRejectionReason.INVALID_URL,
            reason_on_disallowed=RawItemRejectionReason.DISALLOWED_URL_HOST,
        )

    def _validate_origin_url(self, item: RawItem, field_name: str, value: str) -> AnyHttpUrl:
        return self._validate_url(
            item=item,
            value=value,
            reason_on_invalid=RawItemRejectionReason.INVALID_ORIGIN_URL,
            reason_on_disallowed=RawItemRejectionReason.DISALLOWED_ORIGIN_HOST,
            field_name=field_name,
        )

    def _validate_url(
        self,
        *,
        item: RawItem,
        value: str,
        reason_on_invalid: RawItemRejectionReason,
        reason_on_disallowed: RawItemRejectionReason,
        field_name: str = "url",
    ) -> AnyHttpUrl:
        normalized = _normalize_url(_normalize_text(value))
        try:
            parsed = _URL_ADAPTER.validate_python(normalized)
        except ValidationError as exc:
            raise RawItemRejected(
                reason=reason_on_invalid,
                details=f"{field_name} is not a valid URL: {value!r}",
                item=item,
            ) from exc
        host = parsed.host.lower() if parsed.host is not None else ""
        if item.source_kind is SourceKind.CAREER_SITE and self._allowed_career_site_hosts:
            if not _host_allowed(host, self._allowed_career_site_hosts):
                allowed = ", ".join(sorted(self._allowed_career_site_hosts))
                raise RawItemRejected(
                    reason=reason_on_disallowed,
                    details=f"{field_name} host {host!r} is not in the allowlist: {allowed}",
                    item=item,
                )
        elif (
            item.source_kind
            in {
                SourceKind.TELEGRAM_CHANNEL,
                SourceKind.TELEGRAM_GROUP,
                SourceKind.TELEGRAM_COMMENT,
            }
            and host not in _TELEGRAM_HOSTS
        ):
            raise RawItemRejected(
                reason=reason_on_disallowed,
                details=f"{field_name} host {host!r} is not an allowed Telegram origin.",
                item=item,
            )
        return parsed
