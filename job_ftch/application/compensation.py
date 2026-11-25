"""Resolve base pay from its original currency, bounds and adjacent pay interval."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from pydantic import ValidationError

from job_ftch.domain import CompensationPeriod, CompensationRange

if TYPE_CHECKING:
    from job_ftch.domain import JobRecord

_CURRENCY = r"USD|EUR|GBP|RUB|RUR|KZT|CAD|AUD|CHF|PLN|UZS|KGS|AMD|GEL|AZN|TJS|BYN|тенге|руб(?:лей|ля|\.)?|р\.|\$|€|£|₽|₸"
_SCALE = r"(?:k|к|тыс(?:яч)?\.?|млн\.?)(?!\w)"
_AMOUNT = rf"\d+(?:[.,]\d+|[ \u00a0\u202f]\d{{3}}(?!\d))*(?:\s*{_SCALE})?"
_SEP = r"\s*(?:-|to|до|–|—)\s*"
_DIR = r"(?P<direction>от|до|from|up\s+to)?\s*"
_PREFIX = re.compile(
    rf"{_DIR}(?P<currency>{_CURRENCY})\s*(?P<min>{_AMOUNT})(?:{_SEP}(?P=currency)?\s*(?P<max>{_AMOUNT}))?",
    re.I,
)
_SUFFIX = re.compile(
    rf"{_DIR}(?P<min>{_AMOUNT})(?:{_SEP}(?P<max>{_AMOUNT}))?\s*(?P<currency>{_CURRENCY})",
    re.I,
)
_REPEATED_SUFFIX = re.compile(
    rf"{_DIR}(?P<min>{_AMOUNT})\s*(?P<currency>{_CURRENCY}){_SEP}(?P<max>{_AMOUNT})\s*(?P=currency)",
    re.I,
)
_MARKET_PANEL = re.compile(r"\bАнализ\s+зарплаты\s+Медиана\b", re.I)
_CURRENCIES = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "₽": "RUB",
    "₸": "KZT",
    "тенге": "KZT",
    "rur": "RUB",
    "руб": "RUB",
    "руб.": "RUB",
    "рублей": "RUB",
    "рубля": "RUB",
    "р.": "RUB",
}
_PAY = re.compile(
    r"зарплат|оклад|компенсац|доход|вознагражд|вилка|salary|base\s+pay|compensation|income", re.I
)
_NON_PAY = re.compile(
    r"кафетер|льгот|депозит|больнич|ипотек|кредит|страхов|дмс|отпуск|day\s+off|"
    r"бюджет|бонус|релокац|оборот|gmv|revenue|funding|investment|budget|bonus|stipend|allowance|relocation|reimbursement",
    re.I,
)
_CLAUSE_END = re.compile(r"[;!?\n]|\.(?=\s|$)")
_PERIODS = {
    CompensationPeriod.YEAR: r"years?|yearly|annually|annual|annum|год(?:а|овой)?",
    CompensationPeriod.MONTH: r"months?|monthly|месяц(?:а)?|мес",
    CompensationPeriod.HOUR: r"hours?|hourly|hrs?|час(?:а)?",
    CompensationPeriod.DAY: r"days?|daily|день",
    CompensationPeriod.WEEK: r"weeks?|weekly|недел[юя]|нед",
    CompensationPeriod.PROJECT: r"project|проект",
}
_PERIOD_SUFFIX = {
    period: re.compile(
        rf"^\s*(?:(?:gross|net|на\s+руки)\s*)?(?:/\s*|per\s+|a\s+|в\s+|за\s+)?(?:{words})\b", re.I
    )
    for period, words in _PERIODS.items()
}
_PERIOD_PREFIX = re.compile(
    r"(annual|yearly|monthly|hourly|daily|weekly|годовой|годовая|ежемесячный|ежемесячная)\s+(?:base\s+)?(?:salary|pay|compensation|зарплата|оклад)\s*[:–-]?\s*$",
    re.I,
)
_GROSS = re.compile(r"\bgross\b|до\s+вычета\s+налог", re.I)
_NET = re.compile(r"\bnet\b|на\s+руки|после\s+вычета\s+налог", re.I)
_INVISIBLE = re.compile(r"[\u200b-\u200f\ufeff]")


def _currency(value: str) -> str:
    return _CURRENCIES.get(value.strip().casefold(), value.strip().upper())


def _amount(value: object) -> int | None:
    if not isinstance(value, (int, float, str)) or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return round(value)
        except (ValueError, OverflowError):
            return None
    number = re.sub(r"\s", "", value.casefold())
    scale = re.search(rf"{_SCALE}$", number)
    multiplier = 1
    if scale:
        multiplier = 1_000_000 if scale.group().startswith("млн") else 1_000
        number = number[: scale.start()]
    # Only a final one- or two-digit group is a decimal part. Preserve cents
    # until rounding, so $141,000.00 and 141.000,00 EUR both mean 141000.
    money = re.fullmatch(r"([\d.,]*\d)[.,](\d{1,2})", number)
    integer = money[1] if money else number
    if not re.fullmatch(r"\d+(?:[.,]\d+)*", integer):
        return None
    integer = integer.replace(",", "").replace(".", "")
    try:
        return round(Decimal(integer + ("." + money[2] if money else "")) * multiplier)
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _unit(raw: object) -> CompensationPeriod:
    for period, words in _PERIODS.items():
        if re.fullmatch(words, str(raw or ""), re.I):
            return period
    return CompensationPeriod.UNKNOWN


def _tax(text: str) -> bool | None:
    if _GROSS.search(text):
        return True
    return False if _NET.search(text) else None


def _parse_text(value: str) -> tuple[CompensationRange | None, str | None]:
    text = _INVISIBLE.sub("", value)
    best = None
    best_quote = None
    best_score = -1
    for pattern in (_REPEATED_SUFFIX, _PREFIX, _SUFFIX):
        for match in pattern.finditer(text):
            before = _CLAUSE_END.split(text[max(0, match.start() - 100) : match.start()])[-1]
            after = _CLAUSE_END.split(text[match.end() : match.end() + 80], maxsplit=1)[0]
            quote = (before + match.group() + after).strip()
            pay_label = list(_PAY.finditer(before))
            benefit_label = list(_NON_PAY.finditer(before))
            if benefit_label and (
                not pay_label or benefit_label[-1].start() > pay_label[-1].start()
            ):
                continue
            if (
                not pay_label
                and _NON_PAY.search(after)
                and not re.match(r"\s*(?:plus|and|\+)\s", after, re.I)
            ):
                continue
            period = next(
                (p for p, regex in _PERIOD_SUFFIX.items() if regex.match(after)),
                CompensationPeriod.UNKNOWN,
            )
            if period is CompensationPeriod.UNKNOWN and (prefix := _PERIOD_PREFIX.search(before)):
                token = prefix[1].casefold()
                period = (
                    CompensationPeriod.MONTH
                    if token.startswith("ежемесяч")
                    else (CompensationPeriod.YEAR if token.startswith("годов") else _unit(token))
                )
            first, second = _amount(match["min"]), _amount(match["max"])
            if first is None:
                continue
            if second is not None and first > second:
                first, second = second, first
            elif second is None and (match["direction"] or "").casefold() in {"до", "up to"}:
                first, second = None, first
            score = (
                4 * bool(_PAY.search(before))
                + 2 * (period is not CompensationPeriod.UNKNOWN)
                + (second is not None)
            )
            if score > best_score:
                best = CompensationRange(
                    currency=_currency(match["currency"]),
                    min_amount=first,
                    max_amount=second,
                    period=period,
                    gross=_tax(quote),
                )
                best_quote, best_score = quote, score
    return best, best_quote


def parse_compensation_text(value: str) -> tuple[str, int | None, int | None] | None:
    """Read the legacy amount-only projection used by extraction evaluations."""
    offer, _ = _parse_text(value)
    return (offer.currency, offer.min_amount, offer.max_amount) if offer is not None else None


def _structured(metadata: dict[str, object]) -> CompensationRange | None:
    raw = metadata.get("base_salary")
    if not isinstance(raw, dict):
        return None
    lo, hi = _amount(raw.get("min")), _amount(raw.get("max"))
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    try:
        return CompensationRange(
            currency=_currency(str(raw.get("currency") or "")),
            min_amount=lo,
            max_amount=hi,
            period=_unit(raw.get("period") or raw.get("unit")),
            gross=raw.get("gross") if isinstance(raw.get("gross"), bool) else None,
        )
    except ValueError:
        return None


def _same_pay(left: CompensationRange, right: CompensationRange) -> bool:
    if left.currency != right.currency:
        return False
    a, b = (left.min_amount, left.max_amount), (right.min_amount, right.max_amount)
    if a == b:
        return True
    # A fixed amount may be stored as [n,n] or as one bound. Opposite
    # one-sided offers ("at least n" and "up to n") are different evidence.
    return (a[0] == a[1] or b[0] == b[1]) and set(a + b) - {None} == {
        a[0] if a[0] is not None else a[1]
    }


def _needs_pay_evidence(offer: CompensationRange) -> bool:
    high = max(offer.min_amount or 0, offer.max_amount or 0)
    return offer.currency in {"USD", "EUR", "GBP", "CAD", "AUD", "SGD", "CHF"} and (
        high > 10_000_000
        or (
            high >= 50_000
            and offer.period in {CompensationPeriod.MONTH, CompensationPeriod.UNKNOWN}
        )
    )


def _thousandfold_range_conflict(offer: CompensationRange, actual: CompensationRange) -> bool:
    return (
        actual.currency == offer.currency
        and actual.min_amount is not None
        and actual.min_amount > 0
        and actual.max_amount is not None
        and offer.min_amount == actual.min_amount * 1_000
        and offer.max_amount == actual.max_amount * 1_000
    )


def _job_texts(job: object) -> tuple[str, ...]:
    metadata = getattr(job, "metadata", {})
    return tuple(
        dict.fromkeys(
            text
            for text in (
                metadata.get("salary_text"),
                metadata.get("base_salary_text"),
                metadata.get("original_posting_text"),
                getattr(job, "description_raw", None),
                getattr(job, "description", None),
                getattr(job, "title", None),
            )
            if isinstance(text, str) and text.strip()
        )
    )


def _employer_sections(job: object) -> tuple[str, ...] | None:
    """A board's salary analysis, cover letter and related jobs are editorial content."""
    for text in _job_texts(job):
        panel = _MARKET_PANEL.search(text)
        if panel is not None:
            header = re.split(r"Оценка\s+ИИ", text[: panel.start()], maxsplit=1, flags=re.I)[0]
            body = re.split(r"Описание\s+вакансии", text[panel.end() :], maxsplit=1, flags=re.I)
            description = (
                re.split(r"Похожие\s+вакансии", body[1], maxsplit=1, flags=re.I)[0]
                if len(body) == 2
                else ""
            )
            return description, header
    return None


