---
title: "033 — Plugin-Based Domain Parsers (no host hardcode in core)"
description: "**Status**: ACCEPTED"
updated: 2026-07-24
---
# 033 — Plugin-Based Domain Parsers (no host hardcode in core)

**Status**: ACCEPTED
**Date**: 2026-06-18
**Extends**: [008-declarative-career-site-extraction.md](008-declarative-career-site-extraction.md),
[021-career-site-monitor-scraper-split.md](021-career-site-monitor-scraper-split.md),
[022-cloakbrowser-advanced-bypass.md](022-cloakbrowser-advanced-bypass.md)
**Closes**: Hard rule "no hardcoded domain-specific hosts in core composition" (AGENTS.md).

## Context

Three places in `infrastructure/sources/` contain hardcoded `if/elif` branches keyed on
hostname or substring match against a URL:

- `site_defaults.py:10-60` — five host-specific blocks (`career.habr.com`, `hh.ru`,
  `hh.kz`, `yandex.ru + /jobs`, `www.tbank.ru + /career`, `ozon.tech + /vacancies`).
  Each block picks a `url_filter`, `wait` strategy, `include_if_detail_page` flag, etc.
- `declarative.py:70-77` — `if spec.parser_kind == "greenhouse" or ("greenhouse.io" in url.lower())`
  and a parallel block for `alfabank.ru`.
- `monitors/greenhouse.py:103-108` — `if host == "boards-api.greenhouse.io"` / `if host == "boards.greenhouse.io"`.

AGENTS.md explicitly forbids "hardcoded domain-specific hosts or parser switches" in
`config.py` and by spirit anywhere in core composition. The current code violates that
rule, and each new site is a code change to `infrastructure/`, not a config change.

ADR-008 already introduced the declarative path (`CareerSiteConfig` + selector-based
extraction) but kept the URL → parser dispatch in Python `if/elif`. ADR-021 split
monitor/scraper but did not address URL → site_specific_parser routing. ADR-022 wired
CloakBrowser as one tier of the bypass chain. None of them addressed the per-site
runtime defaults (wait strategies, url_filter regex, render flag).

## Decision

Replace all three host-keyed dispatch sites with a single registry:

```python
@register_site_parser(
    "habr_career",
    domain_pattern=r"^https?://career\.habr\.com/",
    version="1.0",
    requires_extras=(),
)
def _habr_defaults(url: str) -> SiteRuntimeDefaults:
    return SiteRuntimeDefaults(
        url_filter=r"career\.habr\.com/vacancies/\d{5,}",
        render=False,
        wait="domcontentloaded",
    )
```

Concretely:

1. New Protocol `SiteParser` in `application/contracts.py` — `match(url) -> bool`,
   `runtime_defaults(url) -> SiteRuntimeDefaults`.
2. New `register_site_parser(name, *, domain_pattern, version, requires_extras=())`
   decorator in `application/registry.py`. Mirrors `@register_parser` shape.
3. `CareerSiteSpec` gains an optional `site_parser: str | None = None` field. If set,
   the named site parser is looked up via `resolve_site_parser(url)`. If `None` and
   the URL matches a registered site parser's `domain_pattern`, that one is auto-picked.
4. `apply_runtime_defaults(spec)` in `site_defaults.py` becomes a thin wrapper:
   `parser = resolve_site_parser(spec.url); return parser.runtime_defaults(spec.url) if parser else spec`.
5. `declarative.py:70-77` and `monitors/greenhouse.py:103-108` either:
   - register their known URLs as `site_parser("greenhouse_board_api", domain_pattern=...)` /
     `site_parser("alfabank_career", ...)` and route through the registry, or
   - keep the small per-monitor dispatch where it is a documented domain-specific
     optimization (with a unit test pinning the host).
6. `config/sources.schema.json` adds `site_parser: string | null` to the
   `career_site` oneOf branch.
7. `config/all_sources_full.yaml` ships one example using `site_parser: habr_career`
   to document the convention.

## Consequences

- (+) Zero `if/elif host == ...` in `infrastructure/`. All dispatch is in
  `application/registry.py` and goes through the same `register_*` decorator shape
  used by every other protocol.
- (+) Adding a site = a new `@register_site_parser` in a plugin module, or one
  YAML entry. No core code change.
- (+) CI module-boundary check (`scripts/check_module_boundaries.py`) can grow a
  rule "no string equality on URL hosts inside `infrastructure/`".
- (=) ADR-008 declarative path is unchanged; site parsers are a thinner layer on top.
- (=) ADR-021 monitor/scraper split is unchanged; site parsers only own URL-level
  runtime defaults, not discovery/extraction logic.
- (-) One more registry to keep documented; `site_parsers/` gets a new
  `registry.py` and one example plugin per known site.
- (-) `config/sources.schema.json` migration: existing YAMLs still work
  (site_parser is optional), but downstream tooling should learn the new field.
