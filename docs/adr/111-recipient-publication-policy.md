---
title: "111 — Recipient-specific publication policy"
description: "Evaluate geography and language on canonical vacancy facts for each publication target."
updated: 2026-11-25
---
# 111 — Recipient-specific publication policy

**Status** Accepted

The tenant config can specify a default audience and overrides for a Telegram
target or the public catalog. Each rule evaluates eligible work locations,
posting languages and verified employer languages. Missing evidence yields
an explicit review or skip. Shadow mode records what the rule would do.

The application evaluates one policy on canonical facts before delivery and
before the public catalog limit. Receipts retain their original identity
when the policy changes. Policy outcomes have their own versioned keys.
Existing tenants without a policy keep their current audience.

Technology tags retain their API contract. Grade, role, country, city, source
language and work mode are separate facets shared by the catalog and Telegram.
