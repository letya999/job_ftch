from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "paritylab" / "static" / "probe.js"
RUNTIME = ROOT / "paritylab" / "static" / "probes" / "runtime.js"
START = "  const propertyDescriptor ="
END = "\n  const probeDeep ="
WRAPPER = """  const runtimeCollector = window.ParityLabProbes.createRuntimeCollector({
    safe, withTimeout, hashBytes, hashString, normalize, captureError, performanceSamples, sid
  });
  const collectWindowProbe = runtimeCollector.collectWindowProbe;
  const collectIframeProbe = runtimeCollector.collectIframeProbe;
  const probePerformance = runtimeCollector.probePerformance;
"""


def main() -> None:
    main_source = MAIN.read_text(encoding="utf-8")
    runtime_source = RUNTIME.read_text(encoding="utf-8")
    if "probes.createRuntimeCollector = helpers =>" in runtime_source:
        raise SystemExit("runtime collector has already been extracted")
    start = main_source.find(START)
    end = main_source.find(END, start)
    if start < 0 or end < 0:
        raise SystemExit("runtime collector boundaries not found")
    cluster = main_source[start:end]
    module = f"""  probes.createRuntimeCollector = helpers => {{
    const {{safe, withTimeout, hashBytes, hashString, normalize, captureError, performanceSamples, sid}} = helpers;
{cluster}
    return {{collectWindowProbe, collectIframeProbe, probePerformance}};
  }};
"""
    marker = "\n})();\n"
    if marker not in runtime_source:
        raise SystemExit("runtime module closure marker not found")
    RUNTIME.write_text(runtime_source.replace(marker, f"\n{module}\n{marker}", 1), encoding="utf-8")
    MAIN.write_text(main_source[:start] + WRAPPER + main_source[end:], encoding="utf-8")


if __name__ == "__main__":
    main()
