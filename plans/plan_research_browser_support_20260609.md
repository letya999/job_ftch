# Research Task: Browser/JS Support for Career Site Sources

## Goal

Research what is needed to support the majority of career sites in job_ftch,
including JS-heavy SPAs and sites with moderate bot protection (Cloudflare
basic, cookie banners, JS rendering). We are NOT targeting Akamai/PerimeterX/
DataDome enterprise anti-bot — just normal sites that require a real browser
to render their job listings.

## Context

job_ftch already has:
- 15 monitors (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Breezy,
  Recruitee, Personio, Rippling, Workable, DOM, Sitemap, Nextdata, RSS board)
- 9 scrapers (JSON-LD, Embedded, Nextdata, DOM, Workday, SmartRecruiters,
  Workable, Rippling)
- All monitors use plain httpx (no browser)
- DOM monitor: static HTTP fetch only, no Playwright render support

jobseek (C:\Users\User\a_projects\jobseek) had:
- apps/crawler/src/shared/browser.py — full Playwright executor:
  - stealth mode (persistent Chrome profile)
  - headless: false option (for Xvfb)
  - cookie injection, action pipeline
  - warmup_url support
  - wait strategies (networkidle, domcontentloaded, load, commit)
  - cookie banner auto-dismiss
  - proxy support (Webshare/Decodo)
- apps/crawler/src/shared/proxy.py — provider-agnostic HTTP proxy layer
- DOM monitor in jobseek: render: true option → uses browser.py
- DOM monitor has pagination support (both static and browser-driven)

## What to Research

### 1. Read and summarize jobseek browser.py

Read C:\Users\User\a_projects\jobseek\apps\crawler\src\shared\browser.py completely.
Extract:
- open_page() context manager signature and behavior
- navigate() function signature
- run_actions() action types supported
- What stealth mode actually does (persistent_context + channel=chrome)
- How cookie banner dismissal works
- BROWSER_KEYS complete list and what each controls

### 2. Read and summarize jobseek proxy.py

Read C:\Users\User\a_projects\jobseek\apps\crawler\src\shared\proxy.py completely.
Extract:
- ProxyProvider protocol
- StaticProxyProvider implementation
- httpx_proxy_for() and playwright_proxy_for() signatures
- Which providers are supported

### 3. Read jobseek DOM monitor render=true implementation

Read C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\dom.py completely.
Focus on:
- How render: true switches from httpx to Playwright
- How pagination.browser: true works
- Action pipeline integration
- How url_filter config works
- _fetch_via_page() browser-fetch for pagination

### 4. Read current job_ftch DOM monitor

Read C:\Users\User\a_projects\job_ftch\job_ftch\infrastructure\sources\monitors\dom.py
Note what is missing vs jobseek version.

### 5. Read current job_ftch shared module

Read C:\Users\User\a_projects\job_ftch\job_ftch\infrastructure\sources\monitors\shared.py
Understand fetch_page_text(), User-Agent handling, retry logic currently in place.

### 6. Assess coverage gap

For each of the following site categories, determine which monitor/scraper
handles it today and what is missing:
a) Standard ATS (Greenhouse/Lever/Ashby etc.) — httpx API calls
b) Sitemap-based sites — httpx + XML parse
c) SPA sites (React/Vue/Next.js) — need JS render
d) Sites with cookie banner blocking — need banner dismiss
e) Sites behind basic Cloudflare (JS challenge) — need real browser UA or brief wait
f) Sites with pagination requiring JS click — need action pipeline
g) Sites requiring proxy to avoid IP blocks — need proxy layer

### 7. Determine minimal viable browser.py for job_ftch

Based on the above, what is the minimal subset of jobseek browser.py needed
to handle categories c-f above (NOT enterprise anti-bot)?
- Which BROWSER_KEYS are essential vs optional?
- Does job_ftch need Xvfb/headless:false? (probably not for basic sites)
- Does job_ftch need persistent Chrome profile (stealth)? (probably yes for
  basic Cloudflare JS challenge)
- Does job_ftch need proxy support? (optional, out of scope for now)

### 8. Survey what extra ATS monitors jobseek has that we don't

From C:\Users\User\a_projects\jobseek\apps\crawler\src\core\monitors\ list:
accenture, almacareer, amazon, bite, deel, dvinci, eightfold, gem, hireology,
inline, jobylon, join, mokahr, notion, oracle_hcm, personio, phenom, pinpoint,
recruiter_co_kr, softgarden, traffit, umantis
For each: read the file and assess — does it use httpx API (portable to job_ftch
without browser) or requires browser? Summarize in a table.

## Output Required

Produce a structured research report as a markdown file saved to:
C:\Users\User\a_projects\job_ftch\plans\research_browser_results_20260609.md

The report must contain:

### Section 1: browser.py key capabilities (table)
Feature | jobseek implementation | Needed for "most sites" (yes/no/optional)

### Section 2: Coverage gap table
Site category | Current job_ftch support | Missing piece

### Section 3: Minimal browser.py design for job_ftch
What to port, what to skip, estimated code lines.

### Section 4: Extra ATS monitors portability table
Monitor name | Uses browser? | API available? | Port effort (low/med/high)

### Section 5: Recommended implementation phases
Phase 1: Quick wins (what can be done with httpx improvements only)
Phase 2: Basic Playwright support (render: true for DOM monitor)
Phase 3: Additional ATS monitors (which ones, in priority order)

## Instructions

1. Read all files mentioned above thoroughly.
2. Do NOT write any implementation code.
3. Do NOT modify any existing files.
4. Only produce the research report markdown file.
5. Be specific and accurate — quote actual code where relevant.
