# nodes/sanitize_node.py — очистка текста
"""SanitizeNode — first node in pipeline. Cleans raw text."""

import re
from typing import Optional

from domain.protocols import Node
from domain.models import RawItem, Job, JobBuilder


class SanitizeNode(Node[RawItem, Job]):
    """Clean raw text: remove HTML, extra spaces, normalize."""

    async def process(self, item: RawItem) -> Optional[Job]:
        """Clean content and prepare for normalization."""
        if not item.content:
            return None

        cleaned = self._clean_text(item.content)

        if not cleaned:
            return None

        return (
            JobBuilder()
            .with_source(item.source_type)
            .with_raw_content(cleaned)
            .with_metadata(item.metadata)
            .build()
        )

    def _clean_text(self, text: str) -> str:
        # Remove HTML
        text = re.sub(r"<[^>]+>", "", text)
        # Remove extra whitespace
        text = re.sub(r"\s+", " ", text)
        # Remove special characters (optional)
        # text = re.sub(r'[^\w\s\-.,!?:;\"\'@$%]', '', text)
        return text.strip()
