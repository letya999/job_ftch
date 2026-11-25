"""Canonical facts and three-valued audience decisions for public recipients."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from job_ftch.application.geo import country_code, normalize_geo_chunk, normalize_geo_sources
from job_ftch.domain.company import normalize_company_name
from job_ftch.domain.models import Seniority
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
        re.compile(r"\b(?:manager|management|product owner|менеджер\w*|продакт\w*)\b", re.I),
    ),
    ("devops", re.compile(r"\b(?:devops|mlops|sre|site reliability)\b", re.I)),
    (
        "qa",
        re.compile(r"\b(?:qa|sdet|quality assurance|quality engineer\w*|тестировщик\w*)\b", re.I),
    ),
    ("analyst", re.compile(r"\b(?:analysts?|analytics|аналитик\w*)\b", re.I)),
    ("architect", re.compile(r"\b(?:architect\w*|архитектор\w*)\b", re.I)),
    ("security", re.compile(r"\b(?:security|безопасност\w*)\b", re.I)),
    (
        "data_ml",
        re.compile(
            r"\b(?:machine learning|ml engineer|data scien\w+|data engineer\w*|ai[ -](?:automation |agent )?(?:engineer\w*|инженер\w*)|artificial intelligence)\b",
            re.I,
        ),
    ),
    ("designer", re.compile(r"\b(?:designer|design|дизайнер\w*)\b", re.I)),
    (
        "developer",
        re.compile(
            r"\b(?:developers?|software (?:engineer\w*|development)|разработчик\w*|программист\w*)\b",
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
_GRADES = {grade.value for grade in Seniority if grade != Seniority.UNKNOWN}
_GRADE_FIELD = re.compile(r"<dt>\s*(?:level|seniority|грейд)\s*</dt>\s*<dd>\s*(\w+)\s*</dd>", re.I)
_SOURCE_TITLE_GRADE = re.compile(r"\bjob (?:title|description)\s*:\s*(\w+)\s+[\w/-]+", re.I)
_TENTATIVE_MODE = re.compile(
    r"\b(?:onsite|on-site|hybrid|remote)\s*/\s*(?:onsite|on-site|hybrid|remote)\s*[-–—:]?\s*to be confirmed\b",
    re.I,
)
_RESIDENCE = re.compile(
    r"\b(?:you (?:must|have to) be based in|candidates must (?:be based|reside) in|кандидат должен проживать в)\s+([\w ,/-]{2,70})(?=[!.?;\n<]|$)",
    re.I,
)


def _profile(job: object) -> str | None:
    role = _known(getattr(job, "role_family", None))
    title = getattr(job, "title", None)
    # Some boards file AI workflow automation under QA despite an explicit engineering title.
    for text in (title, role) if role == "qa" else (role, title):
        if text:
            normalized = re.sub(r"[_/]+", " ", text)
            for profile, pattern in _PROFILE_PATTERNS:
                if pattern.search(normalized):
                    return profile
    if role:
        family = re.sub(r"[\s_/]+", " ", role.casefold()).strip()
        if family in {"data", "ai", "ai ml", "ai safety"}:
            return "data_ml"
        if family.endswith("engineering") or family == "automation":
            return "engineer"
        if family in _PROFILE_LABELS:
            return family
    return None


@dataclass(frozen=True)
class PublicationFacts:
    grade: str | None
    role: str | None
    country: str | None
    city: str | None
    languages: tuple[str, ...]
    work_mode: str | None
    location: str | None
    country_options: tuple[str, ...] = ()
    remote_restrictions: str | None = None

    def hashtags(self) -> tuple[str, ...]:
        values = [
            self.grade,
            _PROFILE_LABELS.get(self.role or "", self.role),
            self.country,
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
    options = normalize_geo_sources((location,)).country_options if city else geo.country_options
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
    grade = _known(getattr(job, "seniority", None))
    if not grade:
        title_grades = _GRADES.intersection(
            re.findall(r"\w+", (getattr(job, "title", None) or "").casefold())
        )
        if len(title_grades) == 1:
            grade = title_grades.pop()
        elif isinstance(original, str) and (
            field := _GRADE_FIELD.search(original) or _SOURCE_TITLE_GRADE.search(original)
        ):
            value = field.group(1).casefold()
            grade = value if value in _GRADES else None
    mode = _known(getattr(job, "work_mode", None))
    restrictions = _known(getattr(job, "remote_restrictions", None))
    if isinstance(original, str):
        if mode and _TENTATIVE_MODE.search(original):
            mode = None
        if not restrictions and (residence := _RESIDENCE.search(original)):
            restrictions = residence.group(1).strip()
    return PublicationFacts(
        grade=grade,
        role=_profile(job),
        country=None
        if options
        else (
            geo.country
            or normalize_geo_sources((_known(country),)).display
            or normalize_geo_chunk(country_code(geo.city) or "")
        ),
        city=None if options else geo.city or normalize_geo_sources((_known(city),)).display,
        languages=languages,
        work_mode=mode,
        location=location if options else geo.display,
        country_options=options,
        remote_restrictions=restrictions,
    )


_WORLD = {"worldwide", "anywhere in the world", "anywhere", "по всему миру", "из любой страны"}
_REMOTE_WORLD = re.compile(
    r"\b(?:remote(?: work)?|work remotely|удал[её]н(?:ка|но))\s*[(:—–-]?\s*"
    r"(?:(?:from|из)\s+)?(?:worldwide|anywhere in the world|глобально|по всему миру|любой страны)\b",
    re.I,
)
_QUALIFIERS = re.compile(r"^(?:only in|only|только из|только в)\s+|\s+(?:only|только)$", re.I)
_COMPANY_RU = re.compile(
    r"\b(?:рабочий язык компании\s*[-—–:]\s*русский"
    r"|company(?:'s)? working language is russian|мы\s*[-—–:]?\s*русскоязычная компания)\b",
    re.I,
)
_TEAM_RU = re.compile(
    r"\b(?:рабочий язык команды\s*[-—–:]\s*русский|team(?:'s)? working language is russian"
    r"|(?:мы\s*[-—–:]?\s*русскоязычная|наша русскоязычная) команда"
    r"|we (?:are|have) a russian[- ]speaking team)\b",
    re.I,
)


def _eligible(job: object, facts: PublicationFacts, allowed: tuple[str, ...]) -> bool | None:
    restriction = facts.remote_restrictions
    if restriction:
        if restriction.strip().casefold() in _WORLD:
            return True
        exclusion = re.split(
            r"\b(?:except|excluding|outside(?: of)?|кроме|за исключением|вне)\b",
            restriction,
            maxsplit=1,
            flags=re.I,
        )
        if len(exclusion) == 2:
            excluded = {
                country_code(s.strip()) for s in re.split(r"[,;/]|\s+(?:and|и)\s+", exclusion[1])
            }
            if set(allowed).issubset(excluded):
                return False
            if None not in excluded and exclusion[0].strip().casefold() in _WORLD:
                return True
            return None
        tokens = re.split(r"\s*[,;/|]\s*|\s+(?:or|или)\s+", restriction.strip())
        codes = [country_code(_QUALIFIERS.sub("", token).strip()) for token in tokens]
        if any(code in allowed for code in codes):
            return True if all(codes) else None
        return False if all(codes) else None
    if facts.work_mode == "remote":
        if (getattr(job, "location", "") or "").strip().casefold() in _WORLD:
            return True
        metadata = getattr(job, "metadata", {}) or {}
        original = (
            metadata.get("original_posting_text") or getattr(job, "description_raw", None) or ""
        )
        world = _REMOTE_WORLD.search(original) if isinstance(original, str) else None
        if world and not re.search(
            r"\b(?:except|excluding|but|not|кроме|исключением|только)\b",
            original[world.end() : world.end() + 100],
            re.I,
        ):
            return True
        return None
    if facts.country_options:
        return any(country_code(place) in allowed for place in facts.country_options)
    code = country_code(facts.country) or country_code(facts.city)
    return code in allowed if code else None


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
            checks["eligible_country"] = _eligible(job, facts, policy.eligible_countries)
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
        body = getattr(job, "description_raw", None) or getattr(job, "description", "") or ""
        declaration = _COMPANY_RU.search(body)
        if declaration is None and policy.accept_team_language:
            declaration = _TEAM_RU.search(body)
        if employer:
            evidence["employer_source"] = str(employer.source)
            evidence["employer_checked_on"] = employer.checked_on.isoformat()
            checks["verified_employer_language"] = bool(
                set(employer.languages).intersection(policy.company_languages)
            )
        elif declaration:
            evidence["employer_declaration"] = declaration.group()
            checks["declared_employer_language"] = (
                True if "ru" in policy.company_languages else None
            )
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
