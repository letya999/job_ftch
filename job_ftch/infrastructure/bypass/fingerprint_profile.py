"""Single source of truth for TLS/HTTP/JS fingerprint alignment.

All bypass tiers (curl_cffi, nodriver, stealth_browser, camoufox, cloak)
consume the same FingerprintProfile to ensure mathematical consistency
between TLS fingerprint (JA3/JA4), HTTP/2 headers (Sec-CH-UA), and
JS environment (navigator, WebGL).

Anti-detection is about internal consistency — the TLS handshake must
report the same browser version as the User-Agent string and the
Sec-CH-UA Client Hints headers.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


@dataclass(frozen=True, slots=True)
class FingerprintProfile:
    """Aligned TLS + HTTP/2 + JS environment profile."""

    curl_impersonate: str
    ua: str
    sec_ch_ua: str
    sec_ch_ua_mobile: str
    sec_ch_ua_platform: str
    accept_language: str
    navigator_platform: str
    browser_family: str
    browser_version: str
    viewport_width: int = 1440
    viewport_height: int = 900
    timezone: str = "America/New_York"
    # High-entropy Client Hints (Wave 2.4). Empty strings => omit the header
    # so we don't advertise entropy we cannot back with JS userAgentData.
    platform_version: str = ""
    architecture: str = ""
    bitness: str = ""
    model: str = ""

    @classmethod
    def from_persona(cls, persona: Any) -> FingerprintProfile:
        """Derive a coherent FingerprintProfile from a BrowserPersona."""
        ua = persona.ua
        sec_ch_ua = persona.sec_ch_ua
        sec_ch_ua_platform = persona.sec_ch_ua_platform
        locale = persona.locale
        timezone = persona.timezone

        if "Chrome/" in ua:
            impersonate = "chrome"
        elif "Safari/" in ua and "Chrome/" not in ua:
            impersonate = "safari17_5"
        elif "Firefox/" in ua:
            impersonate = "firefox125"
        else:
            impersonate = "chrome"

        return cls(
            curl_impersonate=impersonate,
            ua=ua,
            sec_ch_ua=sec_ch_ua,
            sec_ch_ua_mobile="?0",
            sec_ch_ua_platform=sec_ch_ua_platform,
            accept_language=f"{locale},en;q=0.9",
            navigator_platform=persona.navigator_platform,
            browser_family=persona.browser_family,
            browser_version=persona.browser_version,
            viewport_width=persona.viewport_width,
            viewport_height=persona.viewport_height,
            timezone=timezone,
            platform_version=getattr(persona, "platform_version", ""),
            architecture=getattr(persona, "architecture", ""),
            bitness=getattr(persona, "bitness", ""),
            model="",  # personas are desktop; Chrome desktop sends model="\"\""
        )

    @property
    def browser_args(self) -> list[str]:
        return [
            f"--user-agent={self.ua}",
            f"--lang={self.accept_language.split(',')[0]}",
        ]

    def http_headers(self) -> dict[str, str]:
        """Dict view for clients that merge into existing header maps.

        Order is preserved by Python dict since 3.7; see http_headers_ordered
        for the canonical list used by curl_cffi-aware callers.
        """
        return dict(self.http_headers_ordered())

    def http_headers_ordered(self) -> list[tuple[str, str]]:
        """Headers in curl_cffi-impersonation profile order.

        Curl impersonation seeds Host/Connection/Accept-Encoding and the
        TLS/H2 frame layout; callers merge this list AFTER impersonation
        so the persona-specific values override defaults while the order
        follows the real Chrome desktop request shape (matches JA4H).
        """
        accept_html = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        )
        if self.browser_family == "chromium":
            headers: list[tuple[str, str]] = [
                ("sec-ch-ua", self.sec_ch_ua),
                ("sec-ch-ua-mobile", self.sec_ch_ua_mobile),
                ("sec-ch-ua-platform", self.sec_ch_ua_platform),
            ]
            if self.platform_version:
                headers.append(("sec-ch-ua-platform-version", f'"{self.platform_version}"'))
            if self.architecture:
                headers.append(("sec-ch-ua-arch", f'"{self.architecture}"'))
            if self.bitness:
                headers.append(("sec-ch-ua-bitness", f'"{self.bitness}"'))
            # Chrome desktop always sends sec-ch-ua-model="" (missing for
            # desktop = small but real entropy signal; advertise empty).
            headers.append(("sec-ch-ua-model", ""))
            headers.extend(
                [
                    ("Upgrade-Insecure-Requests", "1"),
                    ("User-Agent", self.ua),
                    ("Accept", accept_html),
                    ("Accept-Language", self.accept_language),
                ]
            )
            return headers
        return [
            ("User-Agent", self.ua),
            ("Accept", accept_html),
            ("Accept-Language", self.accept_language),
        ]

    def context_kwargs(self) -> dict[str, Any]:
        return {
            "user_agent": self.ua,
            "viewport": {"width": self.viewport_width, "height": self.viewport_height},
            "locale": self.accept_language.split(",")[0],
            "timezone_id": self.timezone,
        }


_CHROME_MAJOR_RE = re.compile(r"Chrome/(\d+)")
_KNOWN_IMPERSONATIONS: tuple[tuple[str, int], ...] = (
    ("chrome131", 131),
    ("chrome124", 124),
    ("chrome120", 120),
    ("safari17_5", 17),
    ("safari15_3", 15),
    ("firefox125", 125),
)

_PLATFORM_BY_OS: dict[str, tuple[str, str, str, str]] = {
    "windows": ('"Windows"', "Win32", "en-US,en;q=0.9,ru;q=0.8", "America/New_York"),
    "darwin": ('"macOS"', "MacIntel", "en-US,en;q=0.9", "America/Los_Angeles"),
    "linux": ('"Linux"', "Linux x86_64", "en-US,en;q=0.9", "UTC"),
}


def _detect_chrome_major() -> int | None:
    exe = os.environ.get("JOB_FTCH_NODRIVER_BROWSER_EXECUTABLE_PATH", "").strip()
    if not exe:
        exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "").strip()
    if not exe:
        return None
    try:
        out = subprocess.check_output([exe, "--version"], stderr=subprocess.DEVNULL, timeout=3)
        m = _CHROME_MAJOR_RE.search(out.decode("utf-8", "ignore"))
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _pick_impersonate(major: int | None) -> tuple[str, int]:
    if major is None:
        return _KNOWN_IMPERSONATIONS[0]
    compat = [(name, ver) for name, ver in _KNOWN_IMPERSONATIONS if ver <= major]
    return compat[0] if compat else _KNOWN_IMPERSONATIONS[0]


@lru_cache(maxsize=4)
def get_profile(os_hint: str = "") -> FingerprintProfile:
    """Return the canonical fingerprint profile.

    Detects the system Chrome version and OS to generate consistent
    TLS impersonation, User-Agent, Client Hints, and navigator properties.
    """
    platsys = os_hint or platform.system().lower()
    plat_key = next((k for k in _PLATFORM_BY_OS if k in platsys), "windows")
    sec_ch_ua_platform, navigator_platform, lang, tz = _PLATFORM_BY_OS[plat_key]

    major = _detect_chrome_major()
    impersonate, effective_major = _pick_impersonate(major)

    ua = (
        f"Mozilla/5.0 ({navigator_platform}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{effective_major}.0.0.0 Safari/537.36"
    )

    greased = (
        f'"Chromium";v="{effective_major}", '
        f'"Not?A_Brand";v="24", '
        f'"Google Chrome";v="{effective_major}"'
    )

    return FingerprintProfile(
        curl_impersonate=impersonate,
        ua=ua,
        sec_ch_ua=greased,
        sec_ch_ua_mobile="?0",
        sec_ch_ua_platform=sec_ch_ua_platform,
        accept_language=lang,
        navigator_platform=navigator_platform,
        browser_family="chromium",
        browser_version=str(effective_major),
        timezone=tz,
    )


def patch_curl_bypass(bypass: Any) -> None:
    """Align a CurlBypass instance with the canonical profile.

    Only overrides when impersonate is still the default 'chrome' (auto).
    Explicit config values (e.g. 'safari15_3') are preserved.
    """
    if getattr(bypass, "impersonate", "chrome") != "chrome":
        return
    profile = get_profile()
    bypass.impersonate = profile.curl_impersonate


def patch_nodriver_bypass(bypass: Any) -> None:
    """Align a NodriverBypass instance with the canonical profile."""
    profile = get_profile()
    bypass._browser_args = list(bypass._browser_args or []) + profile.browser_args
    if not bypass._lang:
        bypass._lang = profile.accept_language.split(",")[0]


def patch_stealth_browser_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Inject FingerprintProfile headers into Playwright launch/context kwargs."""
    profile = get_profile()
    ctx = kwargs.setdefault("context_kwargs", {})
    ctx.setdefault("user_agent", profile.ua)
    ctx.setdefault("locale", profile.accept_language.split(",")[0])
    ctx.setdefault("timezone_id", profile.timezone)
    return kwargs


def merge_headers(base: dict[str, str] | None = None) -> dict[str, str]:
    """Merge FingerprintProfile.http_headers() with optional base headers."""
    merged = dict(get_profile().http_headers_ordered())
    if base:
        merged.update(base)
    return merged


def merge_headers_ordered(
    base: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    """Merge persona headers with base list, preserving profile order.

    Headers from the profile come first in their canonical order; any header
    from ``base`` that is not already present is appended afterwards. Used by
    curl_cffi callers where insertion order affects JA4H.
    """
    profile_headers = get_profile().http_headers_ordered()
    seen = {name.lower() for name, _ in profile_headers}
    merged = list(profile_headers)
    if base:
        for name, value in base:
            if name.lower() not in seen:
                merged.append((name, value))
                seen.add(name.lower())
    return merged
