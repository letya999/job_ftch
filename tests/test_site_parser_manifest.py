from __future__ import annotations

import pytest

from job_ftch.application import registry
from job_ftch.application.site_parser_manifest import (
    clear_site_parser_manifest_cache,
    load_site_parser_manifest,
)
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_defaults import apply_runtime_defaults
from job_ftch.infrastructure.sources.site_parsers.yandex import YandexJobsParser


@pytest.fixture(autouse=True)
def _reset_manifest_cache() -> None:
    clear_site_parser_manifest_cache()
    yield
    clear_site_parser_manifest_cache()


def test_site_parser_manifest_rejects_malformed_entry(tmp_path) -> None:
    manifest_path = tmp_path / "site_parsers.yaml"
    manifest_path.write_text(
        "parsers:\n  - name: broken\n    has_custom_parse: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid site parser manifest"):
        load_site_parser_manifest(manifest_path)


def test_resolve_site_parser_prefers_more_specific_pattern() -> None:
    unique = "manifest_sort_specificity"

    @registry.register_site_parser(
        f"{unique}_short",
        domain_pattern=r"example\.com/jobs",
    )
    class _ShortParser:
        has_custom_parse = False
        domain_pattern = r"example\.com/jobs"

        def runtime_defaults(self, url: str) -> object | None:
            del url
            return None

        def parser_kind(self, url: str) -> str | None:
            del url
            return None

    @registry.register_site_parser(
        f"{unique}_long",
        domain_pattern=r"sub\.example\.com/jobs",
    )
    class _LongParser:
        has_custom_parse = False
        domain_pattern = r"sub\.example\.com/jobs"

        def runtime_defaults(self, url: str) -> object | None:
            del url
            return None

        def parser_kind(self, url: str) -> str | None:
            del url
            return None

    resolved = registry.resolve_site_parser("https://sub.example.com/jobs")

    assert resolved is not None
    assert type(resolved).__name__ == "_LongParser"


def test_yaml_manifest_overrides_runtime_defaults_and_yandex_browser_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "site_parsers.yaml"
    manifest_path.write_text(
        "parsers:\n"
        "  - name: tbank_career\n"
        "    domain_pattern: '^https?://(?:www\\.)?tbank\\.ru/career'\n"
        "    has_custom_parse: true\n"
        "    supports_discover: true\n"
        "    url_filter: 'custom\\.tbank/detail/.+'\n"
        "    expand_links:\n"
        "      - 'custom-expand'\n"
        "    extra:\n"
        "      monitor: api_sniffer\n"
        "      bypass_capability: cloudflare_challenge\n"
        "    limit: 7\n"
        "  - name: yandex_jobs\n"
        "    domain_pattern: 'yandex\\.ru/jobs'\n"
        "    has_custom_parse: true\n"
        "    supports_discover: false\n"
        "    api_path: '/custom/api'\n"
        "    limit: 7\n"
        "    browser:\n"
        "      scroll_loops: 9\n"
        "      scroll_pause_ms: 250\n"
        "      scroll_px: 777\n"
        "      stale_rounds: 2\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JOB_FTCH_SITE_PARSERS_MANIFEST_PATH", str(manifest_path))
    clear_site_parser_manifest_cache()

    spec = CareerSiteSpec(
        type="career_site",
        url="https://www.tbank.ru/career/",
        monitor="dom",
        monitor_config={},
    )

    updated = apply_runtime_defaults(spec)
    parser = registry.resolve_site_parser("https://yandex.ru/jobs/")

    assert updated.url_filter == r"custom\.tbank/detail/.+"
    assert updated.monitor_config["expand_links"] == ["custom-expand"]
    assert updated.monitor_config["monitor"] == "api_sniffer"
    assert updated.monitor_config["bypass_capability"] == "cloudflare_challenge"
    assert isinstance(parser, YandexJobsParser)
    assert parser._api_path() == "/custom/api"
    assert parser._limit(None, 50) == 7
    assert parser._browser_scroll_config() == (9, 0.25, 777, 2)


def test_missing_manifest_entry_falls_back_to_decorator_defaults(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "site_parsers.yaml"
    manifest_path.write_text("parsers: []\n", encoding="utf-8")
    monkeypatch.setenv("JOB_FTCH_SITE_PARSERS_MANIFEST_PATH", str(manifest_path))
    clear_site_parser_manifest_cache()

    spec = CareerSiteSpec(
        type="career_site",
        url="https://www.tbank.ru/career/",
        monitor="dom",
        monitor_config={},
    )

    updated = apply_runtime_defaults(spec)

    assert updated.url_filter == (
        r"tbank\.ru/career/(?:it/)?vacanc(?:y|ies)/(?:[a-z0-9_-]+/)+[a-z0-9_-]+/?$"
    )
    assert updated.monitor_config["expand_links"] == [
        r"tbank\.ru/career/it(?:/|$)",
        r"tbank\.ru/career/it/ml(?:/|$)",
        r"tbank\.ru/career/vacancies/it(?:/|\?|$)",
    ]
