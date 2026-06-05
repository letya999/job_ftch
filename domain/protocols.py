# domain/protocols.py
"""Core protocols (interfaces) for hexagonal architecture."""

from typing import Protocol, AsyncIterator, TypeVar, Optional, Dict, Any
from abc import abstractmethod

from domain.models import RawItem, Job

T = TypeVar("T")
U = TypeVar("U")


class Source(Protocol):
    """Source protocol - yields RawItem from external sources."""

    @abstractmethod
    async def fetch(self) -> AsyncIterator[RawItem]:
        """Fetch items from source as async iterator."""
        ...

    async def close(self) -> None:
        """Close source resources if needed."""
        pass


class Node(Protocol[T, U]):
    """Node protocol - processes items."""

    @abstractmethod
    async def process(self, item: T) -> Optional[U]:
        """Process single item, return transformed item or None if filtered."""
        ...


class Sink(Protocol[T]):
    """Sink protocol - outputs items."""

    @abstractmethod
    async def emit(self, item: T) -> None:
        """Emit processed item to output destination."""
        ...


class Store(Protocol):
    """Store protocol - persistence layer interface."""

    @abstractmethod
    async def save_job(self, job: Job) -> str:
        """Save or update a job. Returns job ID."""
        ...

    @abstractmethod
    async def save_raw_item(self, item: RawItem) -> None:
        """Save raw item from source."""
        ...

    @abstractmethod
    async def is_processed(self, item_id: str) -> bool:
        """Check if raw item was already processed."""
        ...

    @abstractmethod
    async def mark_processed(self, item_id: str) -> None:
        """Mark raw item as processed."""
        ...

    @abstractmethod
    async def get_last_state(self, key: str) -> Optional[str]:
        """Get last processing state (e.g., last message ID)."""
        ...

    @abstractmethod
    async def set_last_state(self, key: str, value: str) -> None:
        """Set last processing state."""
        ...


class LLMProvider(Protocol):
    """LLM Provider protocol for structured extraction."""

    @abstractmethod
    async def extract(self, text: str, schema: type) -> Dict[str, Any]:
        """Extract structured data from text using LLM."""
        ...
