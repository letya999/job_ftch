---
title: "Search query ingest"
description: "How career-site source URLs are expanded into keyword-search ingest targets."
updated: 2026-09-01
status: current
audience: engineers configuring career-site ingest
---

# Keyword-search ingest for career sites

Career-site sources can start from a listing URL that is already filtered by the
tenant's target roles, instead of crawling the whole board. The target roles
(`FilterProfile.target_roles`) drive this, so no extra per-source config is
required. Sources whose URL already carries an explicit query
(`?text=`, `?q=`, `?qs=`, `?search=`, `?keywords=`) are rebuilt from the
current target roles by default. Set `search_locked=true` when an
operator-authored query must remain unchanged.

Expansion runs in `application/search_expansion.py::expand_career_site_specs`,
called from `TenantRunner._build_runtime_builder` once the effective catalog
(and thus target roles) is known.

## Source assessment and runtime selection

Before ingest, a career-site assessment probes the registered specific parser
and the page's generic search surface. It tries source-local terms plus a
nonsense control and stores only the verified recipe: executor, query mode,
form action/parameter, and result evidence. Target roles are not persisted in
the assessment; runtime injects the current profile roles.

Search execution and vacancy extraction are independent. A generic GET/POST or
browser search may feed a specific parser, and a specific URL/API search may
feed the generic monitor chain. If a source has a search input but no
reproducible URL, runtime makes one bounded browser attempt and otherwise keeps
the original listing as a safe fallback.

## Tier-2: dedicated aggregator parsers (authoritative when verified)

A `SiteParser` may advertise `supports_search = True` and implement
`build_search_urls(base_url, keywords)`. Its `search_mode` decides the fan-out:

- `combined` - one URL with all roles in a single query.
- `per_keyword` - one URL per role (the site cannot take a multi-term query).

Verified per-site behavior (each aggregator uses a different operator/field;
see `job_ftch/infrastructure/sources/site_parsers/`):

| Site | Mode | Query | Notes |
|------|------|-------|-------|
| hh.ru / hh.kz | combined | `text=A OR B` + `search_field=name` + `ored_clusters=true` | Uppercase `OR`. `search_field=name` restricts to the vacancy title; without it, generic terms (manager, specialist) match descriptions and flood results. |
| career.habr.com | combined | `q=A OR B` + `type=all` | Uppercase `OR`. |
| geekjob.ru | per-keyword | `qs=ROLE` → `/json/find/vacancy` | The page form is only a shell; the JSON endpoint is the authoritative search surface. |
| hirehi.ru | per-keyword | `search=ROLE` | Server-rendered JSON-LD vacancy links change with the query; one URL per role avoids undocumented boolean semantics. |
| hirify.me | combined | `search=A or B` + `params=title,company` | Search runs through `/api/vacancies`; the page URL carries `search`/`params`, which `_query_for_spec` forwards. |
| getmatch.ru | combined/local | `query=A OR B` + sitemap slug match | Getmatch exposes no public server-side search API; the parser preserves phrases and applies the query against canonical vacancy slugs. |
| team.vk.company | combined | `query=A OR B` | Discover already reads `query`/`search`/`title` and maps it to the API `title` param. |
| rabota.sber.ru | combined | `query=A OR B` | Parse already maps listing `query` to API `searchString`. |
| jobboard.agilefluent.ru | per-keyword | API `filters.roles=[ROLE]` | Uses the board API; signed origin URLs are decoded and followed when reachable. |
| djinni.co | per-keyword | `all_keywords=ROLE` + `search_type=title-only` | Follows Djinni detail pages and an exposed external vacancy link when present. |

The other requested aggregator boards use their existing category/listing URL
as the search surface: Quick Offer and AIJobs are filtered locally against
detail-page text, because their public HTML does not expose a stable free-text
URL contract. RemoteRocketship is browser-capable but currently may be hidden
behind an anti-bot challenge. `ai.engineer` is registered deliberately as a
terminal empty parser because its current URL is an AI engineering community
site, not a vacancy board. Every aggregator parser follows its detail page
first and only replaces the aggregator URL with an origin URL after that
origin was fetched successfully; otherwise the aggregator detail remains the
safe canonical item.

For `combined` a single verified search URL replaces the bare source and keeps the
original `source_name`. For `per_keyword` each URL becomes its own source with a
`<source_name>_kwN` suffix so the publish ledger and processed-key dedup stay
unique.

Adding a new aggregator = add `supports_search`, `search_mode`, and
`build_search_urls` to its parser, then a unit test in
`tests/test_site_parser_search_urls.py`. Each builder must have a deterministic
positive query path and a negative nonsense-query control. Assessment remains
useful for measuring the site, but runtime search does not silently fall back to
the bare listing when the parser has a verified search contract.

## Tier-1: generic search-form detection (best-effort)

Every career site carries the keywords in
`monitor_config["_search_keywords"]`. At run time
`CareerSiteSource._maybe_apply_generic_search` runs **before**
`_try_site_parser`, including for parsers that advertise `supports_search`.
The assessed executor selects one of these paths:

1. `detect_search_form(html, base_url)` - finds a `<form>` with a text/search
   input. GET forms are preferred and POST forms retain hidden fields.
2. `build_generic_search_url` / `build_generic_search_payload` - build GET or
   POST requests while preserving safe hidden fields.
3. `discover_working_search_url(fetch, base_url, keywords)` - probes the query:
   it compares the count of job-like links for the combined query (tried with
   both `OR` and `or`) against the unfiltered page and a nonsense token. It
   adopts a URL only if the query returns results and demonstrably narrows the
   listing; if a nonsense token returns roughly the full listing the query is
   deemed ignored and the original URL is kept. The same positive/nonsense
   check is used for specific parser candidates.

Limitations: the generic path adopts one combined URL (no per-keyword fan-out),
and browser-only forms whose result stays in the DOM rather than the URL cannot
be handed to a later parser yet. Assessment and runtime search are bounded and
never fail the run: on any error the original listing URL is used and the
normal crawl proceeds. Tests: `tests/test_generic_search_form.py`,
`tests/test_source_assessment.py`.
