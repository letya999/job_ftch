---
title: "085 — Source-assessed career-site search recipes"
description: "**Status**: ACCEPTED"
updated: 2026-09-01
---
# 085 — Source-assessed career-site search recipes

**Status**: ACCEPTED
**Date**: 2026-09-01
**Extends**: [033-plugin-based-domain-parsers.md](033-plugin-based-domain-parsers.md),
[072-career-site-deadline-and-global-work-budgets.md](072-career-site-deadline-and-global-work-budgets.md)

## Context

Career-site search was split between parser-specific URL builders and a generic
GET-form fallback. A `supports_search` flag made these paths mutually exclusive,
so a specific parser could silently skip a usable search box. Some parsers also
advertised search while returning a bare listing URL.

## Decision

1. Career-site source assessment probes search capabilities for known and unknown
   sites. Registry hints seed the probe but do not bypass it.
2. Assessment stores a safe, source-scoped search recipe: executor, query mode,
   form action/parameter and verification evidence. It never stores target roles,
   cookies, CSRF values or credentials.
3. Runtime substitutes the current profile target roles into the stored recipe.
   Search discovery is not repeated on every run while the recipe is fresh.
4. The search executor and vacancy extractor are independent. A generic browser
   search may feed a specific site parser, and a specific URL search may feed a
   generic monitor when that is the verified working combination.
5. A specific parser with no verified URL/API candidate remains eligible for the
   shared GET/POST/browser search path.
6. Search verification requires a positive query to change the result surface
   and a negative control not to reproduce the same result. Equal-count or
   unchanged-URL candidates are not accepted as working search.
7. Search assessment is bounded, SSRF-guarded and never creates `RawItem` or
   invokes the relevance pipeline.

## Consequences

- (+) Search behavior is explicit, cached and observable per source.
- (+) Specific parsers no longer hide generic search-box opportunities.
- (+) Target-role changes do not require re-assessing the source surface.
- (-) The first assessment of a source performs a few additional bounded probes.
- (-) Browser-only searches without a reproducible result URL remain degraded.
