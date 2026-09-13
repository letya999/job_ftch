"""HH.ru CAPTCHA stress trigger: churn short sessions through search pages,
save full evidence on any captcha-class challenge and prove provider solve.
"""

from __future__ import annotations

import asyncio
import json
import random
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import quote

from job_ftch.infrastructure.browser_session import OperatorBrowserSessionService

TENANT = "captcha-stress"
QUERIES = ["AI Developer", "ML engineer", "python developer", "frontend developer"]
EVIDENCE = Path(".runtime/runs/hh_captcha_stress")
MAX_SESSIONS = int(sys.argv[1]) if len(sys.argv) > 1 else 8
_SOFT_CHALLENGES = {"blocked_fingerprint"}


def _save(out: Path, name: str, data: dict) -> None:
    (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8-sig")



async def _evidence(service: OperatorBrowserSessionService, sid: str, out: Path, tag: str) -> dict:
    snap = await service.get(sid)
    html = await service.capture(sid, "html")
    shot = await service.capture(sid, "screenshot")
    trace = await service.capture(sid, "trace")
    saved: dict = {}
    if shot and shot.get("path"):
        png = out / f"{tag}_screenshot.png"
        shutil.copyfile(Path(str(shot["path"])), png)
        saved["screenshot"] = str(png)
    _save(out, f"{tag}_snapshot.json", {"snapshot": snap, "html_prefix": html.get("html", "")[:2000]})
    return {"challenge": snap.get("challenge"), "final_url": snap.get("final_url"), "saved": saved, "trace": trace.get("trace", {}).get("kind")}


async def main() -> int:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = EVIDENCE / stamp
    out.mkdir(parents=True, exist_ok=True)
    service = OperatorBrowserSessionService()
    results: list[dict] = []
    captcha_hits = 0
    solved_proof: dict | None = None
    for i in range(MAX_SESSIONS):
        query = random.choice(QUERIES)
        url = f"https://hh.ru/search/vacancy?text={quote(query)}&page={random.randint(0, 5)}"
        info = await service.open(tenant_id=TENANT, url=url, engine="residential_proxy", headed=False)
        entry: dict = {"session": i, "requested": url, "status": info.get("status")}
        sid = info.get("session_id", "")
        if not sid:
            entry["error"] = info.get("error")
            results.append(entry)
            continue
        challenge = (info.get("challenge") or "").strip()
        if challenge and challenge not in _SOFT_CHALLENGES:
            captcha_hits += 1
            entry["captcha"] = await _evidence(service, sid, out, f"sess{i}_captcha")
            result = await service.continue_session(sid, "solve:provider")
            post = await service.get(sid)
            entry["solve"] = {"result": result.get("captcha"), "challenge_after": post.get("challenge"), "cleared": not post.get("challenge")}
            if entry["solve"]["cleared"]:
                nav = await service.continue_session(sid, f"navigate {url}")
                entry["after_recovery_nav"] = {"status": nav.get("status"), "challenge": nav.get("challenge")}
                solved_proof = entry["solve"]
        elif challenge:
            entry["soft_challenge"] = challenge
        # quick page churn to build pressure
        for page in (0, 1, 2):
            nav = await service.continue_session(sid, f"navigate {f'https://hh.ru/search/vacancy?text={quote(query)}&page={page}'}")
            if nav.get("status") != "ok":
                break
            ch = (nav.get("challenge") or "").strip()
            if ch and ch not in _SOFT_CHALLENGES:
                captcha_hits += 1
                entry[f"churn_captcha_{page}"] = await _evidence(service, sid, out, f"sess{i}_p{page}_captcha")
                break
            await asyncio.sleep(random.uniform(0.4, 1.2))
        await service.close(sid)
        results.append(entry)
        await asyncio.sleep(random.uniform(0.3, 1.0))
    summary = {"started": stamp, "sessions": results, "captcha_challenges": captcha_hits, "solved_proof": solved_proof}
    _save(out, "summary.json", summary)
    print(json.dumps({"captcha_challenges": captcha_hits, "solved_proof": solved_proof, "dir": str(out)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
