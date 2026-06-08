"""Compatibility wrapper for legacy repository scripts and tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from job_ftch.application.builder import build_source, build_store
from job_ftch.application.builder import run_pipeline_from_settings as run_pipeline
from job_ftch.application.registry import create_sink
from job_ftch.cli import main
from job_ftch.sinks import CountedSink, FailureTolerantSink, FanOutSink, RoutingSink

if TYPE_CHECKING:
    from collections.abc import Callable

    from job_ftch.config import Settings
    from job_ftch.domain import Job


def build_sink(settings: Settings):
    return create_sink(settings)


def build_quarantine_sink(settings: Settings):
    return FailureTolerantSink(
        create_sink(settings, quarantine=True),  # type: ignore[arg-type]
        sink_name="quarantine",
    )


def build_rejected_sink(settings: Settings):
    counted = CountedSink(create_sink(settings.rejected_settings()))  # type: ignore[arg-type]
    return counted, FailureTolerantSink(counted, sink_name="rejected")


def _needs_review(settings: Settings) -> Callable[[Job], bool]:
    def predicate(job: Job) -> bool:
        return (
            bool(job.review_reasons)
            or (job.quality_score or 0.0) < settings.review_max_quality_score
        )

    return predicate


def _should_post(settings: Settings) -> Callable[[Job], bool]:
    def predicate(job: Job) -> bool:
        return (
            not job.review_reasons
            and (job.quality_score or 0.0) >= settings.posting_min_quality_score
        )

    return predicate


def build_output_sinks(settings: Settings):
    main_sink: CountedSink[Job] = CountedSink(build_sink(settings))
    sink_chain = [main_sink]
    review_counted: CountedSink[Job] = CountedSink(
        create_sink(settings.review_settings())  # type: ignore[arg-type]
    )
    sink_chain.append(
        RoutingSink(
            [(_needs_review(settings), FailureTolerantSink(review_counted, sink_name="review"))]
        )
    )
    posting_sink: CountedSink[Job] | None = None
    if not settings.dry_run and settings.posting_backend != "none":
        posting_counted: CountedSink[Job] = CountedSink(
            create_sink(settings.posting_settings())  # type: ignore[arg-type]
        )
        posting_sink = posting_counted
        sink_chain.append(
            RoutingSink(
                [
                    (
                        _should_post(settings),
                        FailureTolerantSink(posting_counted, sink_name="posting"),
                    )
                ],
            )
        )
    return FanOutSink(sink_chain), review_counted, posting_sink


__all__ = [
    "build_output_sinks",
    "build_quarantine_sink",
    "build_rejected_sink",
    "build_sink",
    "build_source",
    "build_store",
    "create_sink",
    "main",
    "run_pipeline",
]


if __name__ == "__main__":
    raise SystemExit(main())
