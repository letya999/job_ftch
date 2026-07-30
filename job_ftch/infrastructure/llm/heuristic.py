"""Deterministic extraction backend for local runs and tests.

Per ADR-019: this provider is the **primary** extraction. LLM augmentation is
applied only in the rare cases where a field is critical and heuristic cannot
fill it (e.g. ``company`` on a Telegram message with no metadata).

Skill/role/seniority extraction is **ontology-driven**: when an
``OntologyStore`` is supplied, the live ontology feeds ``_extract_skill_tags``
and ``_detect_seniority``. Otherwise a minimal built-in snapshot is used.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from job_ftch.application.registry import register_llm
from job_ftch.domain import EmploymentType, LanguageCode, PostType, Seniority, SkillTag, WorkMode

if TYPE_CHECKING:
    from job_ftch.application.contracts import OntologyStore
    from job_ftch.config import Settings

_TITLE_RE = re.compile(r"^[^\n:]{4,120}$")

# Compensation: currency may appear BEFORE or AFTER digits.
# Common patterns:
#   "1,000,000 to 1,500,000 KZT"            → currency after range
#   "100 000 — 200 000 ₸"                    → currency after range
#   "$3K - $3.75K"                           → currency before range (uncommon)
#   "USD 120000 - 160000"                    → currency before, range with two values
#   "от 200К"                                → single number, no currency symbol
#   "500,000"                                → no range, no currency (currency optional)
# Three alternatives: (1) range-then-currency, (2) currency-then-range, (3) currency-then-single.
_COMPENSATION_RE = re.compile(
    r"(?:"
    # (1) range first, currency at the end
    r"(?P<min1>[\d][\d\s,.]{1,15})"
    r"(?:\s*(?:-|to|–|—)\s*(?P<max1>[\d][\d\s,.]{1,15}))?"
    r"\s*(?P<cur_after>KZT|USD|EUR|RUB|GBP|₸|\$|€|£|kzt|usd|eur|rub|gbp)\b"
    r"|"
    # (2) currency first, then range
    r"(?P<cur_b1>KZT|USD|EUR|RUB|GBP|₸|\$|€|£)\s*"
    r"(?P<min_b1>[\d][\d\s,.]{1,15})"
    r"(?:\s*(?:-|to|–|—)\s*(?P<max_b1>[\d][\d\s,.]{1,15}))?"
    r"|"
    # (3) currency first, then single number
    r"(?P<cur_b2>KZT|USD|EUR|RUB|GBP|₸|\$|€|£)\s*"
    r"(?P<min_b2>[\d][\d\s,.]{1,15})"
    r")",
    re.IGNORECASE,
)


# Built-in fallback ontology (used when no live store is supplied).
_BUILTIN_SKILLS = (
    "python",
    "pytorch",
    "tensorflow",
    "docker",
    "kubernetes",
    "rag",
    "sql",
    "airflow",
    "fastapi",
    "llm",
    "nlp",
    "react",
    "typescript",
    "javascript",
    "java",
    "go",
    "rust",
    "kotlin",
    "scala",
    "swift",
    "postgresql",
    "mysql",
    "mongodb",
    "redis",
    "kafka",
    "rabbitmq",
    "aws",
    "gcp",
    "azure",
    "terraform",
    "graphql",
    "grpc",
    "git",
    "linux",
    "elasticsearch",
    "spark",
    "hadoop",
    "pandas",
    "numpy",
    "scikit-learn",
    "sklearn",
    "opencv",
    "transformers",
    "huggingface",
    "langchain",
    "llamaindex",
    "openai",
    "anthropic",
    "claude",
    "gpt",
    "computer vision",
    "machine learning",
    "deep learning",
    "data science",
    "mlops",
    "nlp",
)
_BUILTIN_SENIORITY_TOKENS: dict[str, tuple[str, ...]] = {
    "principal": ("principal", "staff", "директор по", "cpo"),
    "lead": ("lead", "tech lead", "team lead", "тимлид", "лид"),
    "senior": ("senior", "старш", "sr.", "sr "),
    "middle": ("middle", "mid-level", "мидл"),
    "junior": ("junior", "jr.", "джуниор", "начинающ"),
    "intern": ("intern", "стаж", "trainee", "practician"),
}

_WORK_MODE_TOKENS: dict[str, tuple[str, ...]] = {
    "remote": ("remote", "удален", "удалён", "удалённо"),
    "hybrid": ("hybrid", "гибрид"),
    "onsite": ("on-site", "onsite", "office", "офис", "в офисе"),
}

# PostType classification rules. Each rule is (PostType, tuple of tokens). The
# rules are evaluated in order, the first match wins. Tokens are matched
# case-insensitively against the whole text. This is the built-in fallback
# (production code should use the LLM-backed classifier or FilterProfile).
#
# v2 (post-three-runs review):
# - Removed "course" from ANNOUNCEMENT tokens: it produced false positives on
#   legitimate jobs that mention "online courses" in the body (e.g. mlacademy.ai).
# - Added "помогу" (service offering) — the marker used on Russian Telegram
#   channels when someone offers their services (not a job posting).
_POST_TYPE_RULES: tuple[tuple[PostType, tuple[str, ...]], ...] = (
    # Service offering (e.g. "I'll build your chatbot") — NOT a job posting.
    (
        PostType.ANNOUNCEMENT,
        (
            "#помогу",
            "оказываю услуги",
            "предлагаю услуги",
            "создаю сайты",
            "делаю сайты",
            "создаю ботов",
            "сделаю",
            "возьму заказы",
            "возьмусь за",
            "продаю",
            "мои услуги",
        ),
    ),
    # Candidate seeking (resume / "looking for work").
    (
        PostType.CANDIDATE_SEEKING,
        (
            "#candidate",
            "#резюме",
            "ищу работу",
            "open to work",
            "#opentowork",
            "мой опыт",
            "мое резюме",
            "моё резюме",
            "#ищу",  # leading "looking for" tag (Russian Telegram convention)
        ),
    ),
    # Real announcements.
    (
        PostType.ANNOUNCEMENT,
        (
            "webinar",
            "meetup",
            "конференц",
            "митап",
            "дайджест",
            "подборка",
            "правила чата",
            "вакансий нет",
            "закрыт до",
            "новости",
            "анонс",
        ),
    ),
    # Spam.
    (PostType.SPAM, ("casino", "betting", "букмекер")),
)


def _normalize_amount(value: str | None) -> int | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else None


def _currency_code(value: str) -> str:
    normalized = value.upper()
    return {
        "$": "USD",
        "€": "EUR",
        "£": "GBP",
        "₸": "KZT",
    }.get(normalized, normalized)


def _detect_work_mode(text: str) -> WorkMode | None:
    lowered = text.casefold()
    for mode_name, tokens in _WORK_MODE_TOKENS.items():
        if any(token in lowered for token in tokens):
            return WorkMode(mode_name)
    return None


def _detect_language(text: str) -> LanguageCode:
    cyrillic = sum(1 for char in text if "а" <= char.casefold() <= "я" or char.casefold() == "ё")
    latin = sum(1 for char in text if "a" <= char.casefold() <= "z")
    if cyrillic == 0 and latin == 0:
        return LanguageCode.UNKNOWN
    return LanguageCode.RU if cyrillic >= latin else LanguageCode.EN


def _detect_post_type(text: str) -> PostType:
    lowered = text.casefold()
    for post_type, tokens in _POST_TYPE_RULES:
        if any(token in lowered for token in tokens):
            return post_type
    return PostType.JOB_POSTING


def _detect_ai_relevance(text: str) -> float:
    lowered = text.casefold()
    keywords = (
        "ai",
        "llm",
        "ml",
        "machine learning",
        "nlp",
        "computer vision",
        "mlops",
        "rag",
        "машинн",
        "нейрон",
        "искусственн",
    )
    hits = sum(1 for token in keywords if token in lowered)
    return min(1.0, round(hits / 4.0, 2))


def _detect_seniority(text: str, ontology_seniority: tuple[str, ...] = ()) -> Seniority:
    lowered = text.casefold()
    # Build effective token map: built-in + anything from live ontology
    token_map: dict[str, tuple[str, ...]] = dict(_BUILTIN_SENIORITY_TOKENS)
    for level in ontology_seniority:
        key = level.strip().lower()
        if key:
            token_map.setdefault(key, (key,))
    for senior_name in ("principal", "lead", "senior", "middle", "junior", "intern"):
        if any(token in lowered for token in token_map.get(senior_name, (senior_name,))):
            return Seniority(senior_name)
    return Seniority.UNKNOWN


def _detect_employment_type(text: str) -> EmploymentType:
    lowered = text.casefold()
    if any(token in lowered for token in ("contract", "b2b", "part-time", "part time")):
        return EmploymentType.CONTRACT
    if "intern" in lowered or "стаж" in lowered:
        return EmploymentType.INTERN
    return EmploymentType.FULL_TIME


def _extract_skill_tags(
    text: str,
    ontology_skills: tuple[str, ...] = (),
) -> tuple[SkillTag, ...]:
    """Universal skill extraction: matches canonical names from live ontology
    (or built-in fallback) against the text. Case-insensitive, multi-word aware.
    """
    lowered = text.casefold()
    pool = set(_BUILTIN_SKILLS) | {s.casefold() for s in ontology_skills}
    found: list[SkillTag] = []
    seen: set[str] = set()
    # Multi-word skills first (e.g. "computer vision", "machine learning")
    multi = sorted((s for s in pool if " " in s), key=len, reverse=True)
    for skill in multi:
        if skill in lowered and skill not in seen:
            found.append(SkillTag(canonical_name=skill, source="heuristic"))
            seen.add(skill)
    # Then single-word skills (whole-word match)
    for skill in sorted((s for s in pool if " " not in s), key=len, reverse=True):
        if skill in seen:
            continue
        # word boundary: r"\b<skill>\b" with punctuation-tolerance
        if re.search(rf"(?<![a-z0-9+#-]){re.escape(skill)}(?![a-z0-9+#-])", lowered):
            found.append(SkillTag(canonical_name=skill, source="heuristic"))
            seen.add(skill)
    return tuple(found)


def _parse_compensation(text: str) -> dict[str, Any] | None:
    match = _COMPENSATION_RE.search(text)
    if match is None:
        return None
    if match.group("cur_after"):
        min_amount = _normalize_amount(match.group("min1"))
        max_amount = _normalize_amount(match.group("max1"))
        currency = _currency_code(match.group("cur_after"))
    elif match.group("cur_b1") is not None:
        min_amount = _normalize_amount(match.group("min_b1"))
        max_amount = _normalize_amount(match.group("max_b1"))
        currency = _currency_code(match.group("cur_b1"))
    else:
        min_amount = _normalize_amount(match.group("min_b2"))
        max_amount = None
        currency = _currency_code(match.group("cur_b2"))
    if min_amount is None and max_amount is None:
        return None
    return {"currency": currency, "min_amount": min_amount, "max_amount": max_amount}


# Telegram DSML-style format:
#   line 0: Title
#   line 1: Company
#   line 2: Salary range (currency at end of range)
#   line 3: "City / Work mode" (e.g. "Астана / Remote")
# We parse line 1 as company and line 3 as city. We do NOT fall back to
# `lines[1]` for any other field (the previous bug).

# Skip lines that are clearly salary/compensation ranges — never treat a salary
# line as the company.
_SALARY_LINE_TOKENS = (
    "kzt",
    "usd",
    "eur",
    "rub",
    "gbp",
    "₸",
    "$",
    "€",
    "£",
    "net",
    "gross",
    "до вычета",
    "на руки",
    "за месяц",
    "за год",
    "per",
)


def _is_salary_line(line: str) -> bool:
    lowered = line.casefold()
    return any(token in lowered for token in _SALARY_LINE_TOKENS)


def _extract_company_from_lines(lines: list[str]) -> str | None:
    """Return the company name from the second non-title line, if it looks like
    a company name. Returns None when the second line is a salary range, a
    salary keyword, or when there are fewer than two lines."""
    # Find the title (line 0 in DSML format) and skip it.
    title_idx = -1
    for i, line in enumerate(lines):
        if _TITLE_RE.match(line):
            title_idx = i
            break
    if title_idx < 0:
        return None
    for line in lines[title_idx + 1 :]:
        stripped = line.strip(" -").strip()
        if not stripped:
            continue
        # Salary range: skip and look at the next line.
        if _is_salary_line(stripped):
            continue
        # Hashtag-only or pure-noise lines: skip.
        if stripped.startswith("#") and len(stripped) < 30:
            continue
        # Looks like "City / Work mode" (contains '/') and was the next
        # non-title line — that means company was missing in source; give up.
        if " / " in stripped and any(
            token in stripped.casefold()
            for token in (
                "remote",
                "office",
                "гибрид",
                "удалён",
                "офис",
                "onsite",
                "on-site",
                "hybrid",
            )
        ):
            return None
        # Reject obvious location-only lines (single word, no company markers).
        if len(stripped) > 2 and len(stripped) < 120:
            return stripped
        return None
    return None


# "City / Work mode" line: e.g. "Астана / Remote", "Astana / Office",
# "Москва / Гибрид", or just a city.
# Pattern is "City / WorkMode" (slash required for the second alternative);
# a bare work_mode word ("Remote" alone) is handled by the work_mode detector
# instead and must NOT match here.
_LOCATION_LINE_RE = re.compile(
    r"^\s*(?P<city>[^/]+?)\s*/\s*(?P<mode>remote|onsite|on-site|office|hybrid|удалён|удален|удалённо|удаленка|гибрид|офис|в офисе)\s*$",
    re.IGNORECASE,
)


def _extract_location_from_lines(lines: list[str]) -> tuple[str | None, WorkMode | None]:
    """Return (city, work_mode) parsed from a "City / Mode" line. Returns
    (None, None) when no such line is found."""
    # Find the title and skip past company + salary.
    title_idx = -1
    for i, line in enumerate(lines):
        if _TITLE_RE.match(line):
            title_idx = i
            break
    if title_idx < 0:
        return None, None
    seen_company = False
    seen_salary = False
    for line in lines[title_idx + 1 :]:
        stripped = line.strip(" -").strip()
        if not stripped:
            continue
        # First non-title, non-salary, non-location line is the company.
        if not seen_company:
            if _is_salary_line(stripped) or " / " in stripped:
                # No explicit company line; move to the next step.
                seen_company = True
                if _is_salary_line(stripped):
                    seen_salary = True
                continue
            seen_company = True
            continue
        # Skip salary lines.
        if not seen_salary and _is_salary_line(stripped):
            seen_salary = True
            continue
        # Try to match "City / WorkMode" pattern.
        match = _LOCATION_LINE_RE.match(stripped)
        if match is None:
            continue
        city_raw = (match.group("city") or "").strip()
        mode_raw = (match.group("mode") or "").strip().casefold()
        if not city_raw or _is_salary_line(city_raw):
            continue
        work_mode = _mode_from_token(mode_raw) if mode_raw else None
        return city_raw, work_mode
    return None, None


def _mode_from_token(token: str) -> WorkMode | None:
    token = token.casefold()
    if token in ("remote", "удалён", "удален", "удалённо", "удаленка"):
        return WorkMode.REMOTE
    if token in ("hybrid", "гибрид"):
        return WorkMode.HYBRID
    if token in ("onsite", "on-site", "office", "офис", "в офисе"):
        return WorkMode.ONSITE
    return None


def _detect_experience(text: str) -> int | None:
    patterns = [
        r"(\d+)\s*\+\s*years",
        r"(\d+)\s*\+\s*лет",
        r"(\d+)\s*years",
        r"от\s*(\d+)\s*лет",
        r"(\d+)\s*год",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _detect_education(text: str) -> str | None:
    lowered = text.casefold()
    if any(token in lowered for token in ("phd", "кандидат наук")):
        return "phd"
    if any(token in lowered for token in ("master", "магистр")):
        return "master"
    if any(token in lowered for token in ("bachelor", "бакалавр", "высшее", "degree")):
        return "bachelor"
    return None


def _detect_relocation(text: str) -> bool | None:
    lowered = text.casefold()
    if any(token in lowered for token in ("relocation", "релокация", "помощь с переездом")):
        return True
    return None


def _detect_visa(text: str) -> bool | None:
    lowered = text.casefold()
    if any(token in lowered for token in ("visa", "виза", "sponsorship")):
        return True
    return None


class HeuristicLLMProvider:
    """Universal heuristic extraction.

    Optional ``ontology_store`` injects live skills/seniority into extraction.
    When omitted, a small built-in snapshot is used.
    """

    def __init__(self, ontology_store: OntologyStore | None = None) -> None:
        self._ontology = ontology_store
        self._skill_snapshot: tuple[str, ...] = ()
        self._seniority_snapshot: tuple[str, ...] = ()
        self._loaded = False

    async def _load_ontology(self) -> None:
        if self._loaded:
            return
        if self._ontology is None:
            self._loaded = True
            return
        try:
            self._skill_snapshot = await self._ontology.list_skills()
        except Exception:
            self._skill_snapshot = ()
        try:
            self._seniority_snapshot = await self._ontology.list_seniority()
        except Exception:
            self._seniority_snapshot = ()
        self._loaded = True

    async def extract(self, text: str, schema: type[Any]) -> Any:
        await self._load_ontology()
        source_match = re.search(
            r"### UNTRUSTED_SOURCE_TEXT_BEGIN\n([\s\S]*?)\n### UNTRUSTED_SOURCE_TEXT_END",
            text,
        )
        if source_match is not None:
            text = source_match.group(1)
        lines = [line.strip(" -") for line in text.splitlines() if line.strip()]
        title = next((line for line in lines if _TITLE_RE.match(line)), None)
        # Telegram-style postings: line 0 = title, line 1 = company,
        # line 2 = salary, line 3 = "City / Work mode". We only use line 1
        # for company (no fallback) and line 3 for city/work_mode.
        company = _extract_company_from_lines(lines)
        city, work_mode_from_text = _extract_location_from_lines(lines)
        work_mode = work_mode_from_text or _detect_work_mode(text)
        compensation = _parse_compensation(text)
        post_type = _detect_post_type(text)
        hiring_intent = 1.0 if post_type is PostType.JOB_POSTING else 0.1
        skills = _extract_skill_tags(text, self._skill_snapshot)
        payload = {
            "title": title,
            "company": company,
            "description": text.strip(),
            "location": city,
            "work_mode": work_mode,
            "compensation": compensation,
            "post_type": post_type,
            "ai_relevance": _detect_ai_relevance(text),
            "hiring_intent": hiring_intent,
            "language": _detect_language(text),
            "seniority": _detect_seniority(text, self._seniority_snapshot),
            "employment_type": _detect_employment_type(text),
            "skills_explicit": skills,
            "tools_stack": tuple(skill.canonical_name for skill in skills),
            "years_experience": _detect_experience(text),
            "education": _detect_education(text),
            "relocation": _detect_relocation(text),
            "visa_support": _detect_visa(text),
        }
        fields = getattr(schema, "model_fields", None)
        if isinstance(fields, dict):
            payload = {key: value for key, value in payload.items() if key in fields}
        return schema.model_validate(payload)

    async def classify(self, prompt: str, schema: type[Any]) -> Any:
        # Extract only from the JOB TO CLASSIFY section; the prompt also has a
        # ## CANDIDATE PROFILE section with its own "Description:" field which
        # would otherwise be matched first by a naive search.
        _job_m = re.search(r"## JOB TO CLASSIFY\n([\s\S]+)", prompt)
        _compact_m = re.search(r"## VACANCY EVIDENCE\n([\s\S]+?)(?:\n##|\Z)", prompt)
        if _compact_m is not None:
            evidence = _compact_m.group(1)
            title_m = re.search(r"^\[\d+\]\s+title:\s*(.+)$", evidence, re.MULTILINE)
            title = (title_m.group(1).strip() if title_m else "").lower()
            desc = evidence[:2000].lower()
        else:
            _job_text = _job_m.group(1) if _job_m else prompt
            title_m = re.search(r"^Title:\s*(.+)$", _job_text, re.MULTILINE)
            desc_m = re.search(r"^Description:\s*([\s\S]+?)(?:\n##|\Z)", _job_text, re.MULTILINE)
            title = (title_m.group(1).strip() if title_m else "").lower()
            desc = (desc_m.group(1).strip()[:2000] if desc_m else "").lower()

        def _mk(decision: str, conf: float, reason: str) -> Any:
            fields = getattr(schema, "model_fields", {})
            if {"is_job", "role_relation", "responsibility_fit"}.issubset(fields):
                if decision == "accept":
                    return schema(
                        is_job="yes",
                        role_relation="target",
                        responsibility_fit="support",
                        positive_evidence_ids=(1,),
                    )
                if decision == "reject":
                    return schema(
                        is_job="yes",
                        role_relation="other",
                        responsibility_fit="contradict",
                        negative_evidence_ids=(1,),
                    )
                return schema(
                    is_job="yes",
                    role_relation="unknown",
                    responsibility_fit="unknown",
                    positive_evidence_ids=(1,),
                )
            pos: tuple[str, ...] = (reason,) if decision != "reject" else ()
            neg: tuple[str, ...] = (reason,) if decision == "reject" else ()
            return schema(
                decision=decision,
                confidence=conf,
                reasoning=reason,
                matched_positive_aspects=pos,
                mismatched_aspects=neg,
            )

        # 1. Non-job content
        # Require 3+ consecutive hashtags (freelancer ads open with many tags)
        # or explicit "#помогу" marker. Single/double location tags (#УдаленкаРФ #middle)
        # are used by job channels and must not be blocked.
        if re.match(r"\s*(?:#\S+\s*){3,}", desc) or "#помогу" in desc[:200]:
            return _mk("reject", 0.97, "Freelancer service ad (hashtag-opening)")
        if "правила чата" in title or "правила чата" in desc[:200]:
            return _mk("reject", 0.97, "Channel rules, not a job posting")
        if "идея:" in desc[:400] or "идея:" in title:
            return _mk("reject", 0.95, "Project specification, not a job posting")

        # 2. Internship
        if any(t in title for t in ("стажировка", "стажёр", "стажер", "intern", "trainee")):
            return _mk("reject", 0.97, "Internship position")
        if any(t in desc[:300] for t in ("стажировка:", "оплачиваемая стажировка", "стажёр:")):
            return _mk("reject", 0.95, "Internship (opening text)")

        # 3. Title-based rejects
        if (
            title.startswith("ds ")
            or title.startswith("ds,")
            or title.startswith("ds/")
            or "data scientist" in title
            or "аналитик данных" in title
        ):
            return _mk("reject", 0.95, "Data Scientist role")
        if "nlp data scientist" in title:
            return _mk("reject", 0.97, "NLP Data Scientist")
        if any(
            t in title
            for t in ("research engineer", "dl researcher", "ml researcher", "nlp researcher")
        ):
            return _mk("reject", 0.95, "Research role")
        if any(t in title for t in ("principal ml", "staff ml", "staff machine learning")):
            return _mk("reject", 0.95, "Principal/Staff ML Engineer")
        if any(
            t in title
            for t in ("product owner", "feature owner", "capability owner", "ai product owner")
        ):
            return _mk("reject", 0.97, "Product management role")
        if title.startswith("аналитик") or "analytics engineer" in title or "bi engineer" in title:
            return _mk("reject", 0.95, "Analytics/BI role")
        if any(t in title for t in ("computer vision engineer", "vision engineer")):
            return _mk("reject", 0.95, "Computer Vision role")
        if any(t in title for t in ("data engineer", "дата-инженер")):
            return _mk("reject", 0.95, "Data Engineer role")
        if any(t in title for t in ("mlops", "ml platform", "ml infra")):
            return _mk("reject", 0.92, "MLOps/ML Platform role")
        if "ds инженер" in title or "ds engineer" in title:
            return _mk("reject", 0.95, "DS engineer role")
        if "data ml" in title:
            return _mk("reject", 0.90, "Data ML role")

        # 4. NLP Engineer at ML Lab
        if ("nlp engineer" in title or "nlp-engineer" in title) and any(
            t in desc or t in title for t in ("лаборатория", "ml lab", "машинного обучения")
        ):
            return _mk("reject", 0.95, "NLP Engineer at ML Lab / Лаборатория МО")

        # 5. Technical Architect for non-LLM backend
        if (
            "technical architect" in title
            or ("solution architect" in title and "llm" not in desc[:500])
        ) and any(
            t in title or t in desc
            for t in ("heavy ml", "audio backend", "media backend", "ml backend")
        ):
            return _mk("reject", 0.93, "Technical Architect for non-LLM ML/audio backend")

        # 6. ML Engineer for compliance document analysis (Armeta-style)
        if any(t in title for t in ("ml engineer", "dl engineer")) and any(
            t in desc for t in ("analyzes permitting", "permitting and design documentation")
        ):
            return _mk("reject", 0.93, "ML Engineer for compliance document analysis")

        # 7. Recommendation systems as primary stated work
        if (
            "рекомендательных систем" in desc[:1000] or "recommendation system" in desc[:600]
        ) and not (title.startswith("llm ") or "llm recommendation" in title):
            return _mk("reject", 0.93, "Primary work is recommendation systems (recsys ML)")

        # 8. Clear accept by title
        accept_title_tokens = (
            "llm engineer",
            "llm developer",
            "llm architect",
            "llm-инженер",
            "llm инженер",
            "ai application engineer",
            "genai engineer",
            "generative ai engineer",
            "agentic engineer",
            "agentic ai",
            "prompt engineer",
            "prompt-engineer",
            "prompt-инженер",
            "prompt инженер",
            "ai automation engineer",
            "vibe coder",
            "ai tech lead",
            "ai архитектор",
            "ai agent engineer",
            "ai-agent engineer",
            "ai engineer",
            "ai-engineer",
            "ai developer",
            "ai-developer",
            "ai-разработчик",
            "ai разработчик",
            "ai/ml engineer",
        )
        for t in accept_title_tokens:
            if t in title:
                return _mk("accept", 0.90, f"Title matches ACCEPT: {t}")

        # LLM in role part (before " в команду/Маркет/отдел" team suffix) + engineering role
        _role_part = re.split(r" в (?:команд|маркет|отдел)", title, maxsplit=1)[0].strip()
        if "llm" in _role_part and any(
            t in _role_part
            for t in (
                "engineer",
                "developer",
                "разработчик",
                "инженер",
            )
        ):
            if any(
                t in desc
                for t in (
                    "алайнмент",
                    "дообучение",
                    "рассуждений",
                    "способности к",
                    "reasoning capabilit",
                )
            ):
                return _mk("reject", 0.87, "LLM role but model training/research context")
            return _mk("accept", 0.90, f"LLM engineer/developer: {_role_part}")

        if "genai" in title or "generative ai" in title or "gen ai" in title:
            return _mk("accept", 0.88, f"GenAI role: {title}")

        if any(t in title for t in ("ai agents", "ai-agents", "(ai agent", "agentic", "ai-driven")):
            return _mk("accept", 0.85, f"AI agent/agentic role: {title}")

        # 9. LLM title prefix — borderline (e.g. "LLM Recommendation System developer" caught above)
        if title.startswith("llm ") and len(title) > 8:
            return _mk("review", 0.78, "Title starts with 'LLM' — borderline review")

        # 10. Targeted description signals (very specific, low FP risk)
        if any(
            s in desc for s in ("openai api", "anthropic api", "llm api", "gigachat api", "gpt-4")
        ):
            return _mk("accept", 0.85, "Explicit LLM API integration in description")

        if "языковыми моделями" in desc and not any(
            t in desc for t in ("алайнмент", "дообучение", "rlhf")
        ):
            return _mk("review", 0.76, "Language model product work in description")

        if re.search(r"интегрировать.{0,30}llm|llm.{0,30}в продукт", desc):
            return _mk("accept", 0.83, "LLM integration into product (description)")

        # 11. Default: reject uncertain items
        return _mk("reject", 0.78, "Role does not clearly match LLM API product engineering")

    async def present(self, job_payload: str, schema: type[Any]) -> Any:  # noqa: ARG002
        raise NotImplementedError("HeuristicLLMProvider has no present() — use openai.")


@register_llm("heuristic")
def _build_heuristic_llm(settings: Settings) -> HeuristicLLMProvider:
    del settings
    return HeuristicLLMProvider()
