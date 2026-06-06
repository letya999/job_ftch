# infrastructure/sources/external_source.py
"""External job source (API/RSS based on VacancyEngine).
Fetches jobs from Remotive, Jobicy, Arbeitnow, RemoteOK, WeWorkRemotely, StackOverflow.
"""

import asyncio
import hashlib
import logging
from typing import AsyncIterator, Optional, List, Dict, Any
from datetime import datetime, UTC

import httpx
import feedparser

from domain import RawItem, SourceKind
from domain.ai_keywords import is_ai_job
from domain.skills import extract_skills_from_text

logger = logging.getLogger(__name__)


class ExternalJobSource:
    """Fetch jobs from external APIs and RSS feeds."""

    def __init__(
        self,
        sources: Optional[List[str]] = None,
        max_jobs_per_source: int = 50,
        user_agent: Optional[str] = None,
    ):
        self.sources = sources or [
            "remotive", "jobicy", "arbeitnow",
            "remoteok", "weworkremotely", "stackoverflow"
        ]
        self.max_jobs_per_source = max_jobs_per_source
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self._items: List[RawItem] = []
        self._index = 0
    
    async def fetch(self) -> AsyncIterator[RawItem]:
        """Fetch all external jobs and yield as RawItems."""
        if not self._items:
            await self._load_all_jobs()
        
        while self._index < len(self._items):
            yield self._items[self._index]
            self._index += 1
    
    async def _load_all_jobs(self) -> None:
        """Load jobs from configured sources concurrently."""
        logger.info(f"Loading jobs from sources: {self.sources}")
        
        tasks = []
        if "remotive" in self.sources:
            tasks.append(self._fetch_remotive())
        if "jobicy" in self.sources:
            tasks.append(self._fetch_jobicy())
        if "arbeitnow" in self.sources:
            tasks.append(self._fetch_arbeitnow())
        if "remoteok" in self.sources:
            tasks.append(self._fetch_remoteok_rss())
        if "weworkremotely" in self.sources:
            tasks.append(self._fetch_wwr_rss())
        if "stackoverflow" in self.sources:
            tasks.append(self._fetch_stackoverflow_rss())
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_jobs: List[Dict[str, Any]] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Source fetch error: {result}")
            elif result:
                all_jobs.extend(result)
        
        logger.info(f"Total raw jobs fetched: {len(all_jobs)}")
        
        # Deduplication by URL
        seen_urls = set()
        deduped_jobs = []
        for job in all_jobs:
            url = job.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                deduped_jobs.append(job)
            elif not url:
                key = f"{job.get('title', '')}_{job.get('company', '')}"
                if key not in seen_urls:
                    seen_urls.add(key)
                    deduped_jobs.append(job)
        
        logger.info(f"After dedup: {len(deduped_jobs)} jobs")
        
        # AI filter
        ai_jobs = []
        for job in deduped_jobs:
            if is_ai_job(job.get("title", ""), job.get("description", ""), job.get("skills", [])):
                ai_jobs.append(job)
        
        logger.info(f"After AI filter: {len(ai_jobs)} jobs")
        
        # Convert to RawItems (используя новую структуру)
        for job in ai_jobs:
            url = job.get("url", "")
            source_name = f"external_{job['source']}"
            
            # Создаём RawItem с новыми полями
            try:
                raw_item = RawItem(
                    source_kind=SourceKind.CAREER_SITE,  # Внешние API = career_site
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
                    }
                )
                self._items.append(raw_item)
            except Exception as e:
                logger.error(f"Failed to create RawItem for job {job.get('title')}: {e}")
        
        logger.info(f"Created {len(self._items)} RawItems")
    
    # ============ API Sources ============

    async def _fetch_remotive(self) -> List[Dict[str, Any]]:
        """Fetch from Remotive API."""
        jobs = []
        try:
            logger.info("Fetching Remotive API...")
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    "https://remotive.com/api/remote-jobs", headers={"User-Agent": self.user_agent}
                )

            if response.status_code == 200:
                data = response.json()
                raw_jobs = data.get("jobs", [])
                logger.info(f"Remotive: {len(raw_jobs)} jobs")

                for job in raw_jobs[: self.max_jobs_per_source]:
                    title = job.get("title", "")
                    description = job.get("description", "")

                    # Extract skills from text
                    skills = extract_skills_from_text(f"{title} {description}")

                    url = job.get("url", "")
                    if not url:
                        slug = job.get("slug", "")
                        if slug:
                            url = f"https://remotive.com/remote-jobs/{slug}"

                    jobs.append(
                        {
                            "source": "remotive",
                            "title": title,
                            "company": job.get("company_name", "Unknown"),
                            "description": description,
                            "skills": skills,
                            "experience_years": 0,
                            "salary_min": self._parse_salary(job.get("salary", "")),
                            "salary_max": 0,
                            "remote": True,
                            "location": job.get("candidate_required_location", "Remote"),
                            "url": url,
                        }
                    )
        except Exception as e:
            logger.error(f"Remotive API error: {e}")
        return jobs

    async def _fetch_jobicy(self) -> List[Dict[str, Any]]:
        """Fetch from Jobicy API."""
        jobs = []
        try:
            logger.info("Fetching Jobicy API...")
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    "https://jobicy.com/api/v2/remote-jobs", headers={"User-Agent": self.user_agent}
                )

            if response.status_code == 200:
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

                    jobs.append(
                        {
                            "source": "jobicy",
                            "title": title,
                            "company": job.get("companyName", "Unknown"),
                            "description": description,
                            "skills": skills,
                            "experience_years": 0,
                            "salary_min": self._parse_salary(job.get("salary", "")),
                            "salary_max": 0,
                            "remote": True,
                            "location": job.get("jobLocation", "Remote"),
                            "url": url,
                        }
                    )
        except Exception as e:
            logger.error(f"Jobicy API error: {e}")
        return jobs

    async def _fetch_arbeitnow(self) -> List[Dict[str, Any]]:
        """Fetch from Arbeitnow API."""
        jobs = []
        try:
            logger.info("Fetching Arbeitnow API...")
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    "https://www.arbeitnow.com/api/job-board-api",
                    headers={"User-Agent": self.user_agent},
                )

            if response.status_code == 200:
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

    async def _fetch_remoteok_rss(self) -> List[Dict[str, Any]]:
        """Fetch from RemoteOK RSS (sync wrapper)."""
        jobs = []
        try:
            logger.info("Fetching RemoteOK RSS...")
            # feedparser is sync, run in thread pool
            feed = await asyncio.to_thread(feedparser.parse, "https://remoteok.io/remote-jobs.rss")
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

    async def _fetch_wwr_rss(self) -> List[Dict[str, Any]]:
        """Fetch from WeWorkRemotely RSS."""
        jobs = []
        try:
            logger.info("Fetching WeWorkRemotely RSS...")
            feed = await asyncio.to_thread(
                feedparser.parse,
                "https://weworkremotely.com/categories/remote-programming-jobs.rss",
            )
            if len(feed.entries) == 0:
                feed = await asyncio.to_thread(
                    feedparser.parse, "https://weworkremotely.com/feed.xml"
                )

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

    async def _fetch_stackoverflow_rss(self) -> List[Dict[str, Any]]:
        """Fetch from StackOverflow RSS."""
        jobs = []
        try:
            logger.info("Fetching StackOverflow RSS...")
            feed = await asyncio.to_thread(feedparser.parse, "https://stackoverflow.com/jobs/feed")

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

    def _parse_salary(self, salary_str: Any) -> int:
        """Parse salary from various formats."""
        if not salary_str:
            return 0

        if isinstance(salary_str, (int, float)):
            return int(salary_str)

        salary_str = str(salary_str)
        try:
            cleaned = salary_str.replace("$", "").replace(",", "").strip()
            if "k" in cleaned.lower():
                num = float(cleaned.lower().replace("k", "").strip())
                return int(num * 1000)
            if "-" in cleaned:
                cleaned = cleaned.split("-")[0]
            return int(float(cleaned))
        except (ValueError, TypeError):
            return 0

    def _extract_company_from_title(self, title: str) -> str:
        """Extract company name from job title."""
        if " at " in title:
            parts = title.split(" at ")
            if len(parts) > 1:
                return parts[-1].strip()[:50]
        if " is hiring" in title:
            return title.split(" is hiring")[0].strip()[:50]
        if " is looking" in title:
            return title.split(" is looking")[0].strip()[:50]
        if " - " in title:
            return title.split(" - ")[0].strip()[:50]
        return "Unknown"


# Optional: Factory function for easy creation
def create_external_source(
    sources: Optional[List[str]] = None, max_jobs: int = 50
) -> ExternalJobSource:
    """Create configured ExternalJobSource instance."""
    return ExternalJobSource(sources=sources, max_jobs_per_source=max_jobs)
