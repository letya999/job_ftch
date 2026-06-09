from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

import jmespath

NEXT_DATA_RE = re.compile(
    r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)

REACT_ROUTER_RE = re.compile(
    r"window\.__staticRouterHydrationData\s*=\s*JSON\.parse\(\"(.+?)\"\);",
)

RSC_PUSH_RE = re.compile(
    r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)',
)

PHENOM_CANVAS_RE = re.compile(r"phApp\.ddo\s*=\s*")

_HTML_TAG_RE = re.compile(r"<[a-zA-Z/]")


def slugify(name: str) -> str:
    """Convert a name to a URL-safe slug."""
    text = unicodedata.normalize("NFKD", name)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = text.replace("&", "and")
    text = text.replace("+", "plus")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def find_json_extent(text: str, start: int) -> int | None:
    """Find the end index of a JSON object or array starting at *start*."""
    if start >= len(text):
        return None
    opener = text[start]
    if opener == "{":
        closer = "}"
    elif opener == "[":
        closer = "]"
    else:
        return None

    depth = 0
    in_string = False
    escape = False
    i = start

    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\":
            if in_string:
                escape = True
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            i += 1
            continue
        if in_string:
            i += 1
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1

    return None


def resolve_path(data: Any, path: str) -> Any:
    """Walk a path through nested dicts/lists using jmespath."""
    if not path:
        return data
    return jmespath.search(path, data)


def extract_field(item: dict, spec: str | list | dict, root: dict | None = None) -> Any:
    """Extract a value from *item* using a field spec."""
    if isinstance(spec, list):
        return _extract_concat(item, spec)

    if isinstance(spec, dict) and "concat" in spec:
        return _extract_concat(item, spec["concat"], separator=spec.get("separator", "\n"))

    if isinstance(spec, dict) and "lookup_from" in spec and "key_from" in spec:
        if root is None:
            return None
        key_val = jmespath.search(spec["key_from"], item)
        if key_val is None:
            return None
        table = jmespath.search(spec["lookup_from"], root)
        if not isinstance(table, dict):
            return None
        return table.get(str(key_val))

    if isinstance(spec, dict) and "path" in spec:
        if "map" in spec:
            result = jmespath.search(spec["path"], item)
            if result is None:
                return None
            value_map = spec["map"]
            if isinstance(result, list):
                mapped = [str(value_map[str(v)]) for v in result if str(v) in value_map]
                return mapped or None
            key = str(result)
            mapped = value_map.get(key)
            return str(mapped) if mapped is not None else None
        return extract_field(item, spec["path"], root=root)

    if isinstance(spec, str) and spec.startswith("="):
        return spec[1:]

    result = jmespath.search(spec, item)
    if result is None:
        return None
    if isinstance(result, list):
        values = [str(v) for v in result if v is not None]
        return values or None
    return str(result)


def _plain_to_html(text: str) -> str:
    """Convert plain text with newlines to HTML."""
    if _HTML_TAG_RE.search(text):
        return text
    if "\n" not in text:
        return text

    lines = text.split("\n")
    out: list[str] = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        is_bullet = stripped.startswith("- ")

        if is_bullet:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{stripped[2:]}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            if not stripped:
                out.append("<br>")
            else:
                out.append(stripped + "<br>")

    if in_list:
        out.append("</ul>")

    while out and out[-1] == "<br>":
        out.pop()
    if out and out[-1].endswith("<br>"):
        out[-1] = out[-1][:-4]

    return "\n".join(out)


def _extract_concat(item: dict, specs: list, separator: str = "\n") -> str | None:
    parts: list[str] = []
    pending_constants: list[str] = []
    had_data_expr = False

    for s in specs:
        if isinstance(s, str) and s.startswith("="):
            pending_constants.append(s[1:])
            continue

        if isinstance(s, dict):
            had_data_expr = True
            each_path = s.get("each", "")
            wrap_tpl = s.get("wrap", "")
            arr = jmespath.search(each_path, item)
            if not arr or not isinstance(arr, list):
                pending_constants.clear()
                continue
            parts.extend(pending_constants)
            pending_constants.clear()
            for obj in arr:
                if not isinstance(obj, dict):
                    parts.append(str(obj))
                    continue
                rendered = wrap_tpl
                for key, val in obj.items():
                    rendered = rendered.replace(f"{{{key}}}", str(val) if val is not None else "")
                parts.append(rendered)
            continue

        had_data_expr = True
        result = jmespath.search(s, item)
        if result is None:
            pending_constants.clear()
            continue

        parts.extend(pending_constants)
        pending_constants.clear()

        if isinstance(result, list):
            parts.extend(_plain_to_html(str(v)) for v in result if v is not None)
        else:
            parts.append(_plain_to_html(str(result)))

    if parts or not had_data_expr:
        parts.extend(pending_constants)

    return separator.join(parts) if parts else None


def extract_next_data(html: str) -> dict | None:
    match = NEXT_DATA_RE.search(html)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return None


def extract_react_router_data(html: str) -> dict | None:
    match = REACT_ROUTER_RE.search(html)
    if not match:
        return None
    try:
        unescaped = json.loads('"' + match.group(1) + '"')
        return json.loads(unescaped)
    except (json.JSONDecodeError, ValueError):
        return None


def extract_rsc_data(html: str) -> dict | None:
    chunks = RSC_PUSH_RE.findall(html)
    if not chunks:
        return None

    merged: dict = {}
    for raw in chunks:
        try:
            unescaped = json.loads('"' + raw + '"')
        except (json.JSONDecodeError, ValueError):
            continue

        for line in unescaped.split("\n"):
            line = line.strip()
            if not line:
                continue
            colon = line.find(":")
            if colon < 1:
                continue
            payload = line[colon + 1 :]
            if not payload or payload[0] not in "{[":
                continue
            try:
                parsed = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(parsed, list) and len(parsed) >= 4 and isinstance(parsed[3], dict):
                merged.update(parsed[3])
            elif isinstance(parsed, dict):
                merged.update(parsed)

    return merged or None


def extract_phenom_canvas_data(html: str) -> dict | None:
    match = PHENOM_CANVAS_RE.search(html)
    if not match:
        return None
    start = match.end()
    while start < len(html) and html[start] in " \t\r\n":
        start += 1
    if start >= len(html) or html[start] != "{":
        return None
    end = find_json_extent(html, start)
    if end is None:
        return None
    try:
        return json.loads(html[start:end])
    except (json.JSONDecodeError, ValueError):
        return None


def extract_embedded_json(html: str, source: str = "nextdata") -> dict | None:
    if source == "reactrouter":
        return extract_react_router_data(html)
    if source == "rsc":
        return extract_rsc_data(html)
    if source == "phenom_canvas":
        return extract_phenom_canvas_data(html)
    return extract_next_data(html)
