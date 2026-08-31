from __future__ import annotations

import inspect

from job_ftch.application import registry
from job_ftch.application.registry import known_board_assessment_hint
from job_ftch.infrastructure.sources import site_parsers
from job_ftch.infrastructure.sources.site_parsers.base import SiteParser


def test_site_parsers_package_imports_linkedin_module() -> None:
    assert "linkedin" in site_parsers.__all__


def test_registered_site_parsers_implement_current_contract() -> None:
    registry.load_extensions()
    assert registry._site_parser_factories, "expected built-in site parsers to be registered"

    for entry in registry._site_parser_factories:
        parser = entry.factory()
        assert hasattr(parser, "domain_pattern"), entry.name
        assert hasattr(parser, "has_custom_parse"), entry.name
        assert callable(getattr(parser, "runtime_defaults", None)), entry.name
        assert callable(getattr(parser, "parser_kind", None)), entry.name
        if getattr(parser, "has_custom_parse", False):
            if getattr(parser, "supports_discover", False):
                discover = getattr(parser, "discover", None)
                assert discover is not None, entry.name
                assert inspect.iscoroutinefunction(discover), entry.name
            else:
                parse = getattr(parser, "parse", None)
                # Inheriting the Protocol stub makes ``hasattr(parse)`` true
                # but the runtime then receives an empty async generator.
                # A custom parser must provide its own executable method.
                assert parse is not None, entry.name
                assert type(parser).parse is not SiteParser.parse, entry.name
                assert inspect.iscoroutinefunction(parse) or inspect.isasyncgenfunction(parse), (
                    entry.name
                )


def test_register_site_parser_preserves_assessment_hint_contract() -> None:
    hint = known_board_assessment_hint(
        "known_site",
        "site_parser:test",
        requires_full_snapshot=False,
    )

    @registry.register_site_parser(
        "test_contract_parser",
        domain_pattern=r"^https://contract\.example/jobs",
        assessment_hint=hint,
    )
    class _TestParser:
        has_custom_parse = False
        domain_pattern = r"^https://contract\.example/jobs"

        def runtime_defaults(self, url: str) -> object | None:
            del url
            return None

        def parser_kind(self, url: str) -> str | None:
            del url
            return None

        async def parse(self, spec: object, client: object) -> None:
            del spec, client
            return None

    resolved_hint = registry.resolve_site_parser_assessment_hint("https://contract.example/jobs")
    assert resolved_hint is not None
    assert resolved_hint.evidence_value == "site_parser:test"
