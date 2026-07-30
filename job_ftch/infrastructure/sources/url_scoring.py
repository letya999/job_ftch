from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlparse

_JOB_KEYWORDS = frozenset(
    {
        "job",
        "jobs",
        "career",
        "careers",
        "position",
        "positions",
        "opening",
        "openings",
        "vacancy",
        "vacancies",
        "vakans",
        "vakansii",
        "vakansiya",
        "rabota",
        "careerist",
        "trud",
        "resume",  # sometimes boards use resume/job mix
    }
)
_NEGATIVE_PATH_WORDS = frozenset(
    {
        "academy",
        "about",
        "account",
        "applicant",
        "application",
        "apply",
        "article",
        "articles",
        "author",
        "authors",
        "auth",
        "benefits",
        "blog",
        "candidate",
        "candidates",
        # Common Russian transliterations used in recruiting-content slugs.
        # They identify articles about candidates rather than a vacancy.
        "kandidat",
        "kandidata",
        "kandidatov",
        "kandidaty",
        "categories",
        "category",
        "city",
        "cities",
        "client",
        "clients",
        "companies",
        "company",
        "contact",
        "contacts",
        "content",
        "course",
        "courses",
        "direction",
        "directions",
        "employer",
        "employers",
        "event",
        "events",
        "faq",
        "favorite",
        "favorites",
        "feed",
        "filter",
        "filters",
        "guide",
        "guides",
        "help",
        "hint",
        "hints",
        "information",
        "information-pages",
        "calendar",
        "catalog",
        "internship",
        "internships",
        "landing",
        "legal",
        "learn",
        "learning",
        "location",
        "locations",
        "login",
        "media",
        "news",
        "office",
        "offices",
        "overview",
        "page",
        "pages",
        "password",
        "psettings",
        "privacy",
        "policy",
        "profile",
        "profiles",
        "rating",
        "ratings",
        "report",
        "reports",
        "research",
        "referral",
        "referrals",
        "topic",
        "topics",
        "register",
        "registration",
        "resume",
        "resumes",
        "rss",
        "search",
        "settings",
        "signin",
        "signup",
        "service",
        "services",
        "subscribe",
        "subscription",
        "support",
        "talent",
        "team",
        "teams",
        "terms",
        "hub",
        "hubs",
        "college",
        "colleges",
        "directory",
        "training",
        "trainings",
        "api",
        "redirect",
    }
)
_NEGATIVE_SUBSTRINGS = (
    "apps.apple.com/",
    "careers/epam-without-borders",
    "careers/external-referral-program",
    "cdn-cgi/l/email-protection",
    "dzen.ru/",
    "linkedin.com/learning/",
    "/applicant/",
    "/article/",
    "/articles/",
    "/blog/",
    "/categories/",
    "/category/",
    "/client/",
    "/clients/",
    "/companies/",
    "/company/",
    "/content/",
    "/employer/",
    "/employers/",
    "/events/",
    "/media/",
    "/news/",
    "/office/",
    "/offices/",
    "/search/",
    "/search?",
    "/guides/",
    "/hint",
    "/hints",
    "/jobseekers/",
    "/legal/",
    "/learning/",
    "/marketvc/",
    "/overview/working-at-",
    "/poslodavac/",
    "/psettings/",
    "/redirect/",
    "/privacy-policy",
    "/privacy/",
    "/terms/",
    "/talent/",
    "/hubs/",
    "/referral",
    "/topics/",
    "/teams/",
    "authwall",
    "cookie-policy",
    "guest-controls",
    "post-a-job",
    "t.me/",
    "talent-pool",
    "talent_pool",
    "youtube.com/",
    "/api/",
    "vacancy-response",
    "vacancy_response",
    "vacancy_search",
    "vacancy_of_the_day/click",
)
_DETAIL_PATH_RE = re.compile(
    r"/(?:job|jobs|vacancy|vacancies|vakansii|position|positions|posting|postings"
    r"|work|project|projects"
    r"|locuri-de-munca|locuri_de_munca"
    r"|career|careers"
    r"|opening|openings"
    r"|offer|offers"
    r"|ployment|ployments"
    r"|stelle|stellen"
    r"|offre|offres"
    r"|empleo|empleos"
    r"|trabajo|trabajos)"
    r"(?:/.*)?/(?:[^/?#]*\d[^/?#]*|[a-zA-Z0-9-]+/?)"
    r"(?:\.(?:html?|php|aspx))?$",
    re.IGNORECASE,
)
_PATH_WORD_SPLIT = re.compile(r"[/\-_.]+")
_DETAIL_QUERY_KEYS = frozenset(
    {
        "gh_jid",
        "jk",
        "job_id",
        "jobid",
        "posting_id",
        "vjk",
        "vacancyid",
        "vacancy_id",
        "reqid",
        "req_id",
    }
)
_WEAK_DETAIL_FRAGMENT_RE = re.compile(r"^#(?:job|vacancy|opening)[\w-]*$", re.IGNORECASE)


