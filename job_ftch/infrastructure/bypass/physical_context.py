"""Physical context emulation with consistency validation.

Emulates complete physical context including geolocation, timezone,
device state, and network type. Validates consistency between
timezone, locale, IP geolocation, and device properties.

Anti-bot systems check physical context consistency. A user claiming
"America/New_York" timezone with a German IP exit node is immediately
flagged.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Timezone to country mapping for consistency checks
_TIMEZONE_COUNTRY_MAP: dict[str, str] = {
    "America/New_York": "US",
    "America/Chicago": "US",
    "America/Los_Angeles": "US",
    "America/Denver": "US",
    "America/Toronto": "CA",
    "America/Vancouver": "CA",
    "Europe/London": "GB",
    "Europe/Berlin": "DE",
    "Europe/Paris": "FR",
    "Europe/Amsterdam": "NL",
    "Europe/Moscow": "RU",
    "Asia/Tokyo": "JP",
    "Asia/Shanghai": "CN",
    "Asia/Dubai": "AE",
    "Australia/Sydney": "AU",
    "Pacific/Auckland": "NZ",
}

# Country to locale mapping
_COUNTRY_LOCALE_MAP: dict[str, str] = {
    "US": "en-US",
    "GB": "en-GB",
    "CA": "en-CA",
    "DE": "de-DE",
    "FR": "fr-FR",
    "NL": "nl-NL",
    "RU": "ru-RU",
    "JP": "ja-JP",
    "CN": "zh-CN",
    "AE": "ar-AE",
    "AU": "en-AU",
    "NZ": "en-NZ",
}

# Major cities with coordinates for geolocation
_CITIES: list[tuple[str, str, float, float, str]] = [
    # (city, country, lat, lon, timezone)
    ("New York", "US", 40.7128, -74.0060, "America/New_York"),
    ("Los Angeles", "US", 34.0522, -118.2437, "America/Los_Angeles"),
    ("Chicago", "US", 41.8781, -87.6298, "America/Chicago"),
    ("London", "GB", 51.5074, -0.1278, "Europe/London"),
    ("Berlin", "DE", 52.5200, 13.4050, "Europe/Berlin"),
    ("Paris", "FR", 48.8566, 2.3522, "Europe/Paris"),
    ("Amsterdam", "NL", 52.3676, 4.9041, "Europe/Amsterdam"),
    ("Moscow", "RU", 55.7558, 37.6173, "Europe/Moscow"),
    ("Tokyo", "JP", 35.6762, 139.6503, "Asia/Tokyo"),
    ("Shanghai", "CN", 31.2304, 121.4737, "Asia/Shanghai"),
    ("Dubai", "AE", 25.2048, 55.2708, "Asia/Dubai"),
    ("Sydney", "AU", -33.8688, 151.2093, "Australia/Sydney"),
    ("Toronto", "CA", 43.6532, -79.3832, "America/Toronto"),
]


@dataclass(frozen=True, slots=True)
class GeoLocation:
    """Geographic location with coordinates."""

    latitude: float
    longitude: float
    accuracy_meters: float = 100.0  # GPS accuracy
    city: str = ""
    country: str = ""


@dataclass(frozen=True, slots=True)
class DeviceState:
    """Device state information."""

    battery_level: float = 0.85  # 0.0 to 1.0
    battery_charging: bool = True
    battery_charging_time: int = 0  # Seconds until full
    battery_discharging_time: int = 0  # Seconds until empty
    device_memory_gb: int = 8
    cpu_cores: int = 8
    screen_width: int = 1920
    screen_height: int = 1080
    color_depth: int = 24
    pixel_ratio: float = 1.0


@dataclass(slots=True)
class PhysicalContext:
    """Complete physical context with consistency validation.

    Usage:
        ctx = PhysicalContext(
            location=GeoLocation(40.7128, -74.0060, city="New York", country="US"),
            timezone="America/New_York",
            locale="en-US",
            ip_country="US",
            device_state=DeviceState(),
            network_type="wifi",
        )
        assert ctx.is_consistent()
    """

    location: GeoLocation
    timezone: str
    locale: str
    ip_country: str
    device_state: DeviceState = field(default_factory=DeviceState)
    network_type: str = "wifi"  # "wifi", "4g", "5g", "ethernet"
    time_of_day: str = "day"  # "morning", "day", "evening", "night"

    def is_consistent(self) -> bool:
        """Check if physical context is internally consistent.

        Validates:
        - Timezone matches IP country
        - Locale matches IP country
        - Timezone matches location country
        """
        # Check timezone vs IP country
        expected_country = _TIMEZONE_COUNTRY_MAP.get(self.timezone)
        if expected_country and expected_country != self.ip_country:
            return False

        # Check locale vs IP country
        expected_locale_prefix = _COUNTRY_LOCALE_MAP.get(self.ip_country, "")
        if expected_locale_prefix and not self.locale.startswith(
            expected_locale_prefix.split("-")[0]
        ):
            return False

        # Check timezone vs location country
        if self.location.country and self.timezone:
            location_tz = _TIMEZONE_COUNTRY_MAP.get(self.timezone)
            if location_tz and location_tz != self.location.country:
                return False

        return True

    def get_consistency_report(self) -> dict[str, Any]:
        """Get detailed consistency report."""
        report: dict[str, Any] = {
            "is_consistent": self.is_consistent(),
            "checks": [],
        }

        # Timezone vs IP country
        expected_country = _TIMEZONE_COUNTRY_MAP.get(self.timezone)
        if expected_country:
            report["checks"].append(
                {
                    "check": "timezone_vs_ip_country",
                    "expected": expected_country,
                    "actual": self.ip_country,
                    "passed": expected_country == self.ip_country,
                }
            )

        # Locale vs IP country
        expected_locale_prefix = _COUNTRY_LOCALE_MAP.get(self.ip_country, "")
        if expected_locale_prefix:
            locale_matches = self.locale.startswith(expected_locale_prefix.split("-")[0])
            report["checks"].append(
                {
                    "check": "locale_vs_ip_country",
                    "expected_prefix": expected_locale_prefix.split("-")[0],
                    "actual": self.locale,
                    "passed": locale_matches,
                }
            )

        return report

    def is_active_hours(self) -> bool:
        """Check if current time is within active hours (8am-11pm)."""
        hour = datetime.now().hour
        return 8 <= hour <= 23

    def get_geolocation_js(self) -> str:
        """Generate JavaScript to spoof geolocation."""
        return f"""
            (() => {{
                const latitude = {self.location.latitude};
                const longitude = {self.location.longitude};
                const accuracy = {self.location.accuracy_meters};

                if (navigator.geolocation) {{
                    const origGetCurrentPosition = navigator.geolocation.getCurrentPosition;
                    navigator.geolocation.getCurrentPosition = function(success, error, options) {{
                        success({{
                            coords: {{
                                latitude: latitude,
                                longitude: longitude,
                                accuracy: accuracy,
                                altitude: null,
                                altitudeAccuracy: null,
                                heading: null,
                                speed: null,
                            }},
                            timestamp: Date.now(),
                        }});
                    }};
                }}
            }})();
        """


class PhysicalContextGenerator:
    """Generate consistent physical contexts.

    Usage:
        gen = PhysicalContextGenerator(seed=42)
        ctx = gen.generate(ip_country="US")
        assert ctx.is_consistent()
    """

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def generate(self, ip_country: str = "US") -> PhysicalContext:
        """Generate a consistent physical context for given IP country.

        Args:
            ip_country: ISO 3166-1 alpha-2 country code (e.g., "US", "GB")

        Returns:
            PhysicalContext with consistent timezone, locale, and location
        """
        # Find cities in the target country
        country_cities = [c for c in _CITIES if c[1] == ip_country]
        if not country_cities:
            # Fallback to US if country not found
            country_cities = [c for c in _CITIES if c[1] == "US"]

        city_name, country, lat, lon, timezone = self._rng.choice(country_cities)

        # Get locale for country
        locale = _COUNTRY_LOCALE_MAP.get(ip_country, "en-US")

        # Generate device state
        device_state = DeviceState(
            battery_level=round(self._rng.uniform(0.15, 1.0), 2),
            battery_charging=self._rng.random() < 0.7,
            device_memory_gb=self._rng.choice([4, 8, 16, 32]),
            cpu_cores=self._rng.choice([2, 4, 6, 8, 12, 16]),
            screen_width=self._rng.choice([1366, 1440, 1536, 1920, 2560]),
            screen_height=self._rng.choice([768, 900, 864, 1080, 1440]),
            color_depth=24,
            pixel_ratio=self._rng.choice([1.0, 1.25, 1.5, 2.0]),
        )

        # Determine time of day based on timezone
        time_of_day = self._get_time_of_day(timezone)

        return PhysicalContext(
            location=GeoLocation(
                latitude=lat,
                longitude=lon,
                accuracy_meters=self._rng.uniform(10, 500),
                city=city_name,
                country=country,
            ),
            timezone=timezone,
            locale=locale,
            ip_country=ip_country,
            device_state=device_state,
            network_type=self._rng.choice(["wifi", "wifi", "wifi", "4g", "5g"]),  # 60% wifi
            time_of_day=time_of_day,
        )

    def _get_time_of_day(self, timezone: str) -> str:
        """Determine time of day for given timezone."""
        # Simplified: just use random distribution
        # In production, would convert current UTC time to timezone
        return self._rng.choice(["morning", "day", "day", "evening", "night"])
