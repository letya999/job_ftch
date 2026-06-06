# 008 - Declarative Career Site Extraction

**Status**: ACCEPTED
**Date**: 2026-06-06

## Context
One parser class per career site does not scale to dozens of boards. Common boards mostly differ in selectors, field mapping, and lightweight metadata extraction rather than control flow.

## Decision
Add declarative `CareerSiteConfig` and `DeclarativeCareerSiteParser` for selector-driven extraction. Greenhouse is migrated to the declarative path as the first proof. Python parser classes remain available as fallback when a site needs custom request or parsing logic.

## Consequences
- (+) "Add a site" becomes mostly config work instead of new parser classes.
- (+) Common board families can reuse one generic implementation.
- (-) Complex boards still need imperative fallbacks, so both declarative and custom paths must coexist.