def _site_family(hostname: str) -> str:
    host = hostname.lower().strip(".")
    if not host:
        return ""
    parts = [part for part in host.split(".") if part]
    if len(parts) <= 2:
        return host
    # Handle simple ccTLDs like example.com.pl / foo.co.uk.
    if len(parts[-1]) == 2 and len(parts[-2]) <= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_same_site_family(url: str, *, board_url: str | None = None) -> bool:
    if not board_url:
        return True
    url_host = urlparse(url).hostname or ""
    board_host = urlparse(board_url).hostname or ""
    if not url_host or not board_host:
        return True
    url_family = _site_family(url_host)
    board_family = _site_family(board_host)
    return (
        url_family == board_family
        or url_host.endswith(f".{board_host}")
        or board_host.endswith(f".{url_host}")
    )


def score_job_url(url: str, *, board_url: str | None = None) -> int:
    lowered = url.lower()
    parsed = urlparse(lowered)
    if parsed.scheme in {"javascript", "tel", "mailto", "skype", "whatsapp"}:
        return -100
    path = parsed.path
    query = dict(parse_qsl(parsed.query))
    segments = [segment for segment in path.split("/") if segment]
    score = 0

    if board_url and lowered.rstrip("/") == board_url.lower().rstrip("/"):
        score -= 12

    if not is_same_site_family(url, board_url=board_url):
        score -= 12

    if any(substring in lowered for substring in _NEGATIVE_SUBSTRINGS):
        score -= 8

    negative_hits = set()
    for segment in segments:
        segment_words = {part for part in _PATH_WORD_SPLIT.split(segment) if part}
        # Long slugs often contain a complete article title.  They used to be
        # skipped wholesale to avoid treating a hyphenated vacancy title as a
        # category, but that also let URLs such as
        # ``100-kontaktov-kandidatov-...`` bypass the ``candidates`` penalty
        # solely because they were long.  Exact negative path words remain a
        # strong signal regardless of a slug's length.
        negative_hits |= segment_words & _NEGATIVE_PATH_WORDS

    # ``search`` can be an ordinary word in a numbered posting slug (for
    # example ``/jobs/search-engineer-99``).  It is not a search endpoint in
    # that shape, while the other editorial/category signals remain useful
    # even for a long detail-looking slug.
    is_numbered_detail_path = bool(_DETAIL_PATH_RE.search(path))
    if negative_hits and not (is_numbered_detail_path and negative_hits <= {"search"}):
        score -= 15

    if segments and segments[-1] in _JOB_KEYWORDS:
        score -= 6
    if segments:
        last_words = [part for part in _PATH_WORD_SPLIT.split(segments[-1]) if part]
        if last_words and set(last_words).issubset(_JOB_KEYWORDS):
            score -= 6

    if "viewjob" in path and _DETAIL_QUERY_KEYS & set(query):
        score += 9
    # A number of newer job boards expose individual postings as the compact
    # ``/jobdesc?id=<numeric-id>`` route.  ``id`` alone is far too generic to
    # trust, so retain the path-specific constraint rather than broadening the
    # shared query-key allowlist.
    if re.search(r"/jobdesc/?$", path, re.IGNORECASE) and query.get("id", "").isdigit():
        score += 16

    if _DETAIL_PATH_RE.search(path):
        score += 8

    # Generic check for trailing IDs (e.g. /marketing/targetolog-59176 or /vacancy/13827813)
    # Most job detail pages end with an ID consisting of at least 4 digits
    if re.search(r"[-/]\d{4,}(?:\.html)?$", path, re.IGNORECASE):
        score += 7

    if _DETAIL_QUERY_KEYS & set(query):
        score += 5

    if re.search(r"\d{3,}", path):
        score += 3

    if segments:
        last_segment = segments[-1]
        first_segment = segments[0]
        if re.search(r"[a-zа-я].*\d|\d.*[a-zа-я]", last_segment, re.IGNORECASE):
            score += 3
        if "-" in last_segment and len(last_segment) >= 12:
            score += 1
        if "jobs" in last_segment and not re.search(r"\d", last_segment) and "/jobs/" in path:
            score -= 6

        # Penalize category/search pages (e.g. /вакансии/Усть-Каменогорск)
        if len(segments) <= 2 and not re.search(r"\d", path) and "-" not in last_segment:
            score -= 5

        if (
            len(segments) == 1
            and not re.search(r"\d", last_segment)
            and any(keyword in last_segment for keyword in _JOB_KEYWORDS)
        ):
            score -= 5
        if len(segments) == 1 and re.search(r"-(jobs|job|vacancies|vacancy)$", last_segment):
            score -= 5
        if (
            len(segments) == 2
            and first_segment in _JOB_KEYWORDS
            and not re.search(r"\d", last_segment)
            and re.search(r"-(jobs|job|vacancies|vacancy)\b", last_segment)
        ):
            score -= 12
        if first_segment in {"api", "catalog", "service", "services", "redirect"}:
            score -= 12

    if any(keyword in path for keyword in _JOB_KEYWORDS):
        score += 2

    if len(segments) <= 1 and not (_DETAIL_QUERY_KEYS & set(query)):
        score -= 2

    if {"page", "p", "sort", "filter"} & set(query):
        score -= 3

    if parsed.fragment and _WEAK_DETAIL_FRAGMENT_RE.search(f"#{parsed.fragment}"):
        score -= 3

    if path.endswith((".pdf", ".doc", ".docx")):
        score -= 8

    return score


def is_probable_job_url(url: str, *, board_url: str | None = None) -> bool:
    return score_job_url(url, board_url=board_url) > 0


def looks_like_listing_url(url: str, *, board_url: str | None = None) -> bool:
    lowered = url.lower()
    parsed = urlparse(lowered)
    path = parsed.path
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return False
    if is_probable_job_url(url, board_url=board_url):
        return False
    if board_url and lowered.rstrip("/") == board_url.lower().rstrip("/"):
        return False
    last_words = [part for part in _PATH_WORD_SPLIT.split(segments[-1]) if part]
    if last_words and set(last_words).issubset(_JOB_KEYWORDS):
        return True
    return any(keyword in path for keyword in _JOB_KEYWORDS)


def rank_job_urls(urls: list[str] | set[str], *, board_url: str | None = None) -> list[str]:
    scored = [(url, score_job_url(url, board_url=board_url)) for url in set(urls)]
    positives = [(url, score) for url, score in scored if score > 0]
    chosen = positives or scored
    chosen.sort(key=lambda item: (-item[1], item[0]))
    return [url for url, _ in chosen]
