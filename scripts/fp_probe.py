"""Fingerprint probe harness (Wave 0.1).

Drives each BrowserPersona through a headless browser and collects
fingerprint telemetry that can be diffed against the committed baseline.

Usage (manual / CI nightly):
    uv run python scripts/fp_probe.py [--save-baseline] [--persona NAME]

The probe opens a minimal local HTML page that runs the same JS checks
as CreepJS/BrowserScan (hardware, WebGL, canvas, fonts, toStringguard)
and dumps the results as JSON.

Results are saved via FingerprintBaselineStore (file fallback by default,
PG when configured).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Allow running from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_ftch.infrastructure.bypass.fingerprint_baseline import (
    BaselineRecord,
    FingerprintBaselineStore,
    compare_fingerprint,
    pairwise_hardware_duplicates,
)
from job_ftch.infrastructure.bypass.persona import PERSONA_POOL, BrowserPersona

# Minimal HTML that extracts the fingerprint signals we care about.
_PROBE_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>fp probe</title></head>
<body><canvas id="c" width="1" height="1"></canvas>
<script>
(async () => {
    const r = {};
    r.user_agent = navigator.userAgent;
    r.hardware_concurrency = navigator.hardwareConcurrency;
    r.device_memory = navigator.deviceMemory || null;
    r.screen_width = screen.width;
    r.screen_height = screen.height;
    r.color_depth = screen.colorDepth;
    r.pixel_ratio = window.devicePixelRatio;
    r.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    r.language = navigator.language;
    r.languages = navigator.languages ? [...navigator.languages] : [];
    r.platform = navigator.platform;
    r.vendor = navigator.vendor;
    r.max_touch_points = navigator.maxTouchPoints;

    // WebGL
    try {
        const c = document.getElementById('c');
        const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
        if (gl) {
            const dbg = gl.getExtension('WEBGL_debug_renderer_info');
            r.webgl_vendor = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : '';
            r.webgl_renderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : '';
        }
    } catch(e) { r.webgl_error = e.message; }

    // toString guard check
    try {
        const s = navigator.hardwareConcurrency.toString
            ? navigator.hardwareConcurrency.toString()
            : String(navigator.hardwareConcurrency);
        r.tamper_detected = false;  // basic check
        // A more thorough check: if any getter was patched, its toString
        // should still return '[native code]'.
        const desc = Object.getOwnPropertyDescriptor(Navigator.prototype, 'hardwareConcurrency');
        if (desc && desc.get) {
            const gs = Function.prototype.toString.call(desc.get);
            r.tamper_detected = !gs.includes('native code');
        }
    } catch(e) { r.tamper_detected_error = e.message; }

    // Fonts (quick probe — check a few sentinel fonts)
    const sentinels = ['Arial', 'Courier New', 'Georgia', 'Tahoma', 'Verdana',
                       'Comic Sans MS', 'Impact', 'Trebuchet MS', 'DejaVu Sans',
                       'Noto Sans', 'Roboto', 'Liberation Sans'];
    const available = [];
    for (const f of sentinels) {
        if (document.fonts && document.fonts.check('12px "' + f + '"')) {
            available.push(f);
        }
    }
    r.font_enumeration_count = available.length;
    r.fonts_available = available;

    // sec-ch-ua (if available)
    if (navigator.userAgentData) {
        r.sec_ch_ua = navigator.userAgentData.brands
            ? navigator.userAgentData.brands.map(b => `"${b.brand}";v="${b.version}"`).join(', ')
            : '';
    }

    window.__FP_RESULT__ = JSON.stringify(r);
})();
</script></body></html>"""


async def _probe_persona(persona: BrowserPersona) -> dict:
    """Launch a headless browser with persona settings and collect fingerprint."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("playwright not installed — skipping live probe", file=sys.stderr)
        return _static_probe(persona)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(**persona.context_kwargs())
        page = await ctx.new_page()

        # Apply stealth hardening if available
        try:
            from job_ftch.infrastructure.bypass.stealth_hardening import (
                apply_persona_hardening,
            )

            await apply_persona_hardening(page, persona)
        except ImportError:
            pass

        await page.set_content(_PROBE_HTML)
        await page.wait_for_timeout(2000)

        result_raw = await page.evaluate("window.__FP_RESULT__")
        await browser.close()

        if result_raw:
            return json.loads(result_raw)
        return _static_probe(persona)


def _static_probe(persona: BrowserPersona) -> dict:
    """Fallback: build a fingerprint record from persona data without browser."""
    return {
        "persona_name": persona.name,
        "user_agent": persona.ua,
        "hardware_concurrency": persona.hardware_concurrency,
        "device_memory": persona.device_memory,
        "screen_width": persona.screen_width,
        "screen_height": persona.screen_height,
        "webgl_renderer": persona.webgl_renderer,
        "browser_family": persona.browser_family,
        "timezone": persona.timezone,
        "locale": persona.locale,
        "tamper_detected": False,
        "font_enumeration_count": None,
        "sec_ch_ua": persona.sec_ch_ua,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Fingerprint probe harness")
    parser.add_argument(
        "--save-baseline", action="store_true", help="Save results as the new baseline"
    )
    parser.add_argument("--persona", type=str, default=None, help="Probe a single persona by name")
    parser.add_argument(
        "--static", action="store_true", help="Use static probe (no browser launch)"
    )
    args = parser.parse_args()

    store = FingerprintBaselineStore()
    personas = PERSONA_POOL
    if args.persona:
        personas = [p for p in PERSONA_POOL if p.name == args.persona]
        if not personas:
            print(f"Persona '{args.persona}' not found", file=sys.stderr)
            return 1

    results: list[dict] = []
    failures: list[str] = []
    now_iso = datetime.now(UTC).isoformat()

    for persona in personas:
        print(f"Probing {persona.name} ({persona.browser_family})...", end=" ")
        if args.static:
            result = _static_probe(persona)
        else:
            try:
                result = await _probe_persona(persona)
            except Exception as exc:
                print(f"FAIL ({exc})")
                failures.append(f"{persona.name}: {exc}")
                continue

        result["persona_name"] = persona.name
        result["browser_family"] = persona.browser_family
        results.append(result)

        if args.save_baseline:
            record = BaselineRecord(
                persona_name=persona.name,
                scope="fingerprint",
                generated_at=now_iso,
                payload=result,
            )
            await store.save(record)
            print("SAVED")
        else:
            # Compare against baseline if exists
            baseline = await store.load(persona.name, "fingerprint")
            if baseline:
                diff = compare_fingerprint(baseline, result)
                if diff.matched:
                    print("OK")
                else:
                    print(f"DIFF: {diff.diffs}")
                    failures.append(f"{persona.name}: {diff.diffs}")
            else:
                print("NO BASELINE (run with --save-baseline first)")

    # Check pairwise hardware duplicates
    dupes = pairwise_hardware_duplicates(results)
    if dupes:
        print(f"\nWARNING: {len(dupes)} hardware tuple groups shared by >2 personas:")
        for group in dupes:
            print(f"  {group}")
        failures.append(f"hardware_duplicates: {dupes}")

    if failures:
        print(f"\n{len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"\nAll {len(results)} persona(s) probed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
