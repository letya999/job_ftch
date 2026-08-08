from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEEP = ROOT / "paritylab" / "static" / "probes" / "deep.js"
RENDERING = ROOT / "paritylab" / "static" / "probes" / "rendering.js"
INDEX = ROOT / "paritylab" / "static" / "index.html"
START = "  const geometryProbe = async hashString => {"
END = "\n  probes.collectDeepExtras = async helpers => {"
WIRE = """  const {geometryProbe, webGpuProbe, mediaProbe, fontRenderingProbe} =
    probes.createRenderingCollectors({finiteObject, withTimeout});
"""


def main() -> None:
    source = DEEP.read_text(encoding="utf-8")
    if RENDERING.exists():
        raise SystemExit("rendering collectors have already been extracted")
    start = source.find(START)
    end = source.find(END, start)
    if start < 0 or end < 0:
        raise SystemExit("rendering collector boundaries not found")
    cluster = source[start:end]
    module = f"""(() => {{
  "use strict";

  const probes = window.ParityLabProbes = window.ParityLabProbes || {{}};
  probes.createRenderingCollectors = helpers => {{
    const {{finiteObject, withTimeout}} = helpers;
{cluster}
    return {{geometryProbe, webGpuProbe, mediaProbe, fontRenderingProbe}};
  }};
}})();
"""
    RENDERING.write_text(module, encoding="utf-8")
    DEEP.write_text(source[:start] + WIRE + source[end:], encoding="utf-8")
    index = INDEX.read_text(encoding="utf-8")
    anchor = '  <script defer src="/static/probes/deep.js?sid=__SID__"></script>'
    INDEX.write_text(
        index.replace(
            anchor,
            '  <script defer src="/static/probes/rendering.js?sid=__SID__"></script>\n' + anchor,
            1,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
