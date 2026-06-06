# infrastructure/stores/sqlite_store.py (обновлённый)

import sqlite3
import json
from typing import Optional, List, Dict, Any
from pathlib import Path

from domain import RawItem, Job  # Новые модели


class SQLiteStore:
    """Store implementation using SQLite with proper indexes."""
    
    def __init__(self, db_path: str = "data/jobs.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()
    
    def _init_tables(self) -> None:
        """Initialize database schema with indexes."""
        with self._get_connection() as conn:
            # Jobs table (адаптированная под новую модель)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    stable_id TEXT PRIMARY KEY,
                    raw_item_id TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    description TEXT NOT NULL,
                    canonical_url TEXT,
                    location TEXT,
                    work_mode TEXT DEFAULT 'unknown',
                    compensation_currency TEXT,
                    compensation_min INTEGER,
                    compensation_max INTEGER,
                    metadata TEXT,
                    found_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Raw items table (адаптированная под новую модель)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS raw_items (
                    stable_id TEXT PRIMARY KEY,
                    source_kind TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    external_id TEXT,
                    url TEXT,
                    text TEXT NOT NULL,
                    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    metadata TEXT,
                    processed BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Processing state table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processing_state (
                    key TEXT PRIMARY KEY,
                    last_processed_at TIMESTAMP,
                    last_item_id TEXT
                )
            """)
            
            # Индексы
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source_kind, source_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_source ON raw_items(source_kind, source_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_fetched ON raw_items(fetched_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_processed ON raw_items(processed)")
            
            conn.commit()
    
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    async def save_raw_item(self, item: RawItem) -> None:
        """Save raw item from source."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO raw_items 
                (stable_id, source_kind, source_name, external_id, url, text, metadata, processed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.stable_id,
                str(item.source_kind),
                item.source_name,
                item.external_id,
                str(item.url) if item.url else None,
                item.text,
                json.dumps(item.metadata),
                0
            ))
            conn.commit()
    
    async def is_processed(self, item_id: str) -> bool:
        """Check if raw item was already processed."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT processed FROM raw_items WHERE stable_id = ?", (item_id,)
            )
            row = cursor.fetchone()
            return row["processed"] == 1 if row else False
    
    async def mark_processed(self, item_id: str) -> None:
        """Mark raw item as processed."""
        with self._get_connection() as conn:
            conn.execute("UPDATE raw_items SET processed = 1 WHERE stable_id = ?", (item_id,))
            conn.commit()
    
    async def get_last_state(self, key: str) -> Optional[str]:
        """Get last processing state."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT last_item_id FROM processing_state WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["last_item_id"] if row else None
    
    async def set_last_state(self, key: str, value: str) -> None:
        """Set last processing state."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO processing_state (key, last_item_id, last_processed_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (key, value))
            conn.commit()
    
    async def save_job(self, job: Job) -> str:
        """Save or update a job."""
        with self._get_connection() as conn:
            compensation = job.compensation
            conn.execute("""
                INSERT OR REPLACE INTO jobs 
                (stable_id, raw_item_id, source_kind, source_name, title, company, 
                 description, canonical_url, location, work_mode, 
                 compensation_currency, compensation_min, compensation_max, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job.stable_id,
                job.raw_item_id,
                str(job.source_kind),
                job.source_name,
                job.title,
                job.company,
                job.description,
                str(job.canonical_url) if job.canonical_url else None,
                job.location,
                str(job.work_mode),
                compensation.currency if compensation else None,
                compensation.min_amount if compensation else None,
                compensation.max_amount if compensation else None,
                json.dumps(job.metadata)
            ))
            conn.commit()
            return job.stable_id

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
