---
title: "Search query ingest"
description: "How career-site source URLs are expanded into keyword-search ingest targets."
updated: 2026-09-03
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
(and thus target roles) is known. Roles are compacted first: slash-aliases
(`Vibe Coder / AI Product Builder`) become separate terms, generic leftovers
are dropped when distinctive AI/LLM/agent phrases exist, and a combined query
is capped at eight terms so HH-style `A OR B` stays concentrated.

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

- `combined` - one URL with all roles in a single query (`OR` / `or` per site).
- `per_keyword` - one URL per role (the site cannot take a multi-term query).
  Fan-out is capped at three URLs so it does not consume the 50-source run budget.

A failed HTML-form assessment does not suppress a parser with `supports_search`.
GeekJob's JSON `qs=` and HH `text=A OR B` are different surfaces from
`detect_search_form`. Per-keyword fan-out is capped at three URLs so HireHi
and GeekJob cannot starve the 50-source run budget.

Verified per-site behavior (each aggregator uses a different operator/field;
see `job_ftch/infrastructure/sources/site_parsers/`):

| Site | Mode | Query | Notes |
|------|------|-------|-------|
| hh.ru / hh.kz | combined | `text=A OR B` + `search_field=name` + `ored_clusters=true` | Uppercase `OR`. `search_field=name` restricts to the vacancy title; without it, generic terms (manager, specialist) match descriptions and flood results. |
| career.habr.com | combined | `q=A OR B` + `type=all` | Uppercase `OR`. Listing cards (`vacancy-card__title-link`) are parsed over HTTP, including company pages such as RWB. Empty parse is terminal. |
| geekjob.ru | per-keyword | `qs=ROLE` → `/json/find/vacancy` | JSON rows are emitted as cards. Browser scroll is not used. Fan-out is capped at three URLs. Empty parse is terminal. |
| hirehi.ru | per-keyword | `search=ROLE` | Server-rendered JSON-LD vacancy links change with the query; fan-out is capped at three URLs. |
| hirify.me | combined | `search=A or B` + `params=title,company` | Search runs through `/api/vacancies`; the page URL carries `search`/`params`, which `_query_for_spec` forwards. |
| getmatch.ru | combined | listing `/vacancies` | No free-text search box; `?query=` is stripped. Target roles stay on `_search_keywords` and are applied locally. `/api/offers` walks `offset` until the limit, then sitemap. |
| team.vk.company | combined | `search=A OR B` | Live search box uses `search=` (maps to API `title=`). `query=` is ignored. API `title` is a substring, not boolean OR; combined queries paginate the listing and filter titles locally. |
| rabota.sber.ru | combined | `query=A OR B` | Parse maps listing `query` to API `searchString` and walks `skip`/`take`. |
| yandex.ru/jobs | combined | `text=A OR B` | Public `/jobs/api/publications?text=` walks `page`; browser intercept is only a fallback. |
| superjob.ru | combined | `keywords=A OR B` | HTTP listing of `vakansii/{slug}-{id}.html`. A challenge body raises `BrowserChallengeError` instead of a generic hang. |
| tbank.ru | combined | `/career/vacancies/it/` | No free-text box. Listing URL is kept; target roles stay on `_search_keywords` and are applied locally while listing pages are walked. |
| rabota.x5.ru | per-keyword | `search=ROLE` | One URL per target role. Combined `OR` is not used. Listing pages are walked. |
| career.ozon.ru | combined | listing `/vacancy/` | Full role phrases in API `query=` return zero. Distinctive tokens from the profile roles (`AI`/`LLM`/`ML`) are queried internally; leftover cards are filtered locally. Empty parse is terminal so the generic browser path cannot hang. |
| career.avito.com | combined | `q=A OR B` | The board may ignore `q=`; target roles still go into the query and `_search_keywords`, then local filter. Listing pages are walked. |
| astanahub.com | combined | listing ``opened=True`` | Full role phrases in `q=` return zero. The opened listing is walked with `page=` and filtered locally from profile roles. |
| cloud.ru | per-keyword | ``search=ROLE`` | SSR HTML already contains the filtered vacancy links. Listing pages are walked. |
| job.kaspi.kz | combined | ``/search?search=A OR B`` | Free-text `search=` gets the profile roles. HTTP listing cards are parsed; leftover cards are filtered locally. Empty parse is terminal. |
| careers.t2.ru | combined | T2 host kept | Public vacancies live on HH employer 4219. The T2 host stays selected so empty HH is terminal. |
| qyzmet.kz | combined | listing `/вакансии` | Homepage is a challenge. Cards use `/redir?id=` and are rewritten to `/jobdesc?id=`. |
| gorodrabot.kz / .by | combined | homepage listing | Role-slug search 404s. `/advert/{id}/{slug}` cards are filtered locally. |
| careers.higgsfield.kz | combined | Ashby posting-api `higgsfieldai` | Token is known; custom-domain `can_handle` is skipped so a hung redirect cannot fall through to the browser. |
| rabota.kz | combined | `/job/list?search=` | SPA cards `/job/list/{id}` are in the listing HTML. |
| bcc.kz | combined | `/career/vacancies/` | Numeric `/career/{id}` cards. |
| careers.indrive.com | combined | `/vacancies/` | WP cards `a.c-job-card[data-id]`; canonical URL is `data-url`. Path pagination `/vacancies/page/N/`. |
| job.rt.ru | combined | listing `/search` | Public `/backend/api/vacancies?page=` (no `searchString`, it returns empty `vacancies[]`). Local title filter. |
| halykbank.kz | combined | `/about/career/vacancies` | Listing cards `vacancies-inner/{n}` without fetching every detail. |
| people.beeline.kz | — | — | OutSystems shell has no listing. Empty parse is terminal. |
| jobboard.agilefluent.ru | combined | API `/api/jobs/search` | `filters.roles` is an enum, not free text. The listing is walked with `hasMore` and filtered locally. Signed origin JWTs are decoded into metadata but not fetched. |
| djinni.co | per-keyword | `all_keywords=ROLE` + `search_type=title-only` | Follows Djinni detail pages and an exposed external vacancy link when present. |
| aijobs.net / foorilla.com | combined | HTMX `GET /hiring/jobs/?job_search=` | aijobs.net redirects here. Requests send `HX-Request`. Cards come from `hx-get="/hiring/jobs/{slug}-{id}/"`. |
| aijobs.com | combined | `q=A OR B` | SSR cards `/jobs/{id}-{slug}`. Listing pages are walked. Origin pages are not fetched. |
| aijobs.ai | combined | `keyword=A OR B` | SSR cards `/job/{slug}`. Listing pages are walked. Origin pages are not fetched. |
| jseek.co | combined | Typesense `q=` | Search uses `/api/typesense-key` then `job_posting` documents; pages are walked. Listing without `q=` still resolves original `sourceUrl`. |

