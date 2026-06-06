"""External job source (API/RSS based on VacancyEngine).
Fetches jobs from Remotive, Jobicy, Arbeitnow, RemoteOK, WeWorkRemotely, StackOverflow.

Configuration:
- Sources are identified by source_kind='career_site' in the database
- All external jobs are separated from Telegram sources
"""

import asyncio
import logging
import platform
import random
from collections.abc import AsyncIterator
from typing import Any

import feedparser
import httpx

from domain import RawItem, SourceKind
from domain.ai_keywords import is_ai_job
from domain.skills import extract_skills_from_text

logger = logging.getLogger(__name__)


def _get_universal_user_agent() -> str:
    """Generate a realistic cross-platform User-Agent string."""
    user_agents = {
        "Linux": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Darwin": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    return user_agents.get(
        platform.system(),
        "Mozilla/5.0 (compatible; job_ftch/1.0; +https://github.com/letya999/job_ftch)",
    )


class ExternalJobSource:
    """Fetch jobs from external APIs and RSS feeds.

    All jobs are marked with source_kind=CAREER_SITE to separate them from Telegram sources.
    Includes rate limiting and retry logic to avoid being blocked.

    Features:
    - Shared HTTP client with connection pooling
    - Semaphore-based concurrency control (default: 2 concurrent requests)
    - Exponential backoff with jitter for retries
    - Honors Retry-After headers from 429 responses
    - Separate retry handling for RSS feeds
    """

    def __init__(
        self,
        sources: list[str] | None = None,
        max_jobs_per_source: int = 50,
        user_agent: str | None = None,
        max_concurrent: int = 2,
    ):
        self.sources = sources or [
            "remotive",
            "jobicy",
            "arbeitnow",
            "remoteok",
            "weworkremotely",
            "stackoverflow",
        ]
        self.max_jobs_per_source = max_jobs_per_source
        self.user_agent = user_agent or _get_universal_user_agent()
        self.max_concurrent = max_concurrent
        self._items: list[RawItem] = []
        self._index = 0
        self._retry_count = 3

        # Shared HTTP client with connection pooling
        self._client = httpx.AsyncClient(
            timeout=15.0,
            headers={"User-Agent": self.user_agent},
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
            ),
        )

        # Concurrency control
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def fetch(self) -> AsyncIterator[RawItem]:
        """Fetch all external jobs and yield as RawItems."""
        if not self._items:
            await self._load_all_jobs()

        while self._index < len(self._items):
            yield self._items[self._index]
            self._index += 1

    async def _fetch_with_retry(self, url: str, source_name: str) -> httpx.Response | None:
        """Fetch URL with intelligent retry logic.

        Features:
        - Honors Retry-After header from 429 responses
        - Exponential backoff with jitter for other errors
        - Separate handling for 5xx errors vs timeouts
        """
        for attempt in range(self._retry_count):
            try:
                async with self._semaphore:
                    response = await self._client.get(url)

                if response.status_code == 200:
                    return response

                if response.status_code == 429:
                    # Handle rate limiting - check for Retry-After header
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = float(retry_after)
                        except ValueError:
                            # Retry-After could be HTTP date, fallback to exponential
                            delay = min(60, (2**attempt) + random.uniform(0, 1))
                    else:
                        # Exponential backoff with jitter
                        delay = min(60, (2**attempt) + random.uniform(0, 1))

                    logger.warning(
                        f"{source_name}: 429 rate limited, retrying in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{self._retry_count})"
                    )
                    await asyncio.sleep(delay)
                    continue

                if response.status_code in (500, 502, 503, 504):
                    # Server errors - exponential backoff
                    delay = min(30, (2**attempt) + random.uniform(0, 1))
                    logger.warning(
                        f"{source_name}: HTTP {response.status_code}, retrying in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{self._retry_count})"
                    )
                    await asyncio.sleep(delay)
                    continue

                # Other client errors (400, 403, 404, etc.) - don't retry
                logger.warning(f"{source_name}: HTTP {response.status_code}, giving up")
                return None

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                # Network issues - exponential backoff
                delay = min(30, (2**attempt) + random.uniform(0, 1))
                logger.warning(
                    f"{source_name}: {e.__class__.__name__}, retrying in {delay:.1f}s "
                    f"(attempt {attempt + 1}/{self._retry_count})"
                )
                await asyncio.sleep(delay)

            except Exception as e:
                logger.error(f"{source_name}: unexpected error: {e}")
                await asyncio.sleep(2**attempt)
                continue

        logger.error(f"{source_name}: failed after {self._retry_count} attempts")
        return None

    async def _fetch_rss_with_retry(self, url: str, source_name: str) -> Any:
        """Fetch and parse RSS feed with retry logic."""
        for attempt in range(self._retry_count):
            try:
                async with self._semaphore:
                    # Use semaphore for RSS too, but feedparser is CPU-bound
                    response = await self._client.get(url)

                if response.status_code != 200:
                    if response.status_code == 429:
                        retry_after = response.headers.get("Retry-After")
                        if retry_after:
                            try:
                                delay = float(retry_after)
                            except ValueError:
                                delay = min(60, (2**attempt) + random.uniform(0, 1))
                        else:
                            delay = min(60, (2**attempt) + random.uniform(0, 1))
                        logger.warning(f"{source_name}: 429 rate limited, retrying in {delay:.1f}s")
                        await asyncio.sleep(delay)
                        continue

                    if response.status_code in (500, 502, 503, 504):
                        delay = min(30, (2**attempt) + random.uniform(0, 1))
                        logger.warning(f"{source_name}: HTTP {response.status_code}, retrying")
                        await asyncio.sleep(delay)
                        continue

                    logger.warning(f"{source_name}: HTTP {response.status_code}, giving up")
                    return None

                # Parse RSS in thread pool to avoid blocking
                feed = await asyncio.to_thread(feedparser.parse, response.text)
                return feed

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                delay = min(30, (2**attempt) + random.uniform(0, 1))
                logger.warning(f"{source_name}: {e.__class__.__name__}, retrying in {delay:.1f}s")
                await asyncio.sleep(delay)

            except Exception as e:
                logger.error(f"{source_name}: RSS parse error: {e}")
                await asyncio.sleep(2**attempt)

        logger.error(f"{source_name}: failed after {self._retry_count} attempts")
        return None

    async def _load_all_jobs(self) -> None:
        """Load jobs from configured sources with concurrency control.

        Uses semaphore to limit concurrent requests to self.max_concurrent.
        All sources are executed concurrently but each respects the semaphore.
        """
        logger.info(f"Loading jobs from sources: {self.sources}")
        logger.debug(f"Using User-Agent: {self.user_agent}")
        logger.debug(f"Max concurrent requests: {self.max_concurrent}")

        # Build task list - they will be controlled by semaphore internally
        tasks = []

        # API Sources
        if "remotive" in self.sources:
            tasks.append(self._fetch_remotive())
        if "jobicy" in self.sources:
            tasks.append(self._fetch_jobicy())
        if "arbeitnow" in self.sources:
            tasks.append(self._fetch_arbeitnow())

        # RSS Sources
        if "remoteok" in self.sources:
            tasks.append(self._fetch_remoteok_rss())
        if "weworkremotely" in self.sources:
            tasks.append(self._fetch_wwr_rss())
        if "stackoverflow" in self.sources:
            tasks.append(self._fetch_stackoverflow_rss())

        # Execute all tasks concurrently - semaphore inside each prevents overload
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_jobs: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Source fetch error: {result}")
            elif isinstance(result, list):
                all_jobs.extend(result)
            elif result is not None:
                logger.warning(f"Unexpected result type: {type(result)}")

        logger.info(f"Total raw jobs fetched: {len(all_jobs)}")

        # Deduplication by URL
        seen_urls = set()
        seen_keys = set()
        deduped_jobs = []

        for job in all_jobs:
            url = job.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                deduped_jobs.append(job)
            elif not url:
                key = f"{job.get('title', '')}_{job.get('company', '')}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    deduped_jobs.append(job)

        logger.info(f"After dedup: {len(deduped_jobs)} jobs")

        # AI filter
        ai_jobs = []
        for job in deduped_jobs:
            if is_ai_job(job.get("title", ""), job.get("description", ""), job.get("skills", [])):
                ai_jobs.append(job)

        logger.info(f"After AI filter: {len(ai_jobs)} jobs")

        # Convert to RawItems with source_kind=CAREER_SITE
        for job in ai_jobs:
            url = job.get("url", "")
            source_name = f"external_{job['source']}"

            try:
                raw_item = RawItem(
                    source_kind=SourceKind.CAREER_SITE,
                    source_name=source_name,
                    external_id=job.get("url", job.get("title", "")),
                    url=url if url else None,
                    text=job.get("description", ""),
                    metadata={
                        "title": job.get("title", ""),
                        "company": job.get("company", ""),
                        "salary_min": job.get("salary_min", 0),
                        "salary_max": job.get("salary_max", 0),
                        "remote": job.get("remote", True),
                        "location": job.get("location", "Remote"),
                        "experience_years": job.get("experience_years", 0),
                        "skills": job.get("skills", []),
                        "source": job["source"],
                    },
                )
                self._items.append(raw_item)
            except Exception as e:
                logger.error(f"Failed to create RawItem for job {job.get('title')}: {e}")

        logger.info(f"Created {len(self._items)} RawItems")

    async def _close(self) -> None:
        """Close the HTTP client. Should be called when shutting down."""
        await self._client.aclose()

    # ============ API Sources ============

    async def _fetch_remotive(self) -> list[dict[str, Any]]:
        """Fetch from Remotive API."""
        jobs = []
        try:
            logger.info("Fetching Remotive API...")
            response = await self._fetch_with_retry(
                "https://remotive.com/api/remote-jobs", "remotive"
            )

            if response:
                data = response.json()
                raw_jobs = data.get("jobs", [])
                logger.info(f"Remotive: {len(raw_jobs)} jobs")

                for job in raw_jobs[: self.max_jobs_per_source]:
                    title = job.get("title", "")
                    description = job.get("description", "")
                    skills = extract_skills_from_text(f"{title} {description}")

                    url = job.get("url", "")
                    if not url:
                        slug = job.get("slug", "")
                        if slug:
                            url = f"https://remotive.com/remote-jobs/{slug}"

                    # Parse salary range
                    salary_min, salary_max = self._parse_salary(job.get("salary", ""))

                    jobs.append(
                        {
                            "source": "remotive",
                            "title": title,
                            "company": job.get("company_name", "Unknown"),
                            "description": description,
                            "skills": skills,
                            "experience_years": 0,
                            "salary_min": salary_min,
                            "salary_max": salary_max,
                            "remote": True,
                            "location": job.get("candidate_required_location", "Remote"),
                            "url": url,
                        }
                    )
        except Exception as e:
            logger.error(f"Remotive API error: {e}")
        return jobs

    async def _fetch_jobicy(self) -> list[dict[str, Any]]:
        """Fetch from Jobicy API."""
        jobs = []
        try:
            logger.info("Fetching Jobicy API...")
            response = await self._fetch_with_retry(
                "https://jobicy.com/api/v2/remote-jobs", "jobicy"
            )

            if response:
                data = response.json()
                raw_jobs = data.get("jobs", [])
                logger.info(f"Jobicy: {len(raw_jobs)} jobs")

                for job in raw_jobs[: self.max_jobs_per_source]:
                    title = job.get("jobTitle", "")
                    description = job.get("jobDescription", "")
                    skills = extract_skills_from_text(f"{title} {description}")

                    url = job.get("url", "") or job.get("applyUrl", "")
                    if not url and job.get("slug"):
                        url = f"https://jobicy.com/jobs/{job.get('slug')}"

                    # Parse salary range
                    salary_min, salary_max = self._parse_salary(job.get("salary", ""))

                    jobs.append(
                        {
                            "source": "jobicy",
                            "title": title,
                            "company": job.get("companyName", "Unknown"),
                            "description": description,
                            "skills": skills,
                            "experience_years": 0,
                            "salary_min": salary_min,
                            "salary_max": salary_max,
                            "remote": True,
                            "location": job.get("jobLocation", "Remote"),
                            "url": url,
                        }
                    )
        except Exception as e:
            logger.error(f"Jobicy API error: {e}")
        return jobs

    async def _fetch_arbeitnow(self) -> list[dict[str, Any]]:
        """Fetch from Arbeitnow API."""
        jobs = []
        try:
            logger.info("Fetching Arbeitnow API...")
            response = await self._fetch_with_retry(
                "https://www.arbeitnow.com/api/job-board-api", "arbeitnow"
            )

            if response:
                data = response.json()
                raw_jobs = data.get("data", []) or data.get("jobs", [])
                logger.info(f"Arbeitnow: {len(raw_jobs)} jobs")

                for job in raw_jobs[: min(self.max_jobs_per_source, 30)]:
                    title = job.get("title", "")
                    description = job.get("description", "")
                    skills = extract_skills_from_text(f"{title} {description}")
                    company = job.get("company_name", "") or job.get("company", "Unknown")

                    url = job.get("url", "")
                    if not url:
                        slug = job.get("slug", "")
                        if slug:
                            url = f"https://www.arbeitnow.com/view/{slug}"

                    is_remote = "remote" in (title + " " + description).lower()

                    jobs.append(
                        {
                            "source": "arbeitnow",
                            "title": title,
                            "company": company,
                            "description": description,
                            "skills": skills,
                            "experience_years": 0,
                            "salary_min": 0,
                            "salary_max": 0,
                            "remote": is_remote,
                            "location": job.get("location", "Remote" if is_remote else "On-site"),
                            "url": url,
                        }
                    )
        except Exception as e:
            logger.error(f"Arbeitnow API error: {e}")
        return jobs

    # ============ RSS Sources ============

    async def _fetch_remoteok_rss(self) -> list[dict[str, Any]]:
        """Fetch from RemoteOK RSS."""
        jobs = []
        try:
            logger.info("Fetching RemoteOK RSS...")
            feed = await self._fetch_rss_with_retry(
                "https://remoteok.io/remote-jobs.rss", "remoteok"
            )

            if feed and hasattr(feed, "entries"):
                logger.info(f"RemoteOK: {len(feed.entries)} entries")

                for entry in feed.entries[: self.max_jobs_per_source]:
                    title = entry.get("title", "")
                    description = entry.get("summary", "")
                    skills = extract_skills_from_text(f"{title} {description}")

                    jobs.append(
                        {
                            "source": "remoteok",
                            "title": title,
                            "company": self._extract_company_from_title(title),
                            "description": description,
                            "skills": skills,
                            "experience_years": 0,
                            "salary_min": 0,
                            "salary_max": 0,
                            "remote": True,
                            "location": "Remote",
                            "url": entry.get("link", ""),
                        }
                    )
        except Exception as e:
            logger.error(f"RemoteOK RSS error: {e}")
        return jobs

    async def _fetch_wwr_rss(self) -> list[dict[str, Any]]:
        """Fetch from WeWorkRemotely RSS."""
        jobs = []
        try:
            logger.info("Fetching WeWorkRemotely RSS...")
            feed = await self._fetch_rss_with_retry(
                "https://weworkremotely.com/categories/remote-programming-jobs.rss",
                "weworkremotely",
            )

            if feed and len(feed.entries) == 0:
                # Try main feed
                feed = await self._fetch_rss_with_retry(
                    "https://weworkremotely.com/feed.xml", "weworkremotely"
                )

            if feed and hasattr(feed, "entries"):
                for entry in feed.entries[: self.max_jobs_per_source]:
                    title = entry.get("title", "")
                    description = entry.get("summary", "") or entry.get("description", "")
                    skills = extract_skills_from_text(f"{title} {description}")

                    jobs.append(
                        {
                            "source": "weworkremotely",
                            "title": title,
                            "company": self._extract_company_from_title(title),
                            "description": description,
                            "skills": skills,
                            "experience_years": 0,
                            "salary_min": 0,
                            "salary_max": 0,
                            "remote": True,
                            "location": "Remote",
                            "url": entry.get("link", ""),
                        }
                    )
        except Exception as e:
            logger.error(f"WWR RSS error: {e}")
        return jobs

    async def _fetch_stackoverflow_rss(self) -> list[dict[str, Any]]:
        """Fetch from StackOverflow RSS."""
        jobs = []
        try:
            logger.info("Fetching StackOverflow RSS...")
            feed = await self._fetch_rss_with_retry(
                "https://stackoverflow.com/jobs/feed", "stackoverflow"
            )

            if feed and hasattr(feed, "entries"):
                for entry in feed.entries[: self.max_jobs_per_source]:
                    title = entry.get("title", "")
                    description = entry.get("summary", "")
                    skills = extract_skills_from_text(f"{title} {description}")
                    is_remote = "remote" in title.lower()

                    jobs.append(
                        {
                            "source": "stackoverflow",
                            "title": title,
                            "company": self._extract_company_from_title(title),
                            "description": description,
                            "skills": skills,
                            "experience_years": 0,
                            "salary_min": 0,
                            "salary_max": 0,
                            "remote": is_remote,
                            "location": "Remote" if is_remote else "On-site",
                            "url": entry.get("link", ""),
                        }
                    )
        except Exception as e:
            logger.error(f"StackOverflow RSS error: {e}")
        return jobs

    # ============ Helper Methods ============

    def _parse_salary(self, salary_str: Any) -> tuple[int, int]:
        """Parse salary range from various formats."""
        if not salary_str:
            return (0, 0)
        
        if isinstance(salary_str, (int, float)):
            return (int(salary_str), 0)
        
        salary_str = str(salary_str).lower().strip()
        
        # Remove $ and commas
        cleaned = salary_str.replace("$", "").replace(",", "").strip()
        
        # Check if the whole string has 'k' (thousands marker)
        has_k = "k" in cleaned
        
        # Remove 'k' for parsing
        if has_k:
            cleaned = cleaned.replace("k", "").strip()
        
        # Extract all numbers from the string
        import re
        numbers = re.findall(r"(\d+(?:\.\d+)?)", cleaned)
        
        if not numbers:
            return (0, 0)
        
        # Parse found numbers and apply multiplier if needed
        values = []
        for num_str in numbers:
            val = int(float(num_str))
            if has_k:
                val *= 1000
            values.append(val)
        
        # If only one number found
        if len(values) == 1:
            # Check if it's a "up to" case
            if "up to" in cleaned or "max" in cleaned:
                return (0, values[0])
            else:
                return (values[0], 0)
        
        # Two or more numbers - take first two as range
        if len(values) >= 2:
            min_val = min(values[0], values[1])
            max_val = max(values[0], values[1])
            return (min_val, max_val)
        
        return (values[0], 0)
        
    def _extract_company_from_title(self, title: str) -> str:
        """Extract company name from job title."""
        patterns = [" at ", " is hiring", " is looking", " - "]
        for pattern in patterns:
            if pattern in title:
                parts = title.split(pattern)
                if len(parts) > 1:
                    return parts[-1].strip()[:50]
        return "Unknown"


# Optional: Factory function
def create_external_source(
    sources: list[str] | None = None,
    max_jobs: int = 50,
    max_concurrent: int = 2,
) -> ExternalJobSource:
    """Create configured ExternalJobSource instance."""
    return ExternalJobSource(
        sources=sources,
        max_jobs_per_source=max_jobs,
        max_concurrent=max_concurrent,
    )
