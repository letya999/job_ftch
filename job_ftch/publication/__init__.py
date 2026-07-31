"""YAML-driven publication subsystem.

Builds deterministic PublicationCards from structured JobRecord fields,
renders them through a capability-aware layout engine, and validates
before dispatch.
"""

from job_ftch.publication.card import PublicationCard
from job_ftch.publication.dedup import deduplicate_for_publish
from job_ftch.publication.interleave import interleave_jobs
from job_ftch.publication.layout import CardLayout, load_layout
from job_ftch.publication.normalize import format_compensation, format_geo, format_work_mode
from job_ftch.publication.pacing import Burst, PacingConfig, plan_bursts
from job_ftch.publication.render import SinkCapabilities, render_card
from job_ftch.publication.validate import ValidationOutcome, validate_card

__all__ = [
    "Burst",
    "CardLayout",
    "PacingConfig",
    "PublicationCard",
    "SinkCapabilities",
    "ValidationOutcome",
    "deduplicate_for_publish",
    "format_compensation",
    "format_geo",
    "format_work_mode",
    "interleave_jobs",
    "load_layout",
    "plan_bursts",
    "render_card",
    "validate_card",
]
