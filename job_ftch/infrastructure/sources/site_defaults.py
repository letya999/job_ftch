from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from job_ftch.domain.source_spec import CareerSiteSpec


def _defaults_for_url(url: str) -> tuple[dict[str, Any], str | dict[str, Any] | None]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"

    if host == "career.habr.com":
        return (
            {"url_filter": r"career\.habr\.com/vacancies/\d{5,}"},
            r"career\.habr\.com/vacancies/\d{5,}",
        )

    if host in {"hh.ru", "hh.kz"}:
        return (
            {"url_filter": r"hh\.(?:ru|kz)/vacancy/\d+"},
            r"hh\.(?:ru|kz)/vacancy/\d+",
        )

    if host == "yandex.ru" and path.startswith("/jobs"):
        return (
            {"url_filter": r"yandex\.ru/jobs/vacancies/(?!\?)[^?#]+"},
            r"yandex\.ru/jobs/vacancies/(?!\?)[^?#]+",
        )

    if host == "www.tbank.ru" and path.startswith("/career"):
        url_filter = r"tbank\.ru/career/it/vacancy/[a-z0-9\-/]+"
        return (
            {
                "url_filter": url_filter,
                "expand_links": [
                    r"tbank\.ru/career/it(?:/|$)",
                    r"tbank\.ru/career/it/ml(?:/|$)",
                    r"tbank\.ru/career/vacancies/it(?:/|\?|$)",
                ],
                "include_if_detail_page": False,
            },
            url_filter,
        )

    if host == "ozon.tech" and path.startswith("/vacancies"):
        url_filter = r"ozon\.tech/vacancies/[a-f0-9\-]+-[a-z0-9-]+/?$"
        return (
            {
                "url_filter": url_filter,
                "render": True,
                "wait": "domcontentloaded",
                "include_if_detail_page": False,
            },
            url_filter,
        )

    return {}, None


def apply_runtime_defaults(spec: CareerSiteSpec) -> CareerSiteSpec:
    """Inject safe domain defaults for generic career-site URLs.

    Explicit spec values always win. This keeps manual single-URL runs aligned with
    the richer YAML source configs used in normal operation.
    """

    default_monitor_config, default_url_filter = _defaults_for_url(spec.url)
    if not default_monitor_config and default_url_filter is None:
        return spec

    monitor_config = dict(default_monitor_config)
    monitor_config.update(spec.monitor_config)

    url_filter = spec.url_filter
    if url_filter is None:
        url_filter = monitor_config.get("url_filter") or default_url_filter

    return spec.model_copy(
        update={
            "monitor_config": monitor_config,
            "url_filter": url_filter,
        }
    )
