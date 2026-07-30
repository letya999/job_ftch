"""Canonical BGE-M3 cards shared by vacancy queries and shot anchors."""

from __future__ import annotations

from typing import Any


def build_bgem3_card(
    text: str,
    *,
    metadata: dict[str, Any] | None = None,
    max_chars: int = 4096,
) -> str:
    """Build one stable retrieval card without relying on raw-post ordering.

    Both stored examples and incoming vacancy observations use this function.
    Optional structured fields are preferred, while the original text remains a
    lossless fallback before the extraction stage is available.
    """
    metadata = metadata or {}
    body = _clean(text)
    title = _first_text(metadata, "normalized_title", "title", "job_title", "position")
    if not title:
        # Keep the source heading separate before whitespace normalization: the
        # latter deliberately collapses line breaks for stable embeddings.
        title = _clean(text.splitlines()[0]) if text.splitlines() else ""
    responsibilities = _first_text(metadata, "responsibilities", "responsibilities_raw", "duties")
    requirements = _first_text(metadata, "requirements", "requirements_raw", "skills")
    description = _first_text(metadata, "description", "description_raw", "summary") or body

    sections = [f"vacancy title: {title}"] if title else []
    if responsibilities:
        sections.append(f"responsibilities: {responsibilities}")
    if requirements:
        sections.append(f"requirements: {requirements}")
    if description:
        sections.append(f"description: {description}")
    return "\n".join(sections)[:max_chars]


def _first_text(metadata: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and (cleaned := _clean(value)):
            return cleaned
        if isinstance(value, (list, tuple)):
            values = [_clean(str(part)) for part in value]
            if cleaned := "; ".join(part for part in values if part):
                return cleaned
    return ""


def _clean(value: str) -> str:
    return " ".join(value.split())
