"""URL and origin allowlist policy for raw items."""

from __future__ import annotations

from ipaddress import ip_address
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from application.outcomes import NodeOutcome, PipelineStage, RejectReason
from domain import SourceKind

if TYPE_CHECKING:
    from application.context import ProcessingContext
    from domain import RawItem

_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
_TELEGRAM_HOSTS = frozenset({"t.me", "www.t.me"})
_METADATA_URL_FIELDS = ("board_url", "job_url", "post_url")


class OriginPolicyNode:
    name = "origin_policy"
    stage = PipelineStage.ORIGIN_POLICY
    is_sanitize = False

    def __init__(
        self,
        *,
        allowed_career_site_hosts: tuple[str, ...] = (),
        allow_private_career_hosts: bool = False,
    ) -> None:
        self._allowed_career_site_hosts = {
            host.strip().lower() for host in allowed_career_site_hosts if host.strip()
        }
        self._allow_private_career_hosts = allow_private_career_hosts

    async def process(self, item: RawItem, context: ProcessingContext) -> NodeOutcome[RawItem]:
        allowed_hosts = self._allowed_career_site_hosts or set(context.allowed_domains)
        checks: list[tuple[str, str, RejectReason, RejectReason, RejectReason]] = []
        if item.url is not None:
            checks.append(
                (
                    "url",
                    str(item.url),
                    RejectReason.INVALID_URL,
                    RejectReason.DISALLOWED_URL_HOST,
                    RejectReason.PRIVATE_URL_HOST,
                )
            )
        for key in _METADATA_URL_FIELDS:
            value = item.metadata.get(key)
            if isinstance(value, str):
                checks.append(
                    (
                        key,
                        value,
                        RejectReason.INVALID_ORIGIN_URL,
                        RejectReason.DISALLOWED_ORIGIN_HOST,
                        RejectReason.PRIVATE_ORIGIN_HOST,
                    )
                )

        for field_name, value, invalid_reason, disallowed_reason, private_reason in checks:
            outcome = self._check_url(
                item=item,
                allowed_hosts=allowed_hosts,
                field_name=field_name,
                value=value,
                invalid_reason=invalid_reason,
                disallowed_reason=disallowed_reason,
                private_reason=private_reason,
            )
            if outcome is not None:
                return outcome
        return NodeOutcome.pass_(item)

    def _check_url(
        self,
        *,
        item: RawItem,
        allowed_hosts: set[str] | frozenset[str],
        field_name: str,
        value: str,
        invalid_reason: RejectReason,
        disallowed_reason: RejectReason,
        private_reason: RejectReason,
    ) -> NodeOutcome[RawItem] | None:
        try:
            parsed = _URL_ADAPTER.validate_python(value)
        except ValidationError:
            return self._quarantine(
                item,
                reason=invalid_reason,
                message=f"{field_name} is not a valid HTTP URL.",
                metadata={field_name: value},
            )
        host = (parsed.host or "").lower()
        scheme = urlsplit(str(parsed)).scheme.lower()
        if scheme not in {"http", "https"}:
            return self._quarantine(
                item,
                reason=invalid_reason,
                message=f"{field_name} must use http or https.",
                metadata={field_name: value, "scheme": scheme},
            )

        if item.source_kind in {
            SourceKind.TELEGRAM_CHANNEL,
            SourceKind.TELEGRAM_GROUP,
            SourceKind.TELEGRAM_COMMENT,
        }:
            if host not in _TELEGRAM_HOSTS:
                return self._quarantine(
                    item,
                    reason=disallowed_reason,
                    message=f"{field_name} host {host!r} is not an allowed Telegram origin.",
                    metadata={field_name: value, "host": host},
                )
            return None

        if item.source_kind is SourceKind.CAREER_SITE:
            if not self._allow_private_career_hosts and _is_private_or_local_host(host):
                return self._quarantine(
                    item,
                    reason=private_reason,
                    message=f"{field_name} host {host!r} is local or private.",
                    metadata={field_name: value, "host": host},
                )
            if host not in allowed_hosts:
                allowed = ", ".join(sorted(allowed_hosts))
                return self._quarantine(
                    item,
                    reason=disallowed_reason,
                    message=f"{field_name} host {host!r} is not in the allowlist: {allowed}",
                    metadata={field_name: value, "host": host, "allowed_hosts": allowed},
                )
        return None

    def _quarantine(
        self,
        item: RawItem,
        *,
        reason: RejectReason,
        message: str,
        metadata: dict[str, object],
    ) -> NodeOutcome[RawItem]:
        return NodeOutcome.quarantine(
            item=item,
            reason=reason,
            message=message,
            metadata={**item.model_dump(mode="json", warnings=False), **metadata},
        )


def _is_private_or_local_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        parsed_ip = ip_address(host)
    except ValueError:
        return False
    return (
        parsed_ip.is_private
        or parsed_ip.is_loopback
        or parsed_ip.is_link_local
        or parsed_ip.is_reserved
    )
