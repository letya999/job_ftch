"""HH.ru AI Developer single-session scenario: discovery, evidence capture,
provider-backed CAPTCHA solve proof. No production DB writes, no Telegram.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import quote

from job_ftch.infrastructure.browser_session import OperatorBrowserSessionService
from job_ftch.infrastructure.bypass.proxy_bypass import _load_residential_proxies

TENANT = "captcha-check"
SEARCH = "https://hh.ru/search/vacancy?text={q}&page={page}"
QUERY = "AI Developer"
PAGES = 9
EVIDENCE = Path(".runtime/runs/hh_ai_dev_evidence")

_VACANCY_RE = re.compile(r"https://hh\.ru/vacancy/\d+")
_LINKS_JS = "() => Array.from(document.querySelectorAll('a')).map(a => a.href)"
_H2_JS = "() => document.body.innerText.slice(0, 2500)"


def _persist(path: Path, name: str, data: dict) -> dict:
    target = path / f"{name}.json"
    target.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8-sig")
    return {"saved": str(target)}


async def _capture_snapshot(service: OperatorBrowserSessionService, sid: str, page: int, out: Path) -> dict:
    info = await service.get(sid)
    challenge = info.get("challenge") or ""
    html = await service.capture(sid, "html")
    saved: dict = {}
    if challenge:
        shot = await service.capture(sid, "screenshot")
        if shot and shot.get("path"):
            png = out / f"page{page:02d}_challenge_screenshot.png"
            shutil.copyfile(Path(str(shot["path"])), png)
            saved["screenshot"] = str(png)
    saved.update(_persist(out, f"page{page:02d}_snapshot", {"snapshot": info, "html_prefix": html.get("html", "")[:2000]}))
    return {"challenge": challenge, "status": info.get("status"), "saved": saved}


async def _js_links(session, page) -> tuple[list[str], str]:
    try:
        hrefs = await page.evaluate(_LINKS_JS)
        await page.evaluate(_H2_JS)
    except Exception as exc:
        return [], f"eval_error:{type(exc).__name__}"
    links = sorted({h.split("?", 1)[0].split("#", 1)[0] for h in hrefs if isinstance(h, str) and _VACANCY_RE.fullmatch(h.split("?", 1)[0].split("#", 1)[0])})
    return links, "ok"


async def _walk_search(service: OperatorBrowserSessionService, sid: str, out: Path) -> dict:
    seen: set[str] = set()
    pages_info: list[dict] = []
    await service.continue_session(sid, "extend")
    for page in range(PAGES):
        nav = await service.continue_session(sid, f"navigate {SEARCH.format(q=quote(QUERY), page=page)}")
        if nav.get("status") != "ok":
            break
        await asyncio.sleep(2.5)
        snap = await _capture_snapshot(service, sid, page, out)
        borrowed = await service.borrow(sid, TENANT)
        if not isinstance(borrowed, dict):
            try:
                links, mode = await _js_links(service, borrowed.page)
            finally:
                await service.release(sid)
        else:
            links, mode = [], str(borrowed.get("error", "borrow_failed"))
        seen.update(links)
        snap["vacancy_links"] = len(links)
        snap["link_mode"] = mode
        pages_info.append(snap)
        if snap["challenge"]:
            break
        await service.continue_session(sid, "extend")
    return {"pages": pages_info, "vacancy_urls": sorted(seen)}


async def _solve_challenge(service: OperatorBrowserSessionService, sid: str, out: Path) -> dict:
    before = await _capture_snapshot(service, sid, 99, out)
    result = await service.continue_session(sid, "solve:provider")
    await asyncio.sleep(3.0)
    after = await service.get(sid)
    result_entry = {"solve_result": result.get("captcha"), "challenge_after": after.get("challenge"), "cleared": not after.get("challenge")}
    result_entry["saved"] = _persist(out, "captcha_solve_result", {"before": before, "result": result_entry})
    return result_entry


async def _check_details(service: OperatorBrowserSessionService, sid: str, vacancy_urls: list[str], out: Path) -> dict:
    details: list[dict] = []
    for url in vacancy_urls[:3]:
        nav = await service.continue_session(sid, f"navigate {url}")
        if nav.get("status") != "ok":
            continue
        await asyncio.sleep(2.0)
        charstr: str = ""
        borrowed = await service.borrow(sid, TENANT)
        chars = 0
        if not isinstance(borrowed, dict):
            try:
                charstr = str(await borrowed.page.evaluate(_H2_JS) or "")
                chars = len(charstr)
                (out / f"detail_{url.rsplit('/', 1)[-1]}.txt").write_text(charstr, encoding="utf-8-sig")
            finally:
                await service.release(sid)
        details.append({"url": url, "text_chars": chars, "status": nav.get("status"), "challenge": nav.get("challenge")})
        await service.continue_session(sid, "extend")
    _persist(out, "details_probe", {"details": details})
    return {"details": details}


async def main() -> int:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = EVIDENCE / stamp
    out.mkdir(parents=True, exist_ok=True)
    proxies = _load_residential_proxies()
    service = OperatorBrowserSessionService()
    report: dict = {"started": stamp, "query": QUERY, "residential_endpoints": len(proxies)}
    info = await service.open(
        tenant_id=TENANT,
        url=SEARCH.format(q=quote(QUERY), page=0),
        engine="residential_proxy",
        headed=False,
        manual_challenge=False,
    )
    sid = info.get("session_id", "")
    report["open"] = {k: info.get(k) for k in ("status", "error", "challenge", "final_url", "page_title", "session_id")}
    if not sid:
        report["failure"] = "no session"
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        return 1
    walk = await _walk_search(service, sid, out)
    report["walk"] = {"pages": walk["pages"], "vacancy_count": len(walk["vacancy_urls"])}
    captcha_steps = [p for p in walk["pages"] if p["challenge"]]
    if captcha_steps:
        report["solve"] = await _solve_challenge(service, sid, out)
        if report["solve"]["cleared"]:
            walk2 = await _walk_search(service, sid, out)
            report["recovery_walk"] = {"vacancy_count": len(walk2["vacancy_urls"])}
    if walk["vacancy_urls"]:
        report["details_probe"] = await _check_details(service, sid, walk["vacancy_urls"], out)
    await service.close(sid)
    from job_ftch.infrastructure.bypass.proxy_bypass import get_cost_tracker

    tracker = get_cost_tracker()
    report["proxy_budget"] = {
        "total_gb": round(tracker.total_gb, 4),
        "gb_budget": tracker.gb_budget,
        "top_domains_mb": [(d, round(b / 1024**2, 2)) for d, b in tracker.top_domains(5)],
        "m_hh_domain_gb": round(tracker.domain_gb("m.hh.ru"), 4),
        "hh_domain_gb": round(tracker.domain_gb("hh.ru"), 4),
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: report.get(k) for k in ("open", "walk", "solve", "details_probe", "proxy_budget", "failure")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