def normalize_compensation(
    job: JobRecord,
) -> tuple[CompensationRange | None, str | None, str | None, str | None]:
    sections = _employer_sections(job)
    structured = _structured(job.metadata) if sections is None else None
    matched, quote = None, None
    for text in _job_texts(job) if sections is None else sections:
        offer, fragment = _parse_text(text)
        if offer is None:
            continue
        if (
            matched is None
            or (structured and _same_pay(structured, offer))
            or (
                structured is None
                and matched.period is CompensationPeriod.UNKNOWN
                and _same_pay(matched, offer)
                and offer.period is not CompensationPeriod.UNKNOWN
            )
        ):
            matched, quote = offer, fragment
        if matched.period is not CompensationPeriod.UNKNOWN and (
            not structured or _same_pay(structured, matched)
        ):
            break
    if structured is not None:
        source = "compensation:structured_metadata"
        if matched and _thousandfold_range_conflict(structured, matched):
            return None, source, quote, "conflicting_salary_scale"
        if matched is not None and _same_pay(structured, matched):
            if matched.period is not CompensationPeriod.UNKNOWN:
                if structured.period not in {CompensationPeriod.UNKNOWN, matched.period}:
                    return None, source, quote, "conflicting_salary_period"
                structured = structured.model_copy(update={"period": matched.period})
            if structured.gross is None and matched.gross is not None:
                structured = structured.model_copy(update={"gross": matched.gross})
        else:
            if structured.gross is None:
                gross = _tax(
                    str(
                        job.metadata.get("salary_text")
                        or job.metadata.get("base_salary_text")
                        or ""
                    )
                )
                structured = structured.model_copy(update={"gross": gross})
            quote = None
        result = structured
    elif matched is not None:
        source, result = "compensation:parsed_from_description", matched
        previous = job.compensation
        if previous and _same_pay(previous, result) and result.gross is None:
            result = result.model_copy(update={"gross": previous.gross})
    else:
        if sections is not None:
            return None, "compensation:employer_sections", None, "market_estimate_without_offer"
        return None, None, None, None
    if _needs_pay_evidence(result) and not (
        matched
        and _same_pay(result, matched)
        and matched.period == result.period
        and matched.period is not CompensationPeriod.UNKNOWN
    ):
        return result, source, quote, "unverified_high_salary"
    return result, source, quote, None


