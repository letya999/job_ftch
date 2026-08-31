"""Normalization stages for structured Job fields."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from job_ftch.application.contracts import TypeChangingNode
from job_ftch.application.geo import normalize_geo_sources
from job_ftch.domain import (
    JobDraft,
    JobRecord,
    Seniority,
    WorkMode,
    draft_to_record,
)

if TYPE_CHECKING:
    from job_ftch.application.contracts import Normalizer

_PREFIX_RE = re.compile(r"^(hiring|vacancy|opening|role|ищем|вакансия)\s*[:\-]\s*", re.IGNORECASE)
_COMP_SPLIT_RE = re.compile(r"\s+(?:at|@|-)\s+", re.IGNORECASE)
_CURRENCY_PATTERN = r"USD|EUR|GBP|RUB|RUR|KZT|руб(?:лей|ля|\.)?|р\.|\$|€|£|₽"
_AMOUNT_PATTERN = r"\d(?:[\d\s.,]*\d)?\s*(?:k|к|тыс(?:яч)?\.?|млн\.?)?"
_SALARY_PREFIX_RE = re.compile(
    rf"(?P<currency>{_CURRENCY_PATTERN})\s*(?P<direction>от|до|from|up\s+to)?\s*"
    rf"(?P<min>{_AMOUNT_PATTERN})(?:\s*(?:-|to|до|–|—)\s*(?P<max>{_AMOUNT_PATTERN}))?",
    re.IGNORECASE,
)
_SALARY_SUFFIX_RE = re.compile(
    rf"(?P<direction>от|до|from|up\s+to)?\s*(?P<min>{_AMOUNT_PATTERN})"
    rf"(?:\s*(?:-|to|до|–|—)\s*(?P<max>{_AMOUNT_PATTERN}))?"
    rf"\s*(?P<currency>{_CURRENCY_PATTERN})",
    re.IGNORECASE,
)
_BARE_SALARY_RE = re.compile(
    rf"(?P<direction>от|до|from|up\s+to)?\s*(?P<min>{_AMOUNT_PATTERN})"
    rf"(?:\s*(?:-|to|до|–|—)\s*(?P<max>{_AMOUNT_PATTERN}))?",
    re.IGNORECASE,
)
_SALARY_CONTEXT_RE = re.compile(
    r"зарплат|оклад|компенсац|доход|вознагражд|вилка|salary|income|pay\b",
    re.IGNORECASE,
)
_NON_SALARY_CONTEXT_RE = re.compile(
    r"кафетер|льгот|депозит|больнич|ипотек|кредит|страхов|дмс|отпуск|day\s+off",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(value: str) -> str:
    text = _TAG_RE.sub(" ", value)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return _WS_RE.sub(" ", text).strip()


def _clean_title(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _PREFIX_RE.sub("", _strip_html(value).strip())
    return cleaned or None


def _clean_company(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _strip_html(value).strip(" -|,")
    # Reject prose/garbage: a real company name is short and not a sentence.
    if len(cleaned) > 60 or cleaned.count(" ") >= 6:
        return None
    return cleaned or None


def _detect_work_mode(*parts: str | None) -> WorkMode:
    lowered = "\n".join(part or "" for part in parts).casefold()
    if "hybrid" in lowered:
        return WorkMode.HYBRID
    if any(token in lowered for token in ("remote", "удален", "удалён")):
        return WorkMode.REMOTE
    if any(token in lowered for token in ("on-site", "onsite", "office")):
        return WorkMode.ONSITE
    return WorkMode.UNKNOWN


def _normalize_amount(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    unit_match = re.search(r"(k|к|тыс(?:яч)?\.?|млн\.?)$", normalized)
    multiplier = 1
    if unit_match:
        unit = unit_match.group(1)
        multiplier = 1_000_000 if unit.startswith("млн") else 1_000
        normalized = normalized[: unit_match.start()].strip().replace(",", ".")
        normalized = normalized.replace(" ", "")
        try:
            return round(float(normalized) * multiplier)
        except ValueError:
            return None
    digits = re.sub(r"\D", "", normalized)
    return int(digits) if digits else None


def _normalize_currency(value: str) -> str:
    normalized = value.strip().casefold()
    return {
        "$": "USD",
        "€": "EUR",
        "£": "GBP",
        "₽": "RUB",
        "rur": "RUB",
        "руб": "RUB",
        "руб.": "RUB",
        "рублей": "RUB",
        "рубля": "RUB",
        "р.": "RUB",
    }.get(normalized, value.upper())


def _compensation_bounds(match: re.Match[str]) -> tuple[int | None, int | None] | None:
    first = _normalize_amount(match.group("min"))
    second = _normalize_amount(match.group("max"))
    direction = (match.group("direction") or "").casefold()
    if first is None:
        return None
    if direction in {"до", "up to"} and second is None:
        return None, first
    minimum, maximum = first, second
    if maximum is not None and minimum > maximum:
        minimum, maximum = maximum, minimum
    return minimum, maximum


def _bare_salary_is_plausible(value: str, match: re.Match[str]) -> bool:
    amount = match.group("min") or ""
    has_compact_unit = bool(re.search(r"(?:k|к|тыс(?:яч)?\.?|млн\.?)$", amount.strip(), re.I))
    remainder = value[match.end("min") :].lstrip().casefold()
    if re.match(r"(?:tokens?|токен(?:ов|а)?|requests?)\b", remainder):
        return False
    context = value[max(0, match.start() - 80) : match.end() + 80]
    return bool(has_compact_unit or _SALARY_CONTEXT_RE.search(context))


def _currency_amount_is_salary(value: str, match: re.Match[str]) -> bool:
    context = value[max(0, match.start() - 100) : match.end() + 100]
    return not (_NON_SALARY_CONTEXT_RE.search(context) and not _SALARY_CONTEXT_RE.search(context))


def _parse_compensation_text(value: str) -> tuple[str, int | None, int | None] | None:
    for match in (_SALARY_PREFIX_RE.search(value), _SALARY_SUFFIX_RE.search(value)):
        if match is None:
            continue
        if not _currency_amount_is_salary(value, match):
            continue
        bounds = _compensation_bounds(match)
        if bounds is not None:
            minimum, maximum = bounds
            return _normalize_currency(match.group("currency")), minimum, maximum

    match = _BARE_SALARY_RE.search(value)
    if match is None or not _bare_salary_is_plausible(value, match):
        return None
    bounds = _compensation_bounds(match)
    if bounds is None:
        return None
    minimum, maximum = bounds
    return "RUB", minimum, maximum


class TitleCompanyNormalizationNode(TypeChangingNode[JobDraft, JobRecord]):
    def __init__(self, normalizer: Normalizer):
        self.normalizer = normalizer

    async def process(self, item: JobDraft) -> JobRecord | None:
        title = _clean_title(item.title_raw)
        company = _clean_company(item.company_name_raw)
        if title is not None and company is None:
            parts = _COMP_SPLIT_RE.split(title, maxsplit=1)
            if len(parts) == 2:
                title, company = parts[0].strip(), parts[1].strip()

        normalization_steps: list[str] = []

        role_family = item.role_family
        if role_family is None and title:
            role_family = self.normalizer.infer_role_family(title)
            if role_family:
                normalization_steps.append(f"role_family:{role_family}")

        seniority = item.seniority
        if seniority is Seniority.UNKNOWN and title:
            inferred = self.normalizer.infer_seniority(title)
            if inferred:
                try:
                    seniority = Seniority(inferred)
                    normalization_steps.append(f"seniority:{inferred}")
                except ValueError:
                    pass

        if title != item.title_raw:
            normalization_steps.append("title:cleaned")
        if company != item.company_name_raw:
            normalization_steps.append("company:cleaned")

        description = item.description_raw or ""
        if "<" in description or "&" in description:
            description = _strip_html(description)
            normalization_steps.append("description:html_stripped")

        record = draft_to_record(item)
        provenance = record.provenance.model_copy(
            update={
                "normalization": tuple(
                    list(record.provenance.normalization)
                    + normalization_steps
                    + ["title_company_normalization"]
                )
            }
        )
        return record.model_copy(
            update={
                "title": title,
                "title_normalized": title,
                "company": company,
                "company_canonical": company,
                "company_name_raw": company,
                "company_name_normalized": company,
                "description": description or record.description,
                "role_family": role_family,
                "seniority": seniority,
                "provenance": provenance,
            }
        )


class LocationWorkModeNormalizationNode:
    async def process(self, item: JobRecord) -> JobRecord | None:
        location = item.location
        work_mode = item.work_mode
        if work_mode is WorkMode.UNKNOWN:
            work_mode = _detect_work_mode(item.description, item.title, location)
        geo = normalize_geo_sources(
            (
                item.city,
                item.country,
                location,
            )
        )
        location = geo.display
        city = item.city or geo.city or location
        country = geo.country or item.country
        region = item.region or location
        normalization_steps: list[str] = []
        if location != item.location:
            normalization_steps.append("location:normalized")
        if city != item.city:
            normalization_steps.append("city:inferred")
        if country != item.country:
            normalization_steps.extend(geo.corrections or ("country:normalized",))
        if work_mode != item.work_mode:
            normalization_steps.append("work_mode:inferred")
        metadata = item.metadata
        if normalization_steps:
            metadata = {
                **item.metadata,
                "geo_normalized_location": location,
                "geo_normalized_city": city,
                "geo_normalized_country": country,
                "geo_normalization_steps": tuple(normalization_steps),
            }
        return item.model_copy(
            update={
                "location": location,
                "city": city,
                "region": region,
                "country": country,
                "work_mode": work_mode,
                "metadata": metadata,
                "provenance": item.provenance.model_copy(
                    update={
                        "normalization": tuple(
                            list(item.provenance.normalization) + normalization_steps
                        )
                    }
                ),
            }
        )


class CompensationParsingNode:
    async def process(self, item: JobRecord) -> JobRecord | None:
        base_salary = item.metadata.get("base_salary")
        if isinstance(base_salary, dict):
            try:
                currency = str(base_salary.get("currency") or "USD")
                currency = _normalize_currency(
                    currency[:3].upper() if len(currency) >= 3 else "USD"
                )

                minimum = base_salary.get("min")
                maximum = base_salary.get("max")

                if isinstance(minimum, str):
                    minimum = _normalize_amount(minimum)
                if isinstance(maximum, str):
                    maximum = _normalize_amount(maximum)

                minimum = int(minimum) if isinstance(minimum, (int, float)) else None
                maximum = int(maximum) if isinstance(maximum, (int, float)) else None

                if minimum is not None or maximum is not None:
                    if minimum is not None and maximum is not None and minimum > maximum:
                        minimum, maximum = maximum, minimum

                    raw_period = str(base_salary.get("period") or "unknown").lower()

                    from job_ftch.domain import CompensationPeriod, CompensationRange

                    try:
                        period = CompensationPeriod(raw_period)
                    except ValueError:
                        period = CompensationPeriod.UNKNOWN

                    compensation = CompensationRange(
                        currency=currency,
                        min_amount=minimum,
                        max_amount=maximum,
                        period=period,
                    )

                    if item.compensation is not None:
                        return item
                    return item.model_copy(
                        update={
                            "compensation": compensation,
                            "provenance": item.provenance.model_copy(
                                update={
                                    "normalization": tuple(
                                        list(item.provenance.normalization)
                                        + ["compensation:structured_metadata"]
                                    )
                                }
                            ),
                        }
                    )
            except (ValueError, TypeError, KeyError):
                pass

        metadata_salary = item.metadata.get("salary_text") or item.metadata.get("base_salary_text")
        salary_source = "\n".join(
            part for part in (item.title, item.description, str(metadata_salary or "")) if part
        )
        parsed = _parse_compensation_text(salary_source)
        if parsed is None:
            if item.compensation is not None and not metadata_salary:
                # LLMs sometimes turn unrelated numbers such as "от 3 лет"
                # into a salary. Keep compensation only when the posting or
                # parser metadata provides salary evidence.
                return item.model_copy(update={"compensation": None})
            return item
        currency, minimum, maximum = parsed

        from job_ftch.domain import CompensationRange

        compensation = CompensationRange(
            currency=currency,
            min_amount=minimum,
            max_amount=maximum,
        )
        return item.model_copy(
            update={
                "compensation": compensation,
                "provenance": item.provenance.model_copy(
                    update={
                        "normalization": tuple(
                            list(item.provenance.normalization)
                            + ["compensation:parsed_from_description"]
                        )
                    }
                ),
            }
        )


class SkillNormalizationNode:
    def __init__(self, normalizer: Normalizer):
        self.normalizer = normalizer

    async def process(self, item: JobRecord) -> JobRecord | None:
        skills_explicit = self.normalizer.normalize_skills(item.skills_explicit)
        skills_inferred = self.normalizer.normalize_skills(item.skills_inferred)

        if skills_explicit != item.skills_explicit or skills_inferred != item.skills_inferred:
            return item.model_copy(
                update={
                    "skills_explicit": skills_explicit,
                    "skills_inferred": skills_inferred,
                    "provenance": item.provenance.model_copy(
                        update={
                            "normalization": tuple(
                                list(item.provenance.normalization) + ["skills:normalized"]
                            )
                        }
                    ),
                }
            )
        return item
