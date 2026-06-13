# Fix: XXE / XML Entity Expansion in XML-parsing monitors

## Problem

Three monitors parse XML from untrusted external URLs using stdlib
`xml.etree.ElementTree`, which does not protect against:
- Billion-laughs (entity expansion DoS)
- Quadratic blowup attacks
- External entity injection (SSRF via XML)

All three files fetch XML from career site URLs — fully external, untrusted input.

## Files to modify

### 1. job_ftch/infrastructure/sources/monitors/personio.py
- Find: `import xml.etree.ElementTree as ET`
- Replace: `import defusedxml.ElementTree as ET`
- No other changes needed — API is identical.

### 2. job_ftch/infrastructure/sources/monitors/rss_board.py
- Find: `import xml.etree.ElementTree as ET`
- Replace: `import defusedxml.ElementTree as ET`
- No other changes needed.

### 3. job_ftch/infrastructure/sources/monitors/sitemap.py
- Find: `import xml.etree.ElementTree as ET`
- Replace: `import defusedxml.ElementTree as ET`
- No other changes needed.

## Dependency

Add `defusedxml` to pyproject.toml dependencies.

Read pyproject.toml first to find the correct dependencies section.
Add `"defusedxml>=0.7.1"` to the main dependencies list (not optional).

## Verification

After making changes, run:
```
python -m pytest tests/ -q --tb=short -k "sitemap or rss or personio" 2>&1 | tail -10
```
Expected: all pass (or skip if optional deps missing).

Also verify import works:
```
python -c "import defusedxml.ElementTree as ET; print('ok')"
```

## Instructions

1. Read each file to find the exact import line before replacing.
2. Read pyproject.toml before modifying it.
3. Make minimal changes — only the import swap and the dependency addition.
4. Do NOT refactor, rename, or change any logic.
