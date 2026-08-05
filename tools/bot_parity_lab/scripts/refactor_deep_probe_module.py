from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "paritylab" / "static" / "probe.js"
DEEP = ROOT / "paritylab" / "static" / "probes" / "deep.js"
START = "  const probeDeep = async () => {"
END = "\n  const runWorker ="
WRAPPER = """  const probeDeep = async () => {
    if (!window.ParityLabProbes || typeof window.ParityLabProbes.collectDeep !== "function") {
      throw new Error("modular deep probe unavailable");
    }
    return window.ParityLabProbes.collectDeep({safe, hashString});
  };
"""


def main() -> None:
    main_source = MAIN.read_text(encoding="utf-8")
    deep_source = DEEP.read_text(encoding="utf-8")
    if "probes.collectDeep = async helpers =>" in deep_source:
        raise SystemExit("deep probe has already been extracted")
    start = main_source.find(START)
    end = main_source.find(END, start)
    if start < 0 or end < 0:
        raise SystemExit("probeDeep boundaries not found")
    function = main_source[start:end]
    function = function.replace(
        START,
        "  probes.collectDeep = async helpers => {\n    const {safe, hashString} = helpers;",
        1,
    )
    marker = "\n})();\n"
    if marker not in deep_source:
        raise SystemExit("deep module closure marker not found")
    DEEP.write_text(deep_source.replace(marker, f"\n{function}\n{marker}", 1), encoding="utf-8")
    MAIN.write_text(main_source[:start] + WRAPPER + main_source[end:], encoding="utf-8")


if __name__ == "__main__":
    main()
