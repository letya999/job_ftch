import argparse
import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import structlog
import yaml

logger = structlog.get_logger("update_proxies")

SOURCES = [
    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=all",
    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=5000&country=all",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt",
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-socks5.txt",
    "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt",
    "https://raw.githubusercontent.com/mmpx12/proxy-list/master/socks5.txt",
    "https://raw.githubusercontent.com/proxy4parsing/proxy-list/main/http.txt",
]

VALIDATION_ENDPOINTS = [
    "https://httpbin.org/ip",
    "https://api.ipify.org?format=json",
    "https://ifconfig.me/ip",
]


def normalize_proxy(p: str) -> str:
    p = p.strip()
    if not p:
        return ""
    if "://" not in p and len(p.split(":")) >= 2:
        return f"http://{p}"
    return p


async def fetch_source(client: httpx.AsyncClient, url: str) -> list[str]:
    try:
        resp = await client.get(url, timeout=15.0)
        resp.raise_for_status()
        lines = resp.text.splitlines()
        proxies = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#") and ":" in line:
                if " " in line:
                    line = line.split(" ")[0]
                norm = normalize_proxy(line)
                if norm:
                    proxies.append(norm)
        return proxies
    except Exception as exc:
        logger.error("fetch_fail", url=url, error=str(exc))
        return []


async def fetch_all_proxies() -> list[str]:
    async with httpx.AsyncClient() as client:
        tasks = [fetch_source(client, url) for url in SOURCES]
        results = await asyncio.gather(*tasks)

    all_proxies = []
    for r in results:
        all_proxies.extend(r)
    return list(dict.fromkeys(all_proxies))


async def validate_proxy(
    proxy: str,
    sem: asyncio.Semaphore,
    timeout: float,
) -> tuple[str, float, bool]:
    async with sem:
        start_time = time.monotonic()
        for endpoint in VALIDATION_ENDPOINTS:
            try:
                async with httpx.AsyncClient(proxy=proxy, verify=False) as client:  # nosec B501
                    resp = await client.get(endpoint, timeout=timeout)
                    resp.raise_for_status()
                elapsed = time.monotonic() - start_time
                logger.info("proxy_ok", proxy=proxy, time=f"{elapsed:.2f}s", endpoint=endpoint)
                return proxy, elapsed, True
            except Exception:
                continue
        logger.debug("proxy_fail_all_endpoints", proxy=proxy)
        return proxy, 0.0, False


async def detect_geo(proxy: str, timeout: float = 8.0) -> str | None:
    try:
        async with httpx.AsyncClient(proxy=proxy, verify=False, timeout=timeout) as client:  # nosec B501
            resp = await client.get("https://ipapi.co/json/")
            resp.raise_for_status()
            data = resp.json()
            return data.get("country_code", "").upper() or None
    except Exception:
        return None


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-proxies", type=int, default=100)
    parser.add_argument("--output", type=str, default="config/proxies.yaml")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--geo-detect", action="store_true", help="Detect proxy countries")
    parser.add_argument("--concurrency", type=int, default=100, help="Validation concurrency")
    args = parser.parse_args()

    logger.info("fetching_proxies")
    raw_proxies = await fetch_all_proxies()
    logger.info("fetch_complete", count=len(raw_proxies))

    sem = asyncio.Semaphore(args.concurrency)
    logger.info("validating_proxies")

    tasks = [validate_proxy(p, sem, args.timeout) for p in raw_proxies]
    results = await asyncio.gather(*tasks)

    valid_proxies = [(p, t) for p, t, ok in results if ok]
    valid_proxies.sort(key=lambda x: x[1])

    top_proxies = [p[0] for p in valid_proxies[: args.max_proxies]]

    geo_map: dict[str, str] = {}
    if args.geo_detect and top_proxies:
        logger.info("detecting_geo", count=len(top_proxies))
        geo_tasks = [detect_geo(p, timeout=args.timeout) for p in top_proxies]
        geo_results = await asyncio.gather(*geo_tasks)
        for proxy_url, country in zip(top_proxies, geo_results, strict=True):
            if country:
                geo_map[proxy_url] = country

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    now_iso = datetime.now(UTC).isoformat()
    yaml_content: dict[str, object] = {"proxies": top_proxies}
    if geo_map:
        yaml_content["geo"] = geo_map

    header = (
        "# Auto-generated by scripts/update_proxies.py — do not edit manually.\n"
        f"# Last updated: {now_iso}\n"
        f"# Validated: {len(valid_proxies)} of {len(raw_proxies)} tested\n"
    )

    with out_path.open("w", encoding="utf-8") as f:
        f.write(header)
        yaml.dump(yaml_content, f, default_flow_style=False, sort_keys=False)

    print(
        f"Tested {len(raw_proxies)}, OK {len(valid_proxies)}, written {len(top_proxies)} to {args.output}"
    )
    if geo_map:
        countries = {}
        for c in geo_map.values():
            countries[c] = countries.get(c, 0) + 1
        print(f"Geo: {dict(sorted(countries.items(), key=lambda x: -x[1]))}")


if __name__ == "__main__":
    asyncio.run(main())
