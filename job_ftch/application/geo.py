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
    "uz": "Узбекистан",
    "uzbekistan": "Узбекистан",
    "tj": "Таджикистан",
    "tajikistan": "Таджикистан",
    "az": "Азербайджан",
    "azerbaijan": "Азербайджан",
    "am": "Армения",
    "ge": "Грузия",
    "cy": "Кипр",
    "de": "Германия",
    "rs": "Сербия",
    "gb": "Великобритания",
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
    "tashkent": "Ташкент",
}

_COUNTRY_ALIASES.update({name.casefold(): name for name in _COUNTRY_ALIASES.values()})
_CITY_ALIASES.update({name.casefold(): name for name in _CITY_ALIASES.values()})

_REGION_ALIASES = {
    "worldwide": "по всему миру",
    "europe": "Европа",
}

# ponytail: only unambiguous supported cities; unknown cities remain unverified.
_CITY_COUNTRIES = {
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
    "Ташкент": "Узбекистан",
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

    country = next((item for item in seen if item in set(_COUNTRY_ALIASES.values())), None)
    known_cities = [item for item in seen if item in _CITY_COUNTRIES]
    countries = [item for item in seen if item in set(_COUNTRY_ALIASES.values())]
    if len(known_cities) == 1 and len(countries) == 1:
        expected_country = _CITY_COUNTRIES[known_cities[0]]
        if country != expected_country:
            corrections.append(f"country:city_conflict:{country}->{expected_country}")
            seen[seen.index(countries[0])] = expected_country
            country = expected_country
    city = None
    if len(known_cities) == 1:
        city = known_cities[0]
    if country:
        country_index = seen.index(country)
        city = city or next(
            (
                item
                for item in seen[:country_index]
                if item not in _GEO_NOISE and item not in _REGION_ALIASES.values()
            ),
            None,
        )

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
    if not any(char.isalpha() for char in cleaned):
        return None
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
        if lowered.endswith(" " + noise):
            return normalize_geo_chunk(cleaned[: -len(noise)])
    return cleaned or None
