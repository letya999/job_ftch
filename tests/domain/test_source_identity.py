from job_ftch.domain import (
    AcquisitionTransport,
    CandidateSpan,
    JobRecord,
    ObservationKind,
    RawItem,
    SourceFamily,
    SourceIdentity,
    SourceKind,
    source_identity_for_raw_item,
)
from job_ftch.domain.site_models import DiscoveredPostingPayload
from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.raw_item_factory import build_raw_item
from job_ftch.infrastructure.sources.site_utils import payload_to_raw_item


def test_legacy_raw_item_gets_conservative_source_identity() -> None:
    item = RawItem(
        source_kind=SourceKind.TELEGRAM_CHANNEL,
        source_name="channel",
        external_id="1",
        text="job",
    )

    identity = source_identity_for_raw_item(item)

    assert identity.family is SourceFamily.TELEGRAM
    assert identity.legacy_kind == SourceKind.TELEGRAM_CHANNEL


def test_explicit_source_identity_survives_candidate_materialization() -> None:
    identity = SourceIdentity(
        family=SourceFamily.ATS_API,
        observation_kind=ObservationKind.STRUCTURED_RECORD,
        transport=AcquisitionTransport.HTTP,
        adapter="greenhouse",
        parser_version="1",
    )
    item = RawItem(
        source_kind=SourceKind.CAREER_SITE,
        source_identity=identity,
        source_name="greenhouse",
        external_id="1",
        text="Senior engineer",
    )
    span = CandidateSpan(
        parent_observation_id=item.stable_id,
        ordinal=0,
        text=item.text,
        raw_item=item,
    ).materialize_raw_item()

    assert span.source_identity == identity


def test_raw_item_factory_assigns_web_detail_identity() -> None:
    item = build_raw_item(
        source_kind=SourceKind.CAREER_SITE,
        source_name="example",
        external_id="job-1",
        url="https://example.com/jobs/1",
        text="Engineer",
    )

    assert item.source_identity is not None
    assert item.source_identity.family is SourceFamily.CAREER_WEB
    assert item.source_identity.observation_kind is ObservationKind.VACANCY_DETAIL


def test_confirmed_detail_with_board_url_is_not_mislabeled_as_listing() -> None:
    item = build_raw_item(
        source_kind=SourceKind.CAREER_SITE,
        source_name="hh",
        external_id="42",
        url="https://hh.ru/vacancy/42",
        text="ML Engineer",
        metadata={
            "board_url": "https://hh.ru/search/vacancy?text=ai",
            "detail_vacancy_confirmed": True,
        },
    )

    assert item.source_identity is not None
    assert item.source_identity.observation_kind is ObservationKind.VACANCY_DETAIL


def test_normalized_record_can_resolve_identity() -> None:
    record = JobRecord(
        source_record_id="obs",
        raw_item_id="obs",
        source_kind=SourceKind.CAREER_SITE,
        source_name="ats",
        metadata={"source_family": "ats_api", "observation_kind": "structured_record"},
    )
    identity = source_identity_for_raw_item(record)
    assert identity.family is SourceFamily.ATS_API


def test_generic_payload_has_explicit_detail_identity() -> None:
    item = payload_to_raw_item(
        DiscoveredPostingPayload(
            url="https://example.com/jobs/42",
            title="ML Engineer",
            description="Build production ML systems",
            metadata={"detail_vacancy_confirmed": True},
        ),
        CareerSiteSpec(url="https://example.com/jobs", source_name="example"),
        "example",
    )

    assert item.source_identity is not None
    assert item.source_identity.observation_kind is ObservationKind.VACANCY_DETAIL
    assert item.metadata["observation_kind"] == "vacancy_detail"


def test_payload_timestamp_is_converted_to_utc_not_relabelled() -> None:
    item = payload_to_raw_item(
        DiscoveredPostingPayload(
            url="https://example.com/jobs/42",
            title="ML Engineer",
            date_posted="2026-06-01T12:00:00+03:00",
        ),
        CareerSiteSpec(url="https://example.com/jobs", source_name="example"),
        "example",
    )

    assert item.created_at is not None
    assert item.created_at.isoformat() == "2026-06-01T09:00:00+00:00"
