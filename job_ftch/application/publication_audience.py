"""Canonical facts and three-valued audience decisions for public recipients."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from job_ftch.application.geo import country_code, normalize_geo_chunk, normalize_geo_sources
from job_ftch.application.publication_labels import publication_grades
from job_ftch.domain.company import normalize_company_name
from job_ftch.domain.posting_language import posting_language
from job_ftch.domain.triage import explicit_non_vacancy

if TYPE_CHECKING:
    from job_ftch.domain.publication_policy import PublicationPolicy


def policy_for(runtime: object, target: str) -> PublicationPolicy | None:
    policies = getattr(getattr(runtime, "tenant", None), "publication_policies", {})
    return policies.get(target, policies.get("*")) if isinstance(policies, Mapping) else None


def _known(value: object) -> str | None:
    if not isinstance(value, str) or value.casefold() in {"unknown", "other", "n/a"}:
        return None
    return value.strip() or None


# Editorial facets group the existing, more specific ontology role families.
_PROFILE_PATTERNS = (
    (
        "manager",
        re.compile(
            r"\b(?:manager|management|product owner|менеджер\w*|продакт\w*|menejer\w*|gerente|gestionnaire)\b",
            re.I,
        ),
    ),
    ("devops", re.compile(r"\b(?:devops|mlops|sre|site reliability)\b", re.I)),
    (
        "qa",
        re.compile(r"\b(?:qa|sdet|quality assurance|quality engineer\w*|тестировщик\w*)\b", re.I),
    ),
    (
        "analyst",
        re.compile(
            r"\b(?:analysts?|analytics|аналитик\w*|аналітик\w*|талдаушы\w*|tahlilchi\w*|analist[ae]s?|analyste\w*)\b",
            re.I,
        ),
    ),
    (
        "architect",
        re.compile(r"\b(?:architect\w*|архитектор\w*|архітектор\w*|arquitect\w*)\b", re.I),
    ),
    ("security", re.compile(r"\b(?:security|безопасност\w*)\b", re.I)),
    (
        "data_ml",
        re.compile(
            r"\b(?:machine learning|ml engineer|data scien\w+|data engineer\w*|ai[ -](?:automation |agent )?(?:engineer\w*|инженер\w*)|artificial intelligence)\b",
            re.I,
        ),
    ),
    (
        "designer",
        re.compile(r"\b(?:designers?|design|дизайнер\w*|dizayner\w*|diseñador\w*)\b", re.I),
    ),
    (
        "developer",
        re.compile(
            r"\b(?:developers?|software (?:engineer\w*|development)|разработчик\w*|программист\w*|розробни\w*|әзірлеуші\w*|бағдарламашы\w*|dasturchi\w*|développeu\w*|desarrollador\w*|entwickler\w*)\b",
            re.I,
        ),
    ),
    (
        "engineer",
        re.compile(
            r"\b(?:engineers?|инженер\w*|інженер\w*|muhandis\w*|ingénieur\w*|ingenier[oa]s?)\b",
            re.I,
        ),
    ),
)
_PROFILE_LABELS = {
    "manager": "менеджер",
    "devops": "devops",
    "qa": "qa",
    "analyst": "аналитик",
    "data_ml": "data_ml",
    "designer": "дизайнер",
    "developer": "разработчик",
    "engineer": "инженер",
    "architect": "архитектор",
    "security": "безопасность",
    "research": "исследования",
    "operations": "операции",
    "legal": "юрист",
    "product": "продукт",
}
_TENTATIVE_MODE = re.compile(
    r"\b(?:onsite|on-site|hybrid|remote)\s*/\s*(?:onsite|on-site|hybrid|remote)\s*[-–—:]?\s*to be confirmed\b",
    re.I,
)
_RESIDENCE = re.compile(
    r"\b(?:you (?:must|have to) be based in|candidates must (?:be based|reside) in|кандидат должен проживать в)\s+([\w ,/-]{2,70})(?=[!.?;\n<]|$)",
    re.I,
)


def _profile(text: str) -> str | None:
    family = re.sub(r"[\s_/]+", " ", text.casefold()).strip()
    for profile, pattern in _PROFILE_PATTERNS:
        if pattern.search(family):
            return profile
    if family in {"data", "ai", "ai ml", "ai safety"}:
        return "data_ml"
    if family.endswith("engineering") or family == "automation":
        return "engineer"
    return family if family in _PROFILE_LABELS else None


def _profiles(job: object) -> tuple[str, ...]:
    for value in (
        getattr(job, "title_raw", None),
        getattr(job, "title", None),
        getattr(job, "role_family", None),
    ):
        if not (text := _known(value)):
            continue
        # AI/ML and CI/CD are specialisms within a title, not additional vacancies.
        compound = re.sub(
            r"\b(?:ai/ml|ml/ai|ci/cd|ui/ux|ux/ui)\b",
            lambda m: m[0].replace("/", " "),
            text,
            flags=re.I,
        )
        parts = re.split(
            r"\s*(?:[/|&;+–—]|\s-\s|(?<=[а-яё])-(?=[а-яё])|\s+(?:and|or|и|или|та|або|және|va|et|und|y)\s+)\s*",
            compound,
            flags=re.I,
        )
        matches = tuple(dict.fromkeys(role for part in parts if (role := _profile(part))))
        if len(matches) > 1:
            return matches
        if role := _profile(text):
            return (role,)
    return ()


@dataclass(frozen=True)
class PublicationFacts:
    grades: tuple[str, ...]
    roles: tuple[str, ...]
    country: str | None
    city: str | None
    languages: tuple[str, ...]
    work_mode: str | None
    location: str | None
    country_options: tuple[str, ...] = ()
    remote_restrictions: str | None = None
    hiring_countries: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()

    @property
    def grade(self) -> str | None:
        return next(iter(self.grades), None)

    @property
    def role(self) -> str | None:
        return next(iter(self.roles), None)

    def hashtags(self) -> tuple[str, ...]:
        values = [
            *self.grades,
            *(_PROFILE_LABELS[role] for role in self.roles),
            *self.hiring_countries,
            *self.regions,
            self.city,
        ]
        values.extend(f"язык_{code}" for code in self.languages)
        values.append(self.work_mode)
        return tuple(
            f"#{slug}"
            for value in values
            if value
            if (slug := re.sub(r"[^\w]+", "_", value.casefold()).strip("_"))
        )


def publication_facts(job: object) -> PublicationFacts:
    city, country, location = (getattr(job, key, None) for key in ("city", "country", "location"))
    geo = normalize_geo_sources((city, country) if city else (location, country))
    listed = normalize_geo_sources((location,)) if city and location else geo
    options = listed.country_options
    metadata = getattr(job, "metadata", {}) or {}
    recorded_locations = metadata.get("publication_locations")
    alternatives = [
        normalize_geo_sources(parts)
        for parts in (recorded_locations if isinstance(recorded_locations, (list, tuple)) else ())
        if isinstance(parts, (list, tuple)) and all(isinstance(s, str) or s is None for s in parts)
    ]
    countries = {g.country or country_code(g.city) for g in alternatives if g.country or g.city}
    cities = {g.city for g in alternatives if g.city}
    if len({country_code(value) for value in countries} - {None}) > 1:
        city = country = None
        options = ()
        geo = normalize_geo_sources(())
    elif len(cities) > 1:
        city = None
        geo = normalize_geo_sources((geo.country,))
    source_languages = [
        getattr(job, "language", None),
        *(getattr(job, "languages_detected", ()) or ()),
    ]
    if not any(_known(value) for value in source_languages):
        source_languages = [metadata.get("detected_language")]
    original = metadata.get("original_posting_text") or getattr(job, "description_raw", None)
    if original or not any(_known(value) for value in source_languages):
        inferred = posting_language(str(original or getattr(job, "description", "") or ""))
        if inferred.value != "unknown":
            declared = getattr(job, "languages_detected", ()) or ()
            source_languages = [inferred, *declared] if len(declared) > 1 else [inferred]
        elif original and re.search(r"(?:📢\s*)?Источник:\s*https?://", str(original), re.I):
            source_languages = []
    languages = tuple(
        dict.fromkeys(
            "kk" if value == "kz" else value
            for value in source_languages
            if isinstance(value, str) and re.fullmatch("[a-z]{2}", value)
        )
    )
    mode = _known(getattr(job, "work_mode", None))
    restrictions = _known(getattr(job, "remote_restrictions", None))
    if isinstance(original, str):
        if mode and _TENTATIVE_MODE.search(original):
            mode = None
        if not restrictions and (residence := _RESIDENCE.search(original)):
            restrictions = residence.group(1).strip()
    single_country = (
        None
        if options
        else (
            geo.country
            or normalize_geo_sources((_known(country),)).display
            or normalize_geo_chunk(country_code(geo.city) or "")
        )
    )
    single_city = None if options else geo.city or normalize_geo_sources((_known(city),)).display
    region_field = _known(getattr(job, "region", None))
    location_text = location or ""
    if (
        mode == "remote"
        and not restrictions
        and (_EXCLUSION.search(location_text) or _REMOTE_PREFIX.match(location_text))
    ):
        restrictions = location_text
    regions = _region_tags(
        restrictions or location_text, region_field if not restrictions else None
    )
    if not regions and mode == "remote" and not restrictions and _explicit_world(original):
        regions = ("глобал",)
    hiring_countries = options or ((single_country,) if single_country else ())
    if restrictions:
        # Office location is not evidence that remote applicants can live there.
        positive = _EXCLUSION.split(restrictions, maxsplit=1)[0]
        hiring_countries = tuple(
            dict.fromkeys(
                normalize_geo_chunk(code) or code
                for part in _place_tokens(positive)
                if not _region_tags(part) and (code := country_code(part, allow_city=True))
            )
        )
        if single_city and country_code(single_city) not in {
            country_code(c) for c in hiring_countries
        }:
            single_city = None
    hiring_countries = tuple(c for c in hiring_countries if not _region_tags(c))
    if single_country and _region_tags(single_country):
        regions = regions or _region_tags(single_country)
        single_country = None
    if single_city and _region_tags(single_city):
        single_city = None
    return PublicationFacts(
        grades=publication_grades(job, original),
        roles=_profiles(job),
        country=single_country,
        city=single_city,
        languages=languages,
        work_mode=mode,
        location=listed.display if options else geo.display,
        country_options=options,
        remote_restrictions=restrictions,
        hiring_countries=hiring_countries,
        regions=regions,
    )


_WORLD = {"worldwide", "anywhere in the world", "anywhere", "по всему миру", "из любой страны"}
_REGIONS = {
    **dict.fromkeys((*_WORLD, "global", "глобально", "глобал", "весь мир"), "глобал"),
    **dict.fromkeys(("europe", "европа", "европе"), "европа"),
    **dict.fromkeys(("eu", "евросоюз", "ес", "european union"), "ес"),
    **dict.fromkeys(("cis", "снг"), "снг"),
    **dict.fromkeys(("emea", "apac", "latam"), ""),  # These abbreviations are also public labels.
}
_EXCLUSION = re.compile(r"\b(?:except|excluding|outside(?: of)?|кроме|за исключением|вне)\b", re.I)
_REMOTE_WORLD = re.compile(
    r"\b(?:работа(?:ть)? из любой точки мира|(?:remote(?: work)?|work remotely|удал[её]н(?:ка|но))\s*[(:—–-]?\s*"
    r"(?:(?:from|из)\s+)?(?:worldwide|anywhere in the world|глобально|по всему миру|любой страны))\b",
    re.I,
)
_REMOTE_PREFIX = re.compile(r"^(?:remote|удал[её]нно|удал[её]нка)\b[\s(:–—-]+", re.I)
_QUALIFIERS = re.compile(
    r"^(?:only in|only|based in|только из|только в)\s+|\s+(?:only|только)$", re.I
)
_TRAVEL_NOTE = re.compile(
    r";\s*(?:(?:(?:occasional|international|business)\s+)*travel (?:is )?required|"
    r"командировки в [а-яё-]+(?: \d+ раз\w* в (?:неделю|месяц|год))?)\s*[.!]?$",
    re.I,
)


def _place_tokens(text: str) -> list[str]:
    text = re.sub(
        r"\s*\((?:hybrid|remote|on[ -]?site|гибрид|офис|удал[её]нно)\)\s*$", "", text, flags=re.I
    )
    return [
        _QUALIFIERS.sub("", _REMOTE_PREFIX.sub("", part.strip()).strip(" .()")).strip()
        for part in re.split(
            r"\s*[,;/|]\s*|\s+(?:or|and|или|и)\s+", _TRAVEL_NOTE.sub("", text.strip()), flags=re.I
        )
    ]


def _region_tags(*values: str | None) -> tuple[str, ...]:
    result = []
    for value in values:
        if not value or _EXCLUSION.search(value):
            continue
        for part in _place_tokens(value):
            key = part.casefold()
            if key in _REGIONS:
                result.append(_REGIONS[key] or key)
    return tuple(dict.fromkeys(result))


def _explicit_world(original: object) -> bool:
    if not isinstance(original, str) or not (world := _REMOTE_WORLD.search(original)):
        return False
    return not (
        re.search(
            r"\b(?:not|never|нельзя|невозможна|не допускается)\s*$",
            original[max(0, world.start() - 28) : world.start()],
            re.I,
        )
        or re.search(
            r"\b(?:except|excluding|but|not|кроме|но|исключением|только|невозможн\w*|запрещ\w*)\b",
            original[world.end() : world.end() + 100],
            re.I,
        )
    )


_LANGUAGE_NAMES = {
    "ru": ("русский", "russian", "русскоязычная"),
    "en": ("английский", "english", "англоязычная"),
    "de": ("немецкий", "german", "deutsch", "немецкоязычная"),
    "fr": ("французский", "french", "français", "франкоязычная"),
    "es": ("испанский", "spanish", "español", "испаноязычная"),
    "kk": ("казахский", "kazakh", "қазақша"),
    "uk": ("украинский", "ukrainian", "українська"),
    "uz": ("узбекский", "uzbek", "o‘zbek"),
}
_DECLARATION = re.compile(
    r"\b(?:рабочий язык (?P<ru_scope>компании|команды)\s*[-—–:]\s*|"
    r"(?P<en_scope>company|team)(?:['’]s)? working language (?:is|:)\s*)(?P<name>[\w’‘'-]+)"
    r"|\b(?:мы\s*[-—–:]?\s*|наша\s+|we (?:are|have) an?\s+)(?P<adj>[\w’‘']+)(?:[- ]speaking\s+(?P<en_noun>company|team)|\s+(?P<ru_noun>компания|команда))\b",
    re.I,
)


def _employer_declaration(body: str, policy: PublicationPolicy) -> str | None:
    names = {
        term.casefold()
        for code in policy.company_languages
        for term in (code, *_LANGUAGE_NAMES.get(code, ()), *policy.language_aliases.get(code, ()))
    }
    for match in _DECLARATION.finditer(body):
        scope = (
            match["ru_scope"] or match["en_scope"] or match["en_noun"] or match["ru_noun"]
        ).casefold()
        if scope in {"team", "команды", "команда"} and not policy.accept_team_language:
            continue
        if (match["name"] or match["adj"]).casefold() in names:
            return match.group()
    return None


def _region_eligible(regions: tuple[str, ...], policy: PublicationPolicy) -> bool | None:
    if "глобал" in regions:
        return True
    members = {
        label: countries
        for key, countries in policy.region_countries.items()
        for label in _region_tags(key)
    }
    if any(
        set(members.get(label, ())).intersection(policy.eligible_countries) for label in regions
    ):
        return True
    return False if regions and all(label in members for label in regions) else None


def _eligible(job: object, facts: PublicationFacts, policy: PublicationPolicy) -> bool | None:
    allowed = policy.eligible_countries
    restriction = facts.remote_restrictions
    if restriction:
        if restriction.strip().casefold() in _WORLD:
            return True
        exclusion = _EXCLUSION.split(restriction, maxsplit=1)
        if len(exclusion) == 2:
            excluded: set[str] = set()
            for token in _place_tokens(exclusion[1]):
                if region := _region_tags(token):
                    groups = [
                        codes
                        for name, codes in policy.region_countries.items()
                        if _region_tags(name) == region
                    ]
                    if not groups:
                        return None
                    excluded.update(code for codes in groups for code in codes)
                elif code := country_code(token):
                    excluded.add(code)
                else:
                    return None
            if set(allowed).issubset(excluded):
                return False
            if "глобал" in _region_tags(exclusion[0]):
                return True
            return None
        checks = []
        for token in _place_tokens(restriction):
            if regions := _region_tags(token):
                checks.append(_region_eligible(regions, policy))
            elif code := country_code(token):
                checks.append(code in allowed)
            else:
                return None
        return True if True in checks else (None if None in checks else False)
    if facts.work_mode == "remote":
        if (getattr(job, "location", "") or "").strip().casefold() in _WORLD:
            return True
        return _region_eligible(facts.regions, policy)
    if facts.country_options:
        return any(country_code(place) in allowed for place in facts.country_options)
    code = country_code(facts.country) or country_code(facts.city)
    return code in allowed if code else _region_eligible(facts.regions, policy)


@dataclass(frozen=True)
class AudienceDecision:
    status: Literal["allow", "skip", "review"]
    reasons: tuple[str, ...]
    evidence: dict[str, str]


def evaluate_audience(job: object, policy: PublicationPolicy) -> AudienceDecision:
    metadata = getattr(job, "metadata", {}) or {}
    original = metadata.get("original_posting_text") or getattr(job, "description_raw", None)
    if isinstance(original, str):
        reason = explicit_non_vacancy(str(getattr(job, "source_kind", "")), original)
        if reason:
            return AudienceDecision("skip", (reason.value,), {})
    facts = publication_facts(job)
    checks: dict[str, bool | None] = {}
    evidence: dict[str, str] = {}
    if policy.eligible_countries:
        for key, value in (
            ("country", facts.country),
            ("city", facts.city),
            ("work_mode", facts.work_mode),
            ("remote_restrictions", facts.remote_restrictions),
        ):
            if value:
                evidence[key] = str(value)[:180]
        if facts.country_options:
            evidence["country_options"] = ", ".join(facts.country_options)
        if facts.regions:
            evidence["regions"] = ", ".join(facts.regions)
        if policy.country_basis == "office":
            code = country_code(facts.country) or country_code(facts.city)
            offices = {country_code(place) for place in facts.country_options} or (
                {code} if code else set()
            )
            checks["office_country"] = (
                bool(offices.intersection(policy.eligible_countries))
                if offices and facts.work_mode in {"hybrid", "onsite"}
                else None
            )
        else:
            checks["eligible_country"] = _eligible(job, facts, policy)
    if policy.posting_languages:
        if facts.languages:
            evidence["posting_languages"] = ",".join(facts.languages)
        checks["posting_language"] = (
            bool(set(facts.languages).intersection(policy.posting_languages))
            if facts.languages
            else None
        )
    if policy.company_languages:
        names = {
            normalize_company_name(str(value))
            for value in (getattr(job, "company_canonical", None), getattr(job, "company", None))
            if value
        }
        employer = next(
            (
                record
                for k, record in policy.verified_employers.items()
                if normalize_company_name(k) in names
            ),
            None,
        )
        body = original or getattr(job, "description", "") or ""
        declaration = _employer_declaration(body, policy)
        if employer:
            evidence["employer_source"] = str(employer.source)
            evidence["employer_checked_on"] = employer.checked_on.isoformat()
            checks["verified_employer_language"] = bool(
                set(employer.languages).intersection(policy.company_languages)
            )
        elif declaration:
            evidence["employer_declaration"] = declaration
            checks["declared_employer_language"] = True
        else:
            checks["verified_employer_language"] = None
    labels = {True: "yes", False: "no", None: "unknown"}
    reasons = tuple(f"{key}={labels[value]}" for key, value in checks.items())
    if policy.require_country:
        location_match = checks.pop(
            "office_country" if policy.country_basis == "office" else "eligible_country"
        )
        if location_match is False:
            return AudienceDecision("skip", reasons, evidence)
        if location_match is None:
            return AudienceDecision(policy.insufficient_evidence, reasons, evidence)
    values = checks.values()
    if (
        not checks
        or (policy.match == "any" and True in values)
        or all(value is True for value in values)
    ):
        return AudienceDecision("allow", reasons, evidence)
    if (policy.match == "all" and False in values) or None not in values:
        return AudienceDecision("skip", reasons, evidence)
    return AudienceDecision(policy.insufficient_evidence, reasons, evidence)
