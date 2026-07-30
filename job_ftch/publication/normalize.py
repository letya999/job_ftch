"""Deterministic normalisation helpers for publication fields.

No LLM, no I/O - pure functions over structured JobRecord/Job fields.
Currency symbols come from a configurable table, not hardcoded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_ftch.domain import Job, JobRecord

CURRENCY_SYMBOLS: dict[str, str] = {
    "RUB": "₽",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "KZT": "₸",
    "UAH": "₴",
    "BYN": "Br",
    "GEL": "₾",
    "TRY": "₺",
    "PLN": "zł",
    "CZK": "Kč",
    "ILS": "₪",
    "AED": "AED",
    "SGD": "S$",
    "JPY": "¥",
    "CNY": "¥",
    "INR": "₹",
    "BRL": "R$",
    "CAD": "C$",
    "AUD": "A$",
    "CHF": "CHF",
}

PERIOD_LABELS: dict[str, str] = {
    "hour": "/час",
    "day": "/день",
    "week": "/нед",
    "month": "/мес",
    "year": "/год",
    "project": "/проект",
}


def _fmt_amount(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")


def format_compensation(job: Job | JobRecord) -> str | None:
    comp = getattr(job, "compensation", None)
    if comp is None:
        return None

    lo = comp.min_amount
    hi = comp.max_amount
    if lo is None and hi is None:
        return None

    sym = CURRENCY_SYMBOLS.get(comp.currency, comp.currency)

    if lo is not None and hi is not None:
        rng = _fmt_amount(lo) if lo == hi else f"{_fmt_amount(lo)}–{_fmt_amount(hi)}"
    elif lo is not None:
        rng = f"от {_fmt_amount(lo)}"
    else:
        rng = f"до {_fmt_amount(hi)}"  # type: ignore[arg-type]

    period = ""
    if comp.period and hasattr(comp.period, "value") and comp.period.value != "unknown":
        period = PERIOD_LABELS.get(comp.period.value, f"/{comp.period.value}")

    gross_mark = ""
    if comp.gross is True:
        gross_mark = " gross"
    elif comp.gross is False:
        gross_mark = " net"

    return f"{rng} {sym}{period}{gross_mark}".strip()


def format_location(job: Job | JobRecord) -> str | None:
    parts: list[str] = []
    city = getattr(job, "city", None)
    country = getattr(job, "country", None)
    work_mode = getattr(job, "work_mode", None)

    if city:
        parts.append(city.strip())
    if country and country.strip().lower() not in (
        (city or "").strip().lower(),
        "",
    ):
        parts.append(country.strip())

    wm_str = work_mode.value if work_mode and hasattr(work_mode, "value") else ""
    if wm_str and wm_str != "unknown":
        wm_labels = {
            "remote": "удалённо",
            "hybrid": "гибрид",
            "onsite": "офис",
        }
        parts.append(wm_labels.get(wm_str, wm_str))

    return " · ".join(parts) if parts else None


def resolve_card_url(job: Job | JobRecord) -> str | None:
    url = getattr(job, "canonical_url", None)
    if not url:
        urls = getattr(job, "urls", None)
        if urls:
            url = urls[0]
    if not url:
        return None
    url_str = str(url).strip()
    safe_schemes = ("https://", "http://", "t.me/", "telegram.me/", "tg://")
    return url_str if any(url_str.startswith(s) for s in safe_schemes) else None


def pick_source_label(job: Job | JobRecord) -> str | None:
    name = getattr(job, "source_name", None)
    return name.strip() if name else None


def summarise_requirements(job: Job | JobRecord) -> str | None:
    reqs = getattr(job, "requirements_must", None) or ()
    if not reqs:
        return None
    joined = ", ".join(r.strip() for r in reqs[:5] if r.strip())
    return joined[:300] or None


def summarise_stack(job: Job | JobRecord) -> str | None:
    tools = getattr(job, "tools_stack", None) or ()
    if not tools:
        skills = getattr(job, "skills_explicit", None) or ()
        names = [getattr(s, "canonical_name", str(s)).strip() for s in skills[:8] if s]
        if not names:
            return None
        return " · ".join(names)[:200] or None
    return " · ".join(t.strip() for t in tools[:8] if t.strip())[:200] or None
