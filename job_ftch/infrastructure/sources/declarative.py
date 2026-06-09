"""Declarative career-site extraction configs and adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser

from job_ftch.application.registry import register_parser, register_source_v2
from job_ftch.domain import RawItem, SourceKind
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item

if TYPE_CHECKING:
    from job_ftch.application.contracts import AuthProvider
    from job_ftch.domain.source_spec import DeclarativeHtmlSpec


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _source_name_from_url(url: str) -> str:
    path = urlsplit(url).path.strip("/")
    return path.split("/")[-1] or "career-site"


class CareerSiteConfig(BaseModel):
    kind: str
    board_selector: str | None = None
    row_selector: str
    link_selector: str
    title_selector: str | None = None
    location_selector: str | None = None
    section_selector: str | None = None
    team_selector: str | None = None
    href_contains: str | None = None
    metadata_defaults: dict[str, object] = Field(default_factory=dict)

    @classmethod
    def greenhouse(cls) -> CareerSiteConfig:
        return cls(
            kind="greenhouse",
            board_selector=".job-posts",
            row_selector="h3.section-header, h4.section-header, h4.sub-section-header, tr.job-post",
            link_selector='a[href*="/jobs/"]',
            title_selector="p.body--medium",
            location_selector="p.body__secondary",
            section_selector="h3.section-header",
            team_selector="h4.section-header, h4.sub-section-header",
            href_contains="/jobs/",
            metadata_defaults={"parser": "greenhouse"},
        )

    @classmethod
    def alfabank(cls) -> CareerSiteConfig:
        return cls(
            kind="alfabank",
            row_selector=".vacancy, [aria-label='vacancy']",
            link_selector="a[href*='/vacancies/']",
            title_selector=".vacancy__title",
            location_selector=".vacancy__city, .vacancy__location",
            metadata_defaults={"parser": "alfabank"},
        )

    @classmethod
    def from_spec(cls, spec: DeclarativeHtmlSpec) -> CareerSiteConfig:
        url_lower = spec.url.lower()
        if spec.parser_kind == "greenhouse" or (
            spec.parser_kind == "auto" and "greenhouse.io" in url_lower
        ):
            return cls.greenhouse()
        if spec.parser_kind == "alfabank" or (
            spec.parser_kind == "auto" and "alfabank.ru" in url_lower
        ):
            return cls.alfabank()

        return cls(
            kind="generic",
            row_selector="a",
            link_selector="a",
            title_selector=None,
        )


class DeclarativeCareerSiteParser:
    def __init__(self, config: CareerSiteConfig) -> None:
        self._config = config

    async def parse(
        self,
        *,
        client: Any,
        url: str,
        html: str,
        limit: int,
    ) -> list[RawItem]:
        del client
        parser = LexborHTMLParser(html)
        source_name = self._extract_source_name(parser, url)
        container = (
            parser.css_first(self._config.board_selector) if self._config.board_selector else None
        )
        scope = container or parser
        items: list[RawItem] = []
        current_section: str | None = None
        current_team: str | None = None

        for node in scope.css(self._config.row_selector):
            tag_name = node.tag
            if self._config.section_selector and tag_name == "h3":
                current_section = _clean_text(node.text(separator=" ", strip=True))
                current_team = None
                continue
            if self._config.team_selector and tag_name == "h4":
                current_team = _clean_text(node.text(separator=" ", strip=True))
                continue
            item = self._build_item(
                board_url=url,
                source_name=source_name,
                row=node,
                section=current_section,
                team=current_team,
            )
            if item is None:
                continue
            items.append(item)
            if len(items) >= limit:
                return items

        if items:
            return items

        for node in scope.css(self._config.link_selector):
            item = self._build_item(
                board_url=url,
                source_name=source_name,
                row=node,
                section=None,
                team=None,
            )
            if item is None:
                continue
            items.append(item)
            if len(items) >= limit:
                break
        return items

    def _build_item(
        self,
        *,
        board_url: str,
        source_name: str,
        row: Any,
        section: str | None,
        team: str | None,
    ) -> RawItem | None:
        anchor = row.css_first(self._config.link_selector) if row.tag != "a" else row
        if anchor is None:
            return None
        href = anchor.attributes.get("href")
        if not href:
            return None
        if self._config.href_contains and self._config.href_contains not in href:
            return None
        full_url = urljoin(board_url, href)
        title = self._extract_title(anchor)
        if not title:
            return None
        location = self._extract_text(anchor, self._config.location_selector)
        text_parts = [title, location, section, team]
        metadata = dict(self._config.metadata_defaults)
        metadata.update(
            {
                "board_url": board_url,
                "job_url": full_url,
                "department": section,
                "team": team,
                "location": location,
            }
        )
        return build_raw_item(
            source_kind=SourceKind.CAREER_SITE,
            source_name=source_name,
            external_id=full_url.rstrip("/").split("/")[-1],
            url=full_url,
            text="\n".join(part for part in text_parts if part),
            metadata=metadata,
        )

    def _extract_source_name(self, parser: LexborHTMLParser, url: str) -> str:
        og_title = parser.css_first('meta[property="og:title"]')
        if og_title is not None:
            value = og_title.attributes.get("content")
            if value:
                return _clean_text(value)
        return _source_name_from_url(url)

    def _extract_text(self, node: Any, selector: str | None) -> str | None:
        if selector is None:
            return None
        selected = node.css_first(selector)
        if selected is None:
            return None
        value = _clean_text(selected.text(separator=" ", strip=True))
        return value or None

    def _extract_title(self, anchor: Any) -> str | None:
        if self._config.title_selector is None:
            return _clean_text(anchor.text(separator=" ", strip=True)) or None
        title_node = anchor.css_first(self._config.title_selector)
        if title_node is None:
            return _clean_text(anchor.text(separator=" ", strip=True)) or None
        primary_text = title_node.text(separator=" ", strip=True)
        badge = title_node.css_first(".tag-container, .tag-text")
        if badge is None:
            return _clean_text(primary_text) or None
        badge_text = _clean_text(badge.text(separator=" ", strip=True))
        cleaned = primary_text.replace(badge_text, " ", 1) if badge_text else primary_text
        return _clean_text(cleaned) or None


class DeclarativeCareerSiteSource:
    def __init__(
        self,
        client: Any,
        site_url: str,
        config: CareerSiteConfig,
        *,
        limit: int = 100,
        own_client: bool = False,
    ) -> None:
        from job_ftch.infrastructure.sources.career_site import CareerSiteSource

        self._delegate = CareerSiteSource(
            client,
            site_url,
            limit=limit,
            own_client=own_client,
            parser=DeclarativeCareerSiteParser(config),
        )

    async def fetch(self):  # type: ignore[no-untyped-def]
        async for item in self._delegate.fetch():
            yield item


def _is_greenhouse(url: str, html: str) -> bool:
    del html
    return "greenhouse.io" in url.lower()


@register_parser("greenhouse", matcher=_is_greenhouse)
def _build_greenhouse_parser() -> DeclarativeCareerSiteParser:
    return DeclarativeCareerSiteParser(CareerSiteConfig.greenhouse())


@register_source_v2("declarative_html")
def _build_declarative_html_source_v2(
    spec: DeclarativeHtmlSpec,
    auth: AuthProvider,
) -> DeclarativeCareerSiteSource:
    del auth
    from job_ftch.infrastructure.sources.career_site import build_default_http_client

    client = build_default_http_client()
    config = CareerSiteConfig.from_spec(spec)
    return DeclarativeCareerSiteSource(
        client,
        spec.url,
        config,
        limit=spec.limit,
        own_client=True,
    )
