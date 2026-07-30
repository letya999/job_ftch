"""Tests for ADR-076 physical context emulation."""

from __future__ import annotations

from job_ftch.infrastructure.bypass.physical_context import (
    DeviceState,
    GeoLocation,
    PhysicalContext,
    PhysicalContextGenerator,
)


class TestPhysicalContext:
    """Test physical context consistency validation."""

    def test_consistent_context_passes(self):
        """Consistent physical context passes validation."""
        ctx = PhysicalContext(
            location=GeoLocation(40.7128, -74.0060, city="New York", country="US"),
            timezone="America/New_York",
            locale="en-US",
            ip_country="US",
        )
        assert ctx.is_consistent()

    def test_inconsistent_timezone_ip_fails(self):
        """Inconsistent timezone and IP country fails validation."""
        ctx = PhysicalContext(
            location=GeoLocation(40.7128, -74.0060, city="New York", country="US"),
            timezone="America/New_York",
            locale="en-US",
            ip_country="DE",  # Germany
        )
        assert not ctx.is_consistent()

    def test_inconsistent_locale_ip_fails(self):
        """Inconsistent locale and IP country fails validation."""
        ctx = PhysicalContext(
            location=GeoLocation(51.5074, -0.1278, city="London", country="GB"),
            timezone="Europe/London",
            locale="de-DE",  # German locale for UK
            ip_country="GB",
        )
        assert not ctx.is_consistent()

    def test_get_consistency_report(self):
        """Get detailed consistency report."""
        ctx = PhysicalContext(
            location=GeoLocation(40.7128, -74.0060, city="New York", country="US"),
            timezone="America/New_York",
            locale="en-US",
            ip_country="US",
        )
        report = ctx.get_consistency_report()

        assert "is_consistent" in report
        assert "checks" in report
        assert report["is_consistent"] is True

    def test_is_active_hours(self):
        """Check active hours (8am-11pm)."""
        ctx = PhysicalContext(
            location=GeoLocation(40.7128, -74.0060),
            timezone="America/New_York",
            locale="en-US",
            ip_country="US",
        )
        # Just verify it returns a boolean
        assert isinstance(ctx.is_active_hours(), bool)

    def test_get_geolocation_js(self):
        """Generate geolocation spoofing JavaScript."""
        ctx = PhysicalContext(
            location=GeoLocation(40.7128, -74.0060),
            timezone="America/New_York",
            locale="en-US",
            ip_country="US",
        )
        js = ctx.get_geolocation_js()

        assert "40.7128" in js
        assert "-74.006" in js
        assert "navigator.geolocation" in js


class TestPhysicalContextGenerator:
    """Test physical context generation."""

    def test_generate_returns_context(self):
        """Generate returns a PhysicalContext."""
        gen = PhysicalContextGenerator(seed=42)
        ctx = gen.generate(ip_country="US")

        assert isinstance(ctx, PhysicalContext)
        assert isinstance(ctx.location, GeoLocation)
        assert isinstance(ctx.device_state, DeviceState)

    def test_generate_consistent_context(self):
        """Generate returns consistent context."""
        gen = PhysicalContextGenerator(seed=42)
        ctx = gen.generate(ip_country="US")

        assert ctx.is_consistent()

    def test_generate_for_different_countries(self):
        """Generate works for different countries."""
        gen = PhysicalContextGenerator(seed=42)

        for country in ["US", "GB", "DE", "FR"]:
            ctx = gen.generate(ip_country=country)
            assert ctx.is_consistent()
            assert ctx.ip_country == country

    def test_generate_deterministic_with_seed(self):
        """Same seed produces same context."""
        gen1 = PhysicalContextGenerator(seed=42)
        gen2 = PhysicalContextGenerator(seed=42)

        ctx1 = gen1.generate(ip_country="US")
        ctx2 = gen2.generate(ip_country="US")

        assert ctx1.location.latitude == ctx2.location.latitude
        assert ctx1.location.longitude == ctx2.location.longitude
        assert ctx1.timezone == ctx2.timezone
        assert ctx1.locale == ctx2.locale

    def test_generate_has_valid_device_state(self):
        """Generated context has valid device state."""
        gen = PhysicalContextGenerator(seed=42)
        ctx = gen.generate(ip_country="US")

        assert 0.0 <= ctx.device_state.battery_level <= 1.0
        assert ctx.device_state.device_memory_gb in [4, 8, 16, 32]
        assert ctx.device_state.cpu_cores in [2, 4, 6, 8, 12, 16]
        assert ctx.device_state.screen_width > 0
        assert ctx.device_state.screen_height > 0

    def test_generate_has_valid_network_type(self):
        """Generated context has valid network type."""
        gen = PhysicalContextGenerator(seed=42)
        ctx = gen.generate(ip_country="US")

        assert ctx.network_type in ["wifi", "4g", "5g", "ethernet"]

    def test_generate_has_valid_time_of_day(self):
        """Generated context has valid time of day."""
        gen = PhysicalContextGenerator(seed=42)
        ctx = gen.generate(ip_country="US")

        assert ctx.time_of_day in ["morning", "day", "evening", "night"]
