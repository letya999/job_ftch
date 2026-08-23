"""Rich HTTP API parser for rabota.sber.ru."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlparse

from job_ftch.application.registry import known_board_assessment_hint, register_site_parser
from job_ftch.domain import (
    AcquisitionTransport,
    ObservationKind,
    SourceFamily,
    SourceIdentity,
    SourceKind,
    source_spec_name,
)
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_parsers.base import SiteRuntimeDefaults
from job_ftch.infrastructure.sources.site_parsers.helpers import (
    normalize_search_keywords,
    safe_fetch,
    with_query_params,
)

if TYPE_CHECKING:
    from job_ftch.domain.source_spec import CareerSiteSpec


class SberParser:
    domain_pattern = r"(?:www\.)?rabota\.sber\.ru(?:/|$)"
    has_custom_parse = True
    supports_discover = False
    supports_search = True
    search_mode = "combined"

    def build_search_urls(
        self,
        base_url: str,
        keywords: Any,
        *,
        limit: int | None = None,
    ) -> list[str]:
        del limit
        terms = normalize_search_keywords(keywords)
        if not terms:
            return []
        return [with_query_params(base_url, {"query": " OR ".join(terms)})]

    _API_URL = "https://rabota.sber.ru/public/app-candidate-public-api-gateway/api/v1/publications"

    def runtime_defaults(self, url: str) -> SiteRuntimeDefaults:
        del url
        return SiteRuntimeDefaults(
            render=False,
            wait="domcontentloaded",
            include_if_detail_page=False,
        )

    def parser_kind(self, url: str) -> str | None:
        del url
        return "sber"

    @property
    def __name__(self) -> str:
        return "SberParser"

    async def parse(self, spec: CareerSiteSpec, client: Any) -> Any:
        limit = spec.limit or getattr(getattr(self, "_manifest_entry", None), "limit", None) or 50
        query = parse_qs(urlparse(spec.url).query).get("query", [""])[0]
        params: dict[str, int | str] = {"skip": 0, "take": min(int(limit), 50)}
        if query:
            params["searchString"] = query
        response = await safe_fetch(client, f"{self._API_URL}?{urlencode(params)}")
        payload = response.json()
        vacancies = (
            payload.get("data", {}).get("vacancies", []) if isinstance(payload, dict) else []
        )
        source_name = spec.source_name or source_spec_name(spec)
        for vacancy in vacancies[: int(limit)]:
            if not isinstance(vacancy, dict):
                continue
            item = self._to_raw_item(vacancy, source_name)
            if item is not None:
                yield item

    @staticmethod
    def _to_raw_item(vacancy: dict[str, Any], source_name: str) -> Any:
        title = str(vacancy.get("title") or "").strip()
        internal_id = str(vacancy.get("internalId") or "").strip()
        if not title or not internal_id:
            return None
        slug = _sber_slug(title) or "vacancy"
        detail_url = f"https://rabota.sber.ru/search/{slug}-{internal_id}/"
        sections = [
            title,
            str(vacancy.get("company") or "").strip(),
            str(vacancy.get("introduction") or "").strip(),
            str(vacancy.get("duties") or "").strip(),
            str(vacancy.get("requirements") or "").strip(),
            str(vacancy.get("conditions") or "").strip(),
        ]
        posted_at = None
        raw_posted_at = vacancy.get("publicationDate")
        if isinstance(raw_posted_at, str):
            try:
                posted_at = datetime.fromisoformat(raw_posted_at.replace("Z", "+00:00"))
                posted_at = posted_at.astimezone(UTC)
            except ValueError:
                posted_at = None
        metadata = {
            "source_family": SourceFamily.ATS_API.value,
            "observation_kind": ObservationKind.VACANCY_DETAIL.value,
            "transport": AcquisitionTransport.HTTP.value,
            "adapter": "sber-public-api",
            "parser_version": "sber-api-v1",
            "detail_vacancy_confirmed": True,
            "company": vacancy.get("company"),
            "locations": [
                value
                for value in (vacancy.get("city"), vacancy.get("region"))
                if isinstance(value, str) and value.strip()
            ],
            "date_posted": raw_posted_at,
            "requisition_id": vacancy.get("requisitionId"),
            "publication_id": vacancy.get("publicationId"),
            "parser": "sber-public-api",
        }
        salary = _sber_salary_metadata(vacancy)
        if salary:
            metadata.update(salary)
        return build_raw_item(
            source_kind=SourceKind.CAREER_SITE,
            source_name=source_name,
            external_id=internal_id,
            url=detail_url,
            text="\n\n".join(section for section in sections if section),
            created_at=posted_at,
            metadata=metadata,
            source_identity=SourceIdentity(
                family=SourceFamily.ATS_API,
                observation_kind=ObservationKind.VACANCY_DETAIL,
                transport=AcquisitionTransport.HTTP,
                adapter="sber-public-api",
                parser_version="sber-api-v1",
                legacy_kind=SourceKind.CAREER_SITE.value,
            ),
        )


_SALARY_TEXT_KEYS = ("salaryText", "salary_text", "salary", "compensation", "salaryRange")
_SALARY_MIN_KEYS = ("min", "from", "salaryFrom", "salaryMin", "minSalary")
_SALARY_MAX_KEYS = ("max", "to", "salaryTo", "salaryMax", "maxSalary")


def _sber_amount(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None
    if not isinstance(value, str):
        return None
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else None


def _sber_salary_metadata(vacancy: dict[str, Any]) -> dict[str, Any]:
    """Keep only explicit salary fields; bonuses in ``conditions`` are not salary."""

    result: dict[str, Any] = {}
    raw = next(
        (vacancy.get(key) for key in _SALARY_TEXT_KEYS if vacancy.get(key) is not None), None
    )
    if isinstance(raw, str) and any(char.isdigit() for char in raw):
        result["salary_text"] = raw.strip()

    payloads = [raw] if isinstance(raw, dict) else []
    payloads.append(vacancy)
    minimum = maximum = None
    currency: str | None = None
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        if minimum is None:
            minimum = next(
                (
                    _sber_amount(payload.get(key))
                    for key in _SALARY_MIN_KEYS
                    if payload.get(key) is not None
                ),
                None,
            )
        if maximum is None:
            maximum = next(
                (
                    _sber_amount(payload.get(key))
                    for key in _SALARY_MAX_KEYS
                    if payload.get(key) is not None
                ),
                None,
            )
        currency = currency or next(
            (
                str(payload.get(key)).strip().upper()
                for key in ("currency", "salaryCurrency")
                if payload.get(key)
            ),
            None,
        )
    if minimum is not None or maximum is not None:
        result["base_salary"] = {
            "currency": currency or "RUB",
            "min": minimum,
            "max": maximum,
        }
    return result


_TRANSLIT = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "c",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)


def _sber_slug(value: str) -> str:
    transliterated = value.lower().translate(_TRANSLIT)
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", transliterated)).strip("-")


register_site_parser(
    "sber",
    domain_pattern=SberParser.domain_pattern,
    assessment_hint=known_board_assessment_hint(
        "known_site",
        "site_parser:rabota.sber.ru",
        has_stable_url=True,
        has_embedded_state=True,
        requires_full_snapshot=False,
        rationale="rabota.sber.ru exposes stable vacancy URLs and is handled by a dedicated Next.js parser.",
    ),
)(SberParser)
