---
title: "ADR-025: Adaptive Site Intelligence (Site Fingerprinting)"
description: "* **Status:** ACCEPTED"
updated: 2026-07-24
---
# ADR-025: Adaptive Site Intelligence (Site Fingerprinting)

* **Status:** ACCEPTED
* **Date:** 2026-06-13

## Context
The current monitor selection process in `job_ftch` follows a "blind escalation" chain. When a source is configured as `monitor: auto`, the system tries a set of monitors (usually starting with `dom` or `api_sniffer`) without knowing the target site's architecture. This leads to several inefficiencies:
1. **Wrong monitors tried first:** We might attempt a heavy browser-based monitor on a simple SSR site where a plain-HTTP `dom` monitor would suffice.
2. **Manual hardcoding:** We often have to manually specify `monitor: rss_board` or `monitor: api_sniffer` even when the site's nature is obvious from a single probe.
3. **Slow discovery:** The system takes time to fail over through the escalation chain when the first choice is wrong.

## Decision
We will implement a `SiteFingerprinter` component that performs a fast, plain-HTTP probe of the target site *before* any monitor is selected. This probe classifies the site into one of several architectural categories and recommends an optimal order of monitors to try.

### Site Classes and Optimal Monitor Order

| Site Class | Description | Recommended Monitors (Ordered) |
| :--- | :--- | :--- |
| **SSR** | Server-Side Rendered (plain HTML) | `dom` |
| **SPA** | Single Page Application (needs browser or API) | `api_sniffer`, `dom` |
| **API_JSON** | Direct JSON API endpoint | `api_sniffer` |
| **RSS** | RSS/Atom feed | `rss_board`, `dom` |
| **BLOCKED** | Known protection or network error | `dom`, `api_sniffer` |
| **UNKNOWN** | Classification failed | `dom`, `api_sniffer` |

## Integration
The `SiteFingerprinter` integrates into `CareerSiteSource` via `MonitorDetector`. When `monitor: auto` is used, the system calls `get_ordered_monitors(url)` to determine the initial sequence.

This classification happens once at the start of the source lifecycle. If the recommended monitor fails, the standard bypass escalation takes over, but it starts from a much more informed position.

## Consequences

### Positive
* **Performance:** Faster selection of the "right" monitor.
* **Reliability:** Reduced unnecessary browser usage on SSR sites.
* **Automation:** Less manual configuration needed for obvious site types (like RSS or JSON APIs).

### Trade-offs
* **Extra Probe:** Adds one additional HTTP GET request at the start (offset by avoiding failing monitor attempts).
* **Heuristics:** Fingerprinting relies on regex and keyword scanning, which may occasionally misclassify (though the fallback chain mitigates this).
