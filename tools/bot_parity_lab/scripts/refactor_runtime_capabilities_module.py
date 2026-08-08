from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "paritylab" / "static" / "probes" / "runtime.js"
CAPABILITIES = ROOT / "paritylab" / "static" / "probes" / "capabilities.js"
INDEX = ROOT / "paritylab" / "static" / "index.html"
START = "  const deadline = (promise, timeoutMs, label) => Promise.race(["
END = "\n  probes.createRuntimeCollector = helpers => {"


def main() -> None:
    source = RUNTIME.read_text(encoding="utf-8")
    if CAPABILITIES.exists():
        raise SystemExit("runtime capabilities have already been extracted")
    start = source.find(START)
    end = source.find(END, start)
    if start < 0 or end < 0:
        raise SystemExit("runtime capability boundaries not found")
    cluster = source[start:end]
    module = f"""(() => {{
  "use strict";

  const probes = window.ParityLabProbes = window.ParityLabProbes || {{}};
{cluster}
}})();
"""
    CAPABILITIES.write_text(module, encoding="utf-8")
    RUNTIME.write_text(source[:start] + source[end:], encoding="utf-8")
    index = INDEX.read_text(encoding="utf-8")
    anchor = '  <script defer src="/static/probes/runtime.js?sid=__SID__"></script>'
    INDEX.write_text(
        index.replace(
            anchor,
            '  <script defer src="/static/probes/capabilities.js?sid=__SID__"></script>\n' + anchor,
            1,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
