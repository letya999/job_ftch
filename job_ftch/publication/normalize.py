"""Deterministic normalisation helpers for publication fields.

No LLM, no I/O - pure functions over structured JobRecord/Job fields.
Currency symbols come from a configurable table, not hardcoded.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from job_ftch.application.geo import normalize_geo_sources

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

# Controlled vocabulary. These are channel chrome, not posting content, so
# they stay in one language regardless of the posting's own - a feed that says
# "офис" on one card and "onsite" on the next reads as broken. Posting content
# (title, requirements, stack) keeps its source language.
PERIOD_LABELS: dict[str, str] = {
    "hour": "/час",
    "day": "/день",
    "week": "/нед",
    "month": "/мес",
    "year": "/год",
    "project": "/проект",
}

WORK_MODE_LABELS: dict[str, str] = {
    "remote": "удалённо",
    "hybrid": "гибрид",
    "onsite": "офис",
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
        rng = f"до {_fmt_amount(hi)}"

    period = ""
    if comp.period and hasattr(comp.period, "value") and comp.period.value != "unknown":
        period = PERIOD_LABELS.get(comp.period.value, f"/{comp.period.value}")

    gross_mark = ""
    if comp.gross is True:
        gross_mark = " до вычета"
    elif comp.gross is False:
        gross_mark = " на руки"

    return f"{rng} {sym}{period}{gross_mark}".strip()


def format_geo(job: Job | JobRecord) -> str | None:
    """Where the job is, from whichever field the parser actually filled.

    Site parsers populate the free-text ``location`` ("Moscow; Saint Petersburg;
    Belgrade") far more often than the structured ``city``/``country`` pair, and
    reading only the latter dropped the geo from most career-site cards.
    """
    sources = [
        (getattr(job, "city", None) or "").strip(),
        (getattr(job, "country", None) or "").strip(),
    ]
    if not any(sources):
        sources = [(getattr(job, "location", None) or "").strip()]

    return normalize_geo_sources(sources).display


def format_work_mode(job: Job | JobRecord) -> str | None:
    work_mode = getattr(job, "work_mode", None)
    value = work_mode.value if work_mode and hasattr(work_mode, "value") else ""
    if not value or value == "unknown":
        return None
    return WORK_MODE_LABELS.get(value, value)


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


TELEGRAM_HOSTS = frozenset({"t.me", "telegram.me"})

# Telegram public usernames: 5-32 chars, letter-initial, [A-Za-z0-9_].
# Anything else in that slot is not a public handle: a leading "+" or a
# joinchat route carries a private invite token, and a numeric channel
# route carries an internal id. Publishing either would put a joinable
# secret into a public post, so only a real handle is ever surfaced.
_TG_PUBLIC_HANDLE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")

# Reserved first segments that are routes, not channel handles.
_TG_RESERVED = frozenset(
    {"joinchat", "c", "s", "addstickers", "addtheme", "proxy", "socks", "share"}
)


def pick_source_label(job: Job | JobRecord) -> str | None:
    """Footer label: the origin domain, so readers see where a job came from.

    Telegram links keep their channel handle (``t.me/ml_jobs_kz``) - the bare
    host would collapse every channel into one indistinguishable label. Private
    invite links carry no publishable handle and fall back to the host.
    Falls back to ``source_name`` when there is no usable URL.
    """
    url = resolve_card_url(job)
    if url:
        try:
            parsed = urlparse(url if "//" in url else f"https://{url}")
            host = (parsed.hostname or "").removeprefix("www.")
            if host in TELEGRAM_HOSTS:
                handle = parsed.path.strip("/").split("/")[0].rstrip(".,;:!?)")
                if _TG_PUBLIC_HANDLE.match(handle) and handle.lower() not in _TG_RESERVED:
                    return f"{host}/{handle}"
            if host:
                return host
        except ValueError:
            pass
    name = getattr(job, "source_name", None)
    return name.strip() if name else None


def detect_content_language(job: Job | JobRecord) -> str:
    """Detect language of the card content from requirements and description.

    Uses requirements_must as the primary signal (that's what gets prefixed),
    falls back to description. If both are ambiguous, trusts job.language.
    """
    lang_attr = getattr(job, "language", None)
    base_lang = lang_attr.value if lang_attr and hasattr(lang_attr, "value") else "en"
    if base_lang == "unknown":
        base_lang = "en"

    reqs = getattr(job, "requirements_must", None) or ()
    req_text = " ".join(str(r) for r in reqs[:5])
    desc_text = (getattr(job, "description", "") or "")[:500]
    sample = req_text if req_text else desc_text
    if not sample:
        return base_lang

    cyrillic = sum(1 for c in sample if "Ѐ" <= c <= "ӿ")
    latin = sum(1 for c in sample if "a" <= c.lower() <= "z")
    total = cyrillic + latin
    if total == 0:
        return base_lang
    ratio = cyrillic / total
    if ratio > 0.3:
        return "ru"
    if ratio < 0.05:
        return "en"
    return base_lang


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