def public_compensation(job: object) -> CompensationRange | None:
    """Require period evidence before displaying high monthly or ambiguous legacy pay."""
    offer = getattr(job, "compensation", None)
    metadata = getattr(job, "metadata", {})
    if offer is None or metadata.get("compensation_review_reason"):
        return None
    try:
        offer = CompensationRange.model_validate(offer, from_attributes=True)
    except ValidationError:
        return None
    sections = _employer_sections(job)
    if sections is not None:
        for section in sections:
            employer_offer, _ = _parse_text(section)
            if employer_offer and _same_pay(offer, employer_offer):
                return employer_offer
        return None
    if not _needs_pay_evidence(offer):
        if (
            offer.min_amount
            and offer.max_amount
            and offer.max_amount >= 1_000_000
            and not (offer.min_amount % 1_000 or offer.max_amount % 1_000)
        ):
            for source_text in _job_texts(job):
                actual, _ = _parse_text(source_text)
                if actual and _thousandfold_range_conflict(offer, actual):
                    return None
        return offer
    for text in (metadata.get("compensation_evidence"), *_job_texts(job)):
        if isinstance(text, str):
            actual, _ = _parse_text(text)
            if (
                actual
                and _same_pay(offer, actual)
                and actual.period is not CompensationPeriod.UNKNOWN
            ):
                return offer.model_copy(update={"period": actual.period})
    return None
