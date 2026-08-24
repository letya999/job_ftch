---
title: "Search query ingest"
description: "How career-site source URLs are expanded into keyword-search ingest targets."
updated: 2026-08-22
status: current
audience: engineers configuring career-site ingest
---

# Keyword-search ingest for career sites

Career-site sources can start from a listing URL that is already filtered by the
tenant's target roles, instead of crawling the whole board. The target roles
(`FilterProfile.target_roles`) drive this, so no extra per-source config is
required. Sources whose URL already carries an explicit query
(`?text=`, `?q=`, `?qs=`, `?search=`, `?keywords=`) are never rewritten - a
hand-authored query is treated as intentional.

Expansion runs in `application/search_expansion.py::expand_career_site_specs`,
called from `TenantRunner._build_runtime_builder` once the effective catalog
(and thus target roles) is known.

## Tier-2: dedicated aggregator parsers (authoritative)

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
| geekjob.ru | per_keyword | `qs=<role>` per role | `qs` is all-terms and has no OR operator, so a combined query matches nothing. |
| hirify.me | combined | `search=A or B` + `params=title,company` | Lowercase ` or `. Search runs through the `/api/vacancies` endpoint; the page URL carries `search`/`params` which `_query_for_spec` forwards. |
| team.vk.company | combined | `query=A OR B` | Discover already reads `query`/`search`/`title` and maps it to the API `title` param. |
| rabota.sber.ru | combined | `query=A OR B` | Parse already maps listing `query` to API `searchString`. |

For `combined` a single search URL replaces the bare source and keeps the
original `source_name`. For `per_keyword` each URL becomes its own source with a
`<source_name>_kwN` suffix so the publish ledger and processed-key dedup stay
unique.

Adding a new aggregator = add `supports_search`, `search_mode`, and
`build_search_urls` to its parser, then a unit test in
`tests/test_site_parser_search_urls.py`.

## Tier-1: generic search-form detection (best-effort)

For a career site whose parser does **not** advertise `supports_search` (or
that has no parser), expansion attaches the keywords to
`monitor_config["_search_keywords"]`. At run time
`CareerSiteSource._maybe_apply_generic_search` runs **before** `_try_site_parser`
so a parser without `supports_search` still sees the rewritten URL. It skips
only when the URL already has a known search query or the resolved parser has
`supports_search=True`. It then:

1. `detect_search_form(html, base_url)` - finds a `<form>` with a text/search
   input. GET forms are preferred (their query parameter is reproducible as a
   URL); POST-only forms are reported but not used.
2. `build_generic_search_url(form, query)` - builds a GET URL preserving hidden
   fields.
3. `discover_working_search_url(fetch, base_url, keywords)` - probes the query:
   it compares the count of job-like links for the combined query (tried with
   both `OR` and `or`) against the unfiltered page and a nonsense token. It
   adopts a URL only if the query returns results and demonstrably narrows the
   listing; if a nonsense token returns roughly the full listing the query is
   deemed ignored and the original URL is kept.

Limitations: the generic path only adopts a single combined URL (no per-keyword
fan-out), handles GET forms only, and costs up to four probe fetches per source
per run. It never fails the run - on any error the original listing URL is used
and the normal crawl proceeds. Tests: `tests/test_generic_search_form.py`.
