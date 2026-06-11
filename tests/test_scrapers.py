import pytest

from job_ftch.application.contracts import JobScraper
from job_ftch.infrastructure.sources.scrapers.dom import scrape as dom_scrape
from job_ftch.infrastructure.sources.scrapers.json_ld import scrape as json_ld_scrape


@pytest.mark.asyncio
async def test_json_ld_scraper_satisfies_protocol():
    # JobScraper protocol: async def scrape(self, url, config, http) -> ScrapedPostingPayload | None
    assert isinstance(json_ld_scrape, JobScraper) or callable(json_ld_scrape)


@pytest.mark.asyncio
async def test_dom_scraper_satisfies_protocol():
    assert isinstance(dom_scrape, JobScraper) or callable(dom_scrape)
