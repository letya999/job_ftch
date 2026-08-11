"""Deterministic geo normalisation shared by pipeline, cards, and traces."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_GEO_SPLIT = re.compile(r"\s*[;,/|&]\s*|\s+[-–—]\s+")
_SETTLEMENT_PREFIX = re.compile(r"^(?:г|гор|пос|пгт|с|д|ст)\.?\s+", re.IGNORECASE)
_PAREN_NOTE = re.compile(r"\s*\([^)]*\)")

_GEO_NOISE = frozenset(
    {
        "office",
        "офис",
        "remote",
        "hybrid",
        "onsite",
        "удалённо",
        "удаленно",
        "удалённая работа",
        "удаленная работа",
        "гибрид",
        "разные локации",
        "не указано",
        "n/a",
    }
)

_COUNTRY_ALIASES = {
    "ru": "Россия",
    "рф": "Россия",
    "rф": "Россия",
    "russia": "Россия",
    "russian federation": "Россия",
    "pl": "Польша",
    "poland": "Польша",
    "polska": "Польша",
    "kz": "Казахстан",
    "kazakhstan": "Казахстан",
    "by": "Беларусь",
    "belarus": "Беларусь",
    "germany": "Германия",
    "serbia": "Сербия",
    "georgia": "Грузия",
    "armenia": "Армения",
    "cyprus": "Кипр",
    "united kingdom": "Великобритания",
    "uk": "Великобритания",
    "united states": "США",
    "usa": "США",
    "us": "США",
}

_CITY_ALIASES = {
    "moscow": "Москва",
    "saint petersburg": "Санкт-Петербург",
    "st petersburg": "Санкт-Петербург",
    "spb": "Санкт-Петербург",
    "novosibirsk": "Новосибирск",
    "yekaterinburg": "Екатеринбург",
    "ekaterinburg": "Екатеринбург",
    "kazan": "Казань",
    "almaty": "Алматы",
    "astana": "Астана",
    "minsk": "Минск",
    "belgrade": "Белград",
    "tbilisi": "Тбилиси",
    "yerevan": "Ереван",
    "warsaw": "Варшава",
    "warszawa": "Варшава",
}

_REGION_ALIASES = {
    "worldwide": "по всему миру",
    "europe": "Европа",
}

_CITY_COUNTRY = {
    "Москва": "Россия",
    "Санкт-Петербург": "Россия",
    "Новосибирск": "Россия",
    "Екатеринбург": "Россия",
    "Казань": "Россия",
    "Алматы": "Казахстан",
    "Астана": "Казахстан",
    "Минск": "Беларусь",
    "Белград": "Сербия",
    "Тбилиси": "Грузия",
    "Ереван": "Армения",
    "Варшава": "Польша",
}


@dataclass(frozen=True)
class GeoNormalization:
    display: str | None
    city: str | None = None
    country: str | None = None
    corrections: tuple[str, ...] = ()


def normalize_geo_sources(sources: Iterable[str | None]) -> GeoNormalization:
    seen: list[str] = []
    corrections: list[str] = []
    for source in sources:
        if not source:
            continue
        for chunk in _GEO_SPLIT.split(source):
            normalized = normalize_geo_chunk(chunk)
            if normalized and normalized.casefold() not in {item.casefold() for item in seen}:
                seen.append(normalized)

    city = next((item for item in seen if item in _CITY_COUNTRY), None)
    country = next((item for item in seen if item in set(_COUNTRY_ALIASES.values())), None)
    expected_country = _CITY_COUNTRY.get(city or "")
    if expected_country and country and country != expected_country:
        seen = [expected_country if item == country else item for item in seen]
        corrections.append(f"country:{country}->{expected_country}")
        country = expected_country

    return GeoNormalization(
        display=", ".join(seen[:3]) if seen else None,
        city=city,
        country=country,
        corrections=tuple(corrections),
    )


def normalize_geo_chunk(chunk: str) -> str | None:
    cleaned = _PAREN_NOTE.sub("", chunk).strip(" .,")
    if not cleaned:
        return None
    cleaned = _SETTLEMENT_PREFIX.sub("", cleaned).strip()
    lowered = cleaned.lower()
    if lowered in _GEO_NOISE:
        return None
    if lowered in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[lowered]
    if lowered in _CITY_ALIASES:
        return _CITY_ALIASES[lowered]
    if lowered in _REGION_ALIASES:
        return _REGION_ALIASES[lowered]
    for noise in _GEO_NOISE:
        if lowered.startswith(noise + " "):
            return normalize_geo_chunk(cleaned[len(noise) :])
    return cleaned or None