Halyk Bank has a vacancy search box, but it is October CMS AJAX
(``data-request=onSearch``). ``?query=ML`` is ignored; the listing HTML already
has all cards, so the parser filters titles locally. Just AI has no search box;
the WordPress REST list is small and is filtered locally.

Quick Offer still follows an exposed origin apply-link when that origin fetch
succeeds. RemoteRocketship is browser-capable but currently may be hidden
behind an anti-bot challenge. `ai.engineer` is registered deliberately as a
terminal empty parser because its current URL is an AI engineering community
site, not a vacancy board. Aggregator parsers keep the aggregator detail as
the canonical item unless an origin URL was fetched successfully.

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

## Listing pagination

Target roles never become hardcoded category IDs or token shortcuts. They come
from `FilterProfile.target_roles` in the live catalog (Postgres/SQLite) and are
passed as-is into `build_search_urls` / `_search_keywords`.

After the search URL is built, both specific parsers and the generic DOM
monitor walk extra listing pages until the source `limit`, an empty page, or a
repeated page:

- `page` / `p` query params;
- `offset` / `skip`;
- opaque `cursor` / `after`;
- HTML `rel=next` and JSON `next` / `next_cursor`.

Shared helpers live in `site_parsers/helpers.py`
(`detect_listing_pagination`, `listing_page_url`, `paginate_listing`).
The generic DOM monitor auto-detects those signals when `monitor_config`
has no explicit `pagination` block, and otherwise tries `page=2,3,...`.
A failed extra page never fails the source.
