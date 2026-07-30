---
title: "ADR-073: nodriver AGPL-3.0 License Obligations"
description: "**Status:** Accepted"
updated: 2026-07-24
---
# ADR-073: nodriver AGPL-3.0 License Obligations

**Status:** Accepted  
**Date:** 2026-07-20  

## Context

nodriver (>=0.50.2) is an optional browser capability in the adaptive route
graph (ADR-074). It is licensed under AGPL-3.0-only.

AGPL-3.0 requires that if you distribute the software or provide it
as a network service (SaaS), you must make the complete corresponding
source code available under the same license.

## Decision

1. **Internal use is unrestricted.** Running job_ftch internally
   (self-hosted, private deployment) does not trigger AGPL copyleft.

2. **SaaS or public-facing deployment** of job_ftch that exercises
   nodriver triggers AGPL section 13 (Affero clause). Before any such
   deployment, the project owner must either:
   - Release the full job_ftch source under a compatible license, OR
   - Remove nodriver from the dependency tree and disable its tier.

3. **Distribution of job_ftch binaries/packages** that include nodriver
   requires AGPL-compliant source disclosure.

4. nodriver remains an optional extra (`[nodriver]`), not a core
   dependency. The route graph degrades gracefully if it is absent
   (logs a WARNING, skips the capability).

## Consequences

- No change for internal/private use.
- Any future decision to offer job_ftch as a hosted service requires
  legal review of this ADR first.
- The `[all]` extra still pins nodriver but consumers are informed via
  this ADR and LICENSE-THIRD-PARTY notices.
