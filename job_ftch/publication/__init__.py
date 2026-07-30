"""YAML-driven publication subsystem.

Builds deterministic PublicationCards from structured JobRecord fields,
renders them through a capability-aware layout engine, and validates
before dispatch.
"""

from job_ftch.publication.card import PublicationCard
from job_ftch.publication.layout import CardLayout, load_layout
from job_ftch.publication.normalize import format_compensation, format_location
from job_ftch.publication.render import SinkCapabilities, render_card
from job_ftch.publication.validate import ValidationOutcome, validate_card

__all__ = [
    "CardLayout",
    "PublicationCard",
    "SinkCapabilities",
    "ValidationOutcome",
    "format_compensation",
    "format_location",
    "load_layout",
    "render_card",
    "validate_card",
]
