"""Backward-compatible imports for the structured vacancy detector."""

from job_ftch.domain.structured_vacancy import (
    MIN_FIELDS_FOR_STRUCTURED,
    extract_structured_fields,
    is_structured_vacancy,
)

__all__ = [
    "MIN_FIELDS_FOR_STRUCTURED",
    "extract_structured_fields",
    "is_structured_vacancy",
]
