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
| geekjob.ru | assessment-driven | specific builder disabled | The old builder returned the bare listing while claiming search; assessment must verify a working GET/POST/browser/API surface first. |
| hirify.me | combined | `search=A or B` + `params=title,company` | Lowercase ` or `. Search runs through the `/api/vacancies` endpoint; the page URL carries `search`/`params` which `_query_for_spec` forwards. |
| team.vk.company | combined | `query=A OR B` | Discover already reads `query`/`search`/`title` and maps it to the API `title` param. |
| rabota.sber.ru | combined | `query=A OR B` | Parse already maps listing `query` to API `searchString`. |

For `combined` a single verified search URL replaces the bare source and keeps the
original `source_name`. For `per_keyword` each URL becomes its own source with a
`<source_name>_kwN` suffix so the publish ledger and processed-key dedup stay
unique.

Adding a new aggregator = add `supports_search`, `search_mode`, and
`build_search_urls` to its parser, then a unit test in
`tests/test_site_parser_search_urls.py`. The builder is accepted only after
assessment proves that it changes the result surface and rejects the nonsense
control.

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
