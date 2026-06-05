# infrastructure/stores/sqlite_store.py
"""SQLite implementation of Store Protocol."""

import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pathlib import Path

from domain.protocols import Store
from domain.models import Job, RawItem


class SQLiteStore(Store):
    """Store implementation using SQLite with proper indexes."""

    def __init__(self, db_path: str = "data/jobs.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _init_tables(self) -> None:
        """Initialize database schema with indexes."""
        with self._get_connection() as conn:
            # Jobs table (ваша схема)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT,
                    description TEXT,
                    skills TEXT DEFAULT '[]',
                    experience_years INTEGER DEFAULT 0,
                    salary_min INTEGER,
                    salary_max INTEGER,
                    remote BOOLEAN DEFAULT 0,
                    location TEXT,
                    url TEXT UNIQUE,
                    raw_content TEXT,
                    found_at DATE DEFAULT CURRENT_DATE,
                    expires_at DATE DEFAULT (date('now', '+7 days')),
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Raw items table (для Source)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS raw_items (
                    id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed BOOLEAN DEFAULT 0
                )
            """)

            # Processing state table (для дедупликации)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processing_state (
                    key TEXT PRIMARY KEY,
                    last_processed_at TIMESTAMP,
                    last_item_id TEXT
                )
            """)

            # Индексы (ваши + дополнительные)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_found ON jobs(found_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_expires ON jobs(expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_url ON jobs(url)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_skills ON jobs(skills)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_fetched ON raw_items(fetched_at)")

            conn.commit()

    def _get_connection(self):
        """Get database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # Store Protocol methods
    async def save_job(self, job: Job) -> str:
        """Save or update a job."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR REPLACE INTO jobs 
                (source, title, company, description, skills, experience_years,
                 salary_min, salary_max, remote, location, url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    job.source,
                    job.title,
                    job.company,
                    job.description,
                    json.dumps(job.skills),
                    job.experience_years,
                    job.salary_min,
                    job.salary_max,
                    job.remote,
                    job.location,
                    job.url,
                ),
            )
            conn.commit()
            return str(cursor.lastrowid)

    async def save_raw_item(self, item: RawItem) -> None:
        """Save raw item from source."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO raw_items (id, source_type, source_id, content, metadata)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    item.id,
                    item.source_type,
                    item.source_id,
                    item.content,
                    json.dumps(item.metadata),
                ),
            )
            conn.commit()

    async def is_processed(self, item_id: str) -> bool:
        """Check if raw item was already processed."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM raw_items WHERE id = ? AND processed = 1", (item_id,)
            )
            return cursor.fetchone() is not None

    async def mark_processed(self, item_id: str) -> None:
        """Mark raw item as processed."""
        with self._get_connection() as conn:
            conn.execute("UPDATE raw_items SET processed = 1 WHERE id = ?", (item_id,))
            conn.commit()

    async def get_last_state(self, key: str) -> Optional[str]:
        """Get last processing state (e.g., last message ID from channel)."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT last_item_id FROM processing_state WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["last_item_id"] if row else None

    async def set_last_state(self, key: str, value: str) -> None:
        """Set last processing state."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO processing_state (key, last_item_id, last_processed_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
                (key, value),
            )
            conn.commit()

    # Query methods (для бота)
    async def get_active_jobs(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Get active jobs for matching."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM jobs 
                WHERE is_active = 1 AND expires_at >= date('now')
                ORDER BY found_at DESC
                LIMIT ?
            """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]
