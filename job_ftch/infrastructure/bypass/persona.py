"""Browser persona pool for consistent anti-fingerprint rotation.

Each persona is a coherent identity: UA + viewport + locale + timezone +
canvas seed + WebGL renderer. Personas are sticky per-domain per-session
to avoid fingerprint flip-flopping within one crawl.

The pool is pre-generated at import time from a fixed seed set (20 personas)
covering Chrome 120-131, Safari 17, and Firefox 125 across Windows/macOS/Linux.
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, replace
from typing import Any

_PERSONA_SEED = 42


@dataclass(frozen=True, slots=True)
class BrowserPersona:
    """A coherent browser identity for anti-fingerprinting."""

    name: str
    ua: str
    sec_ch_ua: str
    sec_ch_ua_platform: str
    viewport_width: int
    viewport_height: int
    locale: str
    timezone: str
    navigator_platform: str
    webgl_renderer: str
    canvas_seed: int
    browser_family: str
    browser_version: str
    screen_width: int = 1920
    screen_height: int = 1080
    battery_charging: bool = True
    battery_level: float = 0.85
    proxy_country: str = ""
    hardware_concurrency: int = 8
    device_memory: int = 8
    navigator_vendor: str = "Google Inc."
    navigator_oscpu: str = "Windows NT 10.0; Win64; x64"
    font_spacing_seed: int = 42
    font_list: list[str] | None = None
    speech_voices: list[str] | None = None
    platform_version: str = "10.0.0"
    architecture: str = "x86"
    bitness: str = "64"

    def context_kwargs(self) -> dict[str, Any]:
        return {
            "user_agent": self.ua,
            "viewport": {"width": self.viewport_width, "height": self.viewport_height},
            "locale": self.locale,
            "timezone_id": self.timezone,
        }

    def http_headers(self) -> dict[str, str]:
        # Note: callers that need curl_cffi-impersonation header ORDER should
        # use http_headers_ordered() instead — this dict is kept for
        # backward compatibility with non-curl clients and playwright kwargs.
        return dict(self.http_headers_ordered())

    def http_headers_ordered(self) -> list[tuple[str, str]]:
        """Return headers in curl_cffi profile order for the persona's family.

        Order follows the Chrome desktop profile (matches curl_cffi's
        ``chrome131``/``chrome124`` impersonation), Firefox and Safari use
        their own canonical orders so JA4H stays coherent with impersonation.
        Duplicate keys are avoided — the curl_cffi profile already injects
        ``Host``, ``Connection``, ``Accept-Encoding``, etc. via impersonation;
        callers merge the returned list AFTER letting impersonation seed
        defaults so that this list only overrides per-persona values.
        """
        accept_html = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        )
        if self.browser_family == "chromium":
            return [
                ("sec-ch-ua", self.sec_ch_ua),
                ("sec-ch-ua-mobile", "?0"),
                ("sec-ch-ua-platform", self.sec_ch_ua_platform),
                ("sec-ch-ua-platform-version", f'"{self.platform_version}"'),
                ("sec-ch-ua-arch", f'"{self.architecture}"'),
                ("sec-ch-ua-bitness", f'"{self.bitness}"'),
                ("sec-ch-ua-model", '""'),
                ("Upgrade-Insecure-Requests", "1"),
                ("User-Agent", self.ua),
                ("Accept", accept_html),
                ("Accept-Language", f"{self.locale},en;q=0.9"),
            ]
        if self.browser_family == "safari":
            return [
                ("User-Agent", self.ua),
                ("Accept", accept_html),
                ("Accept-Language", f"{self.locale},en;q=0.9"),
            ]
        # Firefox: no Client Hints, simpler Accept.
        return [
            ("User-Agent", self.ua),
            ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
            ("Accept-Language", f"{self.locale},en;q=0.9"),
        ]


_VIEWPORTS: list[tuple[int, int]] = [
    (1920, 1080),
    (1440, 900),
    (1536, 864),
    (1366, 768),
    (1280, 720),
    (1600, 900),
    (2560, 1440),
    (1680, 1050),
]

_LOCALES: list[tuple[str, str]] = [
    ("en-US", "America/New_York"),
    ("en-US", "America/Chicago"),
    ("en-US", "America/Los_Angeles"),
    ("en-GB", "Europe/London"),
    ("de-DE", "Europe/Berlin"),
    ("fr-FR", "Europe/Paris"),
    ("nl-NL", "Europe/Amsterdam"),
    ("ru-RU", "Europe/Moscow"),
]

_BROWSERS: list[dict[str, Any]] = [
    {
        "family": "chromium",
        "version": "131",
        "ua_tpl": "Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "ch_ua": '"Chromium";v="131", "Not?A_Brand";v="24", "Google Chrome";v="131"',
        "nav_win": "Win32",
        "nav_mac": "MacIntel",
        "nav_linux": "Linux x86_64",
    },
    {
        "family": "chromium",
        "version": "124",
        "ua_tpl": "Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "ch_ua": '"Chromium";v="124", "Not_A Brand";v="8", "Google Chrome";v="124"',
        "nav_win": "Win32",
        "nav_mac": "MacIntel",
        "nav_linux": "Linux x86_64",
    },
    {
        "family": "safari",
        "supported_os": ("macos",),
        "version": "safari17",
        "ua_tpl": "Mozilla/5.0 ({platform}) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        "ch_ua": "",
        "nav_win": "MacIntel",
        "nav_mac": "MacIntel",
        "nav_linux": "MacIntel",
    },
    {
        "family": "firefox",
        "version": "ff125",
        "ua_tpl": "Mozilla/5.0 ({platform}; rv:125.0) Gecko/20100101 Firefox/125.0",
        "ch_ua": "",
        "nav_win": "Win32",
        "nav_mac": "MacIntel",
        "nav_linux": "Linux x86_64",
    },
]

_WEBGL_RENDERERS: list[str] = [
    "ANGLE (Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0)",
    "Apple GPU",
    "Mesa Intel(R) UHD Graphics 630 (CFL GT2)",
]

_PLATFORM_STRINGS: dict[str, list[str]] = {
    "windows": ["Windows NT 10.0; Win64; x64", "Windows NT 10.0; WOW64"],
    "macos": ["Macintosh; Intel Mac OS X 10_15_7", "Macintosh; Intel Mac OS X 14_3"],
    "linux": ["X11; Linux x86_64", "X11; Ubuntu; Linux x86_64"],
}

_PROXY_COUNTRY_LOCALES: dict[str, list[tuple[str, str]]] = {
    "US": [
        ("en-US", "America/New_York"),
        ("en-US", "America/Chicago"),
        ("en-US", "America/Los_Angeles"),
    ],
    "GB": [("en-GB", "Europe/London")],
    "DE": [("de-DE", "Europe/Berlin")],
    "FR": [("fr-FR", "Europe/Paris")],
    "NL": [("nl-NL", "Europe/Amsterdam")],
    "RU": [("ru-RU", "Europe/Moscow")],
    "CA": [("en-US", "America/Toronto")],
    "AU": [("en-US", "Australia/Sydney")],
}

_NAVIGATOR_VENDORS: dict[str, str] = {
    "chromium": "Google Inc.",
    "firefox": "",
    "safari": "Apple Computer, Inc.",
}

_OSCPU_MAP: dict[str, str] = {
    "windows": "Windows NT 10.0; Win64; x64",
    "macos": "Intel Mac OS X 10.15",
    "linux": "Linux x86_64",
}

_FONT_LISTS: dict[str, list[str]] = {
    "windows": [
        "Arial",
        "Calibri",
        "Cambria",
        "Candara",
        "Consolas",
        "Constantia",
        "Corbel",
        "Courier New",
        "Georgia",
        "Helvetica",
        "Segoe UI",
        "Tahoma",
        "Times New Roman",
        "Trebuchet MS",
        "Verdana",
    ],
    "macos": [
        "Arial",
        "Courier New",
        "Geneva",
        "Georgia",
        "Helvetica",
        "Helvetica Neue",
        "Lucida Grande",
        "Menlo",
        "Monaco",
        "San Francisco",
        "Times New Roman",
        "Verdana",
    ],
    "linux": [
        "Arial",
        "Courier New",
        "DejaVu Sans",
        "DejaVu Serif",
        "Droid Sans",
        "Droid Serif",
        "FreeMono",
        "FreeSans",
        "FreeSerif",
        "Georgia",
        "Helvetica",
        "Liberation Mono",
        "Liberation Sans",
        "Liberation Serif",
        "Noto Sans",
        "Times New Roman",
        "Ubuntu",
        "Verdana",
    ],
}

_SPEECH_VOICE_SETS: dict[str, list[str]] = {
    "windows": [
        "Microsoft David Desktop - English (United States)",
        "Microsoft Zira Desktop - English (United States)",
        "Microsoft Mark - English (United States)",
    ],
    "macos": [
        "Alex",
        "Allison",
        "Arthur",
        "Daniel",
        "Fiona",
        "Fred",
        "Karen",
        "Moira",
        "Samantha",
        "Tessa",
        "Victoria",
    ],
    "linux": [
        "English (America) espeak-ng",
        "English (Great Britain) espeak-ng",
        "default",
    ],
}

_HARDWARE_CONCURRENCY_OPTIONS: list[int] = [2, 4, 6, 8, 12, 16]
_DEVICE_MEMORY_OPTIONS: list[int] = [2, 4, 8, 16]

_PLATFORM_VERSIONS: dict[str, list[str]] = {
    "windows": ["10.0.0", "15.0.0", "16.0.0"],
    "macos": ["14.5.0", "14.6.1", "15.0.0", "15.1.0"],
    "linux": ["6.5.0", "6.8.0", "6.9.0"],
}

_ARCHITECTURE_MAP: dict[str, str] = {
    "windows": "x86",
    "macos": "arm",
    "linux": "x86",
}

_BITNESS_MAP: dict[str, str] = {
    "windows": "64",
    "macos": "64",
    "linux": "64",
}


def _generate_personas(count: int = 20) -> list[BrowserPersona]:
    rng = random.Random(_PERSONA_SEED)
    personas: list[BrowserPersona] = []

    for i in range(count):
        browser = rng.choice(_BROWSERS)
        supported_os = browser.get("supported_os", ("windows", "macos", "linux"))
        os_key = rng.choice(supported_os)
        platform_str = rng.choice(_PLATFORM_STRINGS[os_key])
        viewport = rng.choice(_VIEWPORTS)
        locale, tz = rng.choice(_LOCALES)
        compatible_renderers = {
            "windows": [value for value in _WEBGL_RENDERERS if value.startswith("ANGLE")],
            "macos": ["Apple GPU"],
            "linux": [value for value in _WEBGL_RENDERERS if value.startswith("Mesa")],
        }
        renderer = rng.choice(compatible_renderers[os_key])

        nav_key = f"nav_{os_key.replace('macos', 'mac')}"
        nav_platform = browser.get(nav_key, "Win32")
        ch_platform_map = {"windows": '"Windows"', "macos": '"macOS"', "linux": '"Linux"'}

        ua = browser["ua_tpl"].format(platform=platform_str)

        screen_width = viewport[0]
        screen_height = viewport[1] + rng.choice([0, 40, 80])
        battery_charging = rng.random() < 0.7
        battery_level = round(rng.uniform(0.15, 1.0), 2)

        hardware_concurrency = rng.choice(_HARDWARE_CONCURRENCY_OPTIONS)
        device_memory = rng.choice(_DEVICE_MEMORY_OPTIONS)
        navigator_vendor = _NAVIGATOR_VENDORS.get(browser["family"], "Google Inc.")
        navigator_oscpu = _OSCPU_MAP.get(os_key, "Windows NT 10.0; Win64; x64")
        font_spacing_seed = rng.randint(1, 999999)
        font_list = _FONT_LISTS.get(os_key, _FONT_LISTS["windows"])
        speech_voices = _SPEECH_VOICE_SETS.get(os_key, _SPEECH_VOICE_SETS["windows"])
        plat_version = rng.choice(_PLATFORM_VERSIONS.get(os_key, ["10.0.0"]))
        arch = _ARCHITECTURE_MAP.get(os_key, "x86")
        bitness = _BITNESS_MAP.get(os_key, "64")

        personas.append(
            BrowserPersona(
                name=f"persona_{i:02d}_{browser['version']}_{os_key[:3]}",
                ua=ua,
                sec_ch_ua=browser["ch_ua"],
                sec_ch_ua_platform=ch_platform_map[os_key],
                viewport_width=viewport[0],
                viewport_height=viewport[1],
                locale=locale,
                timezone=tz,
                navigator_platform=nav_platform,
                webgl_renderer=renderer,
                canvas_seed=rng.randint(1000, 9999),
                browser_family=browser["family"],
                browser_version=browser["version"],
                screen_width=screen_width,
                screen_height=screen_height,
                battery_charging=battery_charging,
                battery_level=battery_level,
                hardware_concurrency=hardware_concurrency,
                device_memory=device_memory,
                navigator_vendor=navigator_vendor,
                navigator_oscpu=navigator_oscpu,
                font_spacing_seed=font_spacing_seed,
                font_list=font_list,
                speech_voices=speech_voices,
                platform_version=plat_version,
                architecture=arch,
                bitness=bitness,
            )
        )

    return personas


PERSONA_POOL: list[BrowserPersona] = _generate_personas(20)

_domain_persona_map: dict[tuple[str, str | None, str], BrowserPersona] = {}


def select_persona(
    domain: str,
    browser_family: str | None = None,
    *,
    proxy_country: str = "",
) -> BrowserPersona:
    """Select a sticky persona for a domain (consistent within session).

    If proxy_country is provided (ISO 3166-1 alpha-2, e.g. 'US', 'DE'),
    the persona's locale and timezone will be aligned to match the proxy
    exit IP, avoiding the bot signal of mismatched geo.
    """
    normalized_family = {
        "chromium_cdp": "chromium",
        "chromium_patched": "chromium",
    }.get(browser_family or "", browser_family)
    key = (domain, normalized_family, proxy_country.upper())
    if key in _domain_persona_map:
        return _domain_persona_map[key]
    candidates = [
        persona
        for persona in PERSONA_POOL
        if normalized_family is None or persona.browser_family == normalized_family
    ]
    if not candidates:
        candidates = PERSONA_POOL
    idx = int(
        hashlib.sha256(f"{domain}:{normalized_family}:{proxy_country}".encode()).hexdigest(),
        16,
    ) % len(candidates)
    persona = candidates[idx]
    if proxy_country:
        aligned_locales = _PROXY_COUNTRY_LOCALES.get(proxy_country.upper())
        if aligned_locales:
            from dataclasses import replace

            locale, tz = aligned_locales[idx % len(aligned_locales)]
            persona = replace(
                persona, locale=locale, timezone=tz, proxy_country=proxy_country.upper()
            )
    _domain_persona_map[key] = persona
    return persona


def reset_personas() -> None:
    """Clear domain-persona bindings (call between sessions)."""
    _domain_persona_map.clear()


def align_persona_version(
    persona: BrowserPersona,
    browser_family: str,
    reported_version: str,
) -> BrowserPersona:
    """Align UA/client hints to a browser's runtime-reported major version."""
    normalized_family = {
        "chromium_cdp": "chromium",
        "chromium_patched": "chromium",
    }.get(browser_family, browser_family)
    match = re.search(r"\d+", reported_version)
    if match is None or normalized_family != persona.browser_family:
        return persona
    major = match.group(0)
    if normalized_family == "chromium":
        ua = re.sub(r"Chrome/\d+", f"Chrome/{major}", persona.ua)
        client_hints = re.sub(
            r'((?:Chromium|Google Chrome)";v=")\d+',
            rf"\g<1>{major}",
            persona.sec_ch_ua,
        )
    elif normalized_family == "firefox":
        ua = re.sub(r"rv:\d+", f"rv:{major}", persona.ua)
        ua = re.sub(r"Firefox/\d+", f"Firefox/{major}", ua)
        client_hints = ""
    else:
        return persona
    return replace(
        persona,
        ua=ua,
        sec_ch_ua=client_hints,
        browser_version=major,
    )
