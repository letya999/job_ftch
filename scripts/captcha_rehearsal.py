"""Rehearsal v2: churn same-site navigations until a challenge appears,
capture evidence, run provider solve, prove the site accepted and ingest
continued in the same session.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

from job_ftch.infrastructure.browser_session import OperatorBrowserSessionService

TENANT = "captcha-rehearsal"
EVIDENCE = Path(".runtime/runs/captcha_rehearsal")
_TARGETS = [
    (
        "kadrof",
        "https://www.kadrof.ru/work",
        [
            "https://www.kadrof.ru/work/remote-work",
            "https://www.kadrof.ru/work/moscow",
            "https://www.kadrof.ru/work/all",
        ],
        "https://www.kadrof.ru/work",
    ),
    (
        "telecom_kz",
        "https://telecom.kz/ru/career",
        [
            "https://telecom.kz/ru/news",
            "https://telecom.kz/ru/company",
            "https://telecom.kz/ru/career",
        ],
        "https://telecom.kz/ru/career",
    ),
    (
        "airastana",
        "https://airastana.com/kaz/en-us/About-Us/Careers",
        ["https://airastana.com/kaz/en-us/About-Us/Careers"],
        "https://airastana.com/kaz/en-us/About-Us/Careers",
    ),
]
_LINKS_JS = "() => Array.from(document.querySelectorAll('a')).map(a => a.href).filter(u => u && !u.startsWith('javascript')).length"
_MAX_NAV = 10


def _save(out: Path, name: str, data: dict) -> None:
    (out / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8-sig"
    )


async def _full_evidence(
    service: OperatorBrowserSessionService, sid: str, out: Path, tag: str
) -> dict:
    snap = await service.get(sid)
    html = await service.capture(sid, "html")
    shot = await service.capture(sid, "screenshot")
    trace = await service.capture(sid, "trace")
    saved: dict = {"html_prefix_chars": len(html.get("html", "") or "")}
    if shot and shot.get("path"):
        png = out / f"{tag}_screenshot.png"
        shutil.copyfile(Path(str(shot["path"])), png)
        saved["screenshot"] = str(png)
    _save(out, f"{tag}_snapshot", {"snapshot": snap, "html_prefix": html.get("html", "")[:3000]})
    return {
        "challenge": snap.get("challenge"),
        "final_url": snap.get("final_url"),
        "page_title": snap.get("page_title"),
        "saved": saved,
        "trace_kind": trace.get("trace", {}).get("kind"),
    }


async def _churn(
    service: OperatorBrowserSessionService, name: str, url: str, paths: list[str], out: Path
) -> dict:
    site_out = out / name
    site_out.mkdir(parents=True, exist_ok=True)
    result: dict = {"site": name, "entry": url}
    info = await service.open(
        tenant_id=TENANT,
        url=url,
        engine="residential_proxy",
        headed=False,
        bypass_config={"wait_seconds": 60},
    )
    sid = info.get("session_id", "")
    if not sid:
        result["open_error"] = info.get("error")
        return result
    navs: list[dict] = []
    for i in range(_MAX_NAV):
        nav = await service.continue_session(sid, f"navigate {paths[i % len(paths)]}")
        ch = (nav.get("challenge") or "").strip()
        navs.append(
            {
                "url": nav.get("final_url", paths[i % len(paths)]),
                "status": nav.get("status"),
                "challenge": ch,
            }
        )
        if ch:
            result["evidence"] = await _full_evidence(service, sid, site_out, f"challenge_{ch}")
            solve = await service.continue_session(sid, "solve:provider")
            post = await service.get(sid)
            solved = {
                "result": solve.get("captcha"),
                "challenge_after": post.get("challenge"),
                "cleared": not post.get("challenge"),
            }
            result["solve"] = solved
            _save(site_out, "solve_result", solved)
            nav = await service.continue_session(sid, f"navigate {paths[i % len(paths)]}")
            navs.append(
                {"retry_after_solve_status": nav.get("status"), "challenge": nav.get("challenge")}
            )
            if not post.get("challenge") and nav.get("status") == "ok":
                await asyncio.sleep(2.0)
                borrowed = await service.borrow(sid, TENANT)
                if not isinstance(borrowed, dict):
                    try:
                        solved["links_after_clear"] = await borrowed.page.evaluate(_LINKS_JS)
                    finally:
                        await service.release(sid)
            break
        await asyncio.sleep(1.0)
    result["navs"] = navs
    result["challenge_seen"] = any(n["challenge"] for n in navs if "challenge" in n)
    await service.close(sid)
    _save(site_out, "site_result", result)
    return result


async def main() -> int:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = EVIDENCE / stamp
    out.mkdir(parents=True, exist_ok=True)
    service = OperatorBrowserSessionService()
    results = []
    for name, url, paths, _ in _TARGETS:
        print(f"=== {name} ===", flush=True)
        results.append(await _churn(service, name, url, paths, out))
    _save(out, "summary", {"started": stamp, "results": results})
    for r in results:
        print(json.dumps(r, ensure_ascii=False)[:1800])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
