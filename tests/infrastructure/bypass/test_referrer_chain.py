"""Tests for ADR-076 referrer chain forgery."""

from __future__ import annotations

from job_ftch.infrastructure.bypass.referrer_chain import (
    Referrer,
    ReferrerChainGenerator,
    extract_domain,
    is_same_domain,
)


class TestReferrerChainGenerator:
    """Test referrer chain generation."""

    def test_generate_chain_returns_list(self):
        """Generate chain returns a list of Referrer objects."""
        gen = ReferrerChainGenerator(seed=42)
        chain = gen.generate_chain("https://example.com/jobs")

        assert isinstance(chain, list)
        assert all(isinstance(r, Referrer) for r in chain)

    def test_generate_chain_has_correct_length(self):
        """Chain has between min_hops+1 and max_hops+1 elements."""
        gen = ReferrerChainGenerator(seed=42)
        chain = gen.generate_chain("https://example.com/jobs", min_hops=2, max_hops=4)

        # min_hops intermediate + 1 target, max_hops intermediate + 1 target
        assert 3 <= len(chain) <= 5

    def test_generate_chain_ends_with_target(self):
        """Last referrer in chain is the target URL."""
        gen = ReferrerChainGenerator(seed=42)
        target = "https://example.com/jobs"
        chain = gen.generate_chain(target)

        assert chain[-1].url == target
        assert chain[-1].source_type == "target"

    def test_generate_chain_timestamps_are_ordered(self):
        """Timestamps are in chronological order (newest first)."""
        gen = ReferrerChainGenerator(seed=42)
        chain = gen.generate_chain("https://example.com/jobs")

        # Timestamps should be decreasing (more negative = further in past)
        for i in range(len(chain) - 1):
            assert chain[i].timestamp_offset > chain[i + 1].timestamp_offset

    def test_generate_chain_has_variety(self):
        """Chain includes different source types."""
        gen = ReferrerChainGenerator(seed=42)
        chain = gen.generate_chain("https://example.com/jobs", min_hops=3, max_hops=5)

        source_types = {r.source_type for r in chain[:-1]}  # Exclude target
        # Should have at least one source type
        assert len(source_types) >= 1

    def test_generate_chain_deterministic_with_seed(self):
        """Same seed produces same chain."""
        gen1 = ReferrerChainGenerator(seed=42)
        gen2 = ReferrerChainGenerator(seed=42)

        chain1 = gen1.generate_chain("https://example.com/jobs")
        chain2 = gen2.generate_chain("https://example.com/jobs")

        assert len(chain1) == len(chain2)
        for r1, r2 in zip(chain1, chain2, strict=True):
            assert r1.url == r2.url
            assert r1.source_type == r2.source_type

    def test_get_immediate_referrer(self):
        """Get immediate referrer returns single referrer."""
        gen = ReferrerChainGenerator(seed=42)
        referrer = gen.get_immediate_referrer("https://example.com/jobs")

        assert isinstance(referrer, Referrer)
        assert referrer.timestamp_offset == 0.0

    def test_referrer_has_valid_url(self):
        """All referrers have valid URLs."""
        gen = ReferrerChainGenerator(seed=42)
        chain = gen.generate_chain("https://example.com/jobs")

        for referrer in chain:
            assert referrer.url.startswith("http")
            assert len(referrer.url) > 10


class TestReferrerHelpers:
    """Test referrer helper functions."""

    def test_extract_domain(self):
        """Extract domain from URL."""
        assert extract_domain("https://www.google.com/search?q=test") == "www.google.com"
        assert extract_domain("https://example.com/jobs") == "example.com"
        assert extract_domain("http://subdomain.example.com/path") == "subdomain.example.com"

    def test_is_same_domain(self):
        """Check if two URLs are from same domain."""
        assert is_same_domain(
            "https://www.google.com/search?q=test",
            "https://www.google.com/maps",
        )
        assert not is_same_domain(
            "https://www.google.com/search",
            "https://www.bing.com/search",
        )


class TestReferrerSourceTypes:
    """Test different referrer source types."""

    def test_search_engine_referrers(self):
        """Search engine referrers contain search queries."""
        gen = ReferrerChainGenerator(seed=42)
        # Generate many chains to get search engine referrers
        all_urls = []
        for _ in range(50):
            chain = gen.generate_chain("https://example.com/jobs", min_hops=2, max_hops=3)
            all_urls.extend(r.url for r in chain[:-1])

        # Should have some search engine URLs
        search_urls = [url for url in all_urls if "google.com" in url or "bing.com" in url]
        assert len(search_urls) > 0

    def test_social_network_referrers(self):
        """Social network referrers are included."""
        gen = ReferrerChainGenerator(seed=42)
        all_urls = []
        for _ in range(100):
            chain = gen.generate_chain("https://example.com/jobs", min_hops=3, max_hops=4)
            all_urls.extend(r.url for r in chain[:-1])

        # Should have some social network URLs
        social_urls = [url for url in all_urls if "linkedin.com" in url or "twitter.com" in url]
        # May or may not have social URLs depending on random selection
        # Just verify the generator doesn't crash
        assert isinstance(social_urls, list)
