"""Runtime defaults for known WAF-heavy career surfaces.

These entries do not claim to bypass the protection.  They prevent wasteful
HTTP-only discovery attempts and send the source directly to rendered
browser/fingerprint-capable monitors; unresolved challenges remain explicit
``waf_challenge`` outcomes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_site_parser
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_ftch.domain.models import RawItem
    from job_ftch.domain.source_spec import CareerSiteSpec


_DOMAIN_PATTERN = (
    r"^https?://(?:"
    r"himalayas\.app|"
    r"(?:www\.)?foody\.com\.cy|"
    r"(?:www\.)?fishingbooker\.com|"
    r"theprotocol\.it|"
    r"(?:www\.)?pracuj\.pl|"
    r"(?:www\.)?cypruswork\.com|"
    r"jobs\.ashbyhq\.com|"
    r"careers\.higgsfield\.kz|"
    r"job\.beeline\.ru|"
    r"(?:www\.)?superjob\.ru"
    r")(?:/|$)"
)


class ProtectedBrowserDefaultsParser:
    domain_pattern = _DOMAIN_PATTERN
    has_custom_parse = False

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=True,
            wait="domcontentloaded",
            include_if_detail_page=False,
            extra={
                "bypass_capability": "cloudflare_challenge",
                "bypass_capability_reason": "protected_browser_defaults",
                "challenge_retries": 1,
                "challenge_wait_ms": 2500,
                "disable_http2": True,
                "monitor": "dom",
                "persistent_context": True,
                "protected_hint": "waf_challenge",
                "wait_fallback": "load",
                "settle_seconds": 5,
                "captcha_authorized_domains": [
                    "jobs.ashbyhq.com",
                    "careers.higgsfield.kz",
                    "job.beeline.ru",
                    "www.superjob.ru",
                    "superjob.ru",
                ],
                "proxy_rescue_allow_domains": [
                    "jobs.ashbyhq.com",
                    "careers.higgsfield.kz",
                    "job.beeline.ru",
                    "www.superjob.ru",
                    "superjob.ru",
                ],
            },
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return None

    async def parse(
        self,
        spec: CareerSiteSpec,
        client: Any,
    ) -> AsyncIterator[RawItem]:
        del spec, client
        return
        yield  # pragma: no cover


register_site_parser(
    "protected_browser_defaults",
    domain_pattern=ProtectedBrowserDefaultsParser.domain_pattern,
)(ProtectedBrowserDefaultsParser)
