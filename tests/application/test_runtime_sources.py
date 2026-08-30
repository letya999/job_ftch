from __future__ import annotations

import pytest

from job_ftch.application.source_inputs import build_source_spec_from_input


@pytest.mark.asyncio
async def test_build_source_spec_from_http_input_returns_career_site() -> None:
    spec = await build_source_spec_from_input("https://example.com/jobs")

    assert spec.type == "career_site"
    assert spec.source_name == "example_com_jobs"
    assert spec.monitor_config == {}


@pytest.mark.asyncio
async def test_build_source_spec_from_telegram_input_auto_detects_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_detect(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return "telegram_group"

    monkeypatch.setattr(
        "job_ftch.application.source_inputs._detect_telegram_source_type",
        _fake_detect,
    )

    spec = await build_source_spec_from_input("https://t.me/ml_jobs")

    assert spec.type == "telegram_group"
    assert spec.entity == "ml_jobs"
    assert spec.source_name == "ml_jobs"


@pytest.mark.asyncio
async def test_build_source_spec_accepts_t_me_without_scheme() -> None:
    spec = await build_source_spec_from_input("t.me/ml_jobs")

    assert spec.type == "telegram_channel"
    assert spec.entity == "ml_jobs"


@pytest.mark.asyncio
async def test_build_source_spec_accepts_inline_rss_type_hint() -> None:
    spec = await build_source_spec_from_input("rss:https://example.com/feed.xml")

    assert spec.type == "rss_feed"
    assert str(spec.feed_url) == "https://example.com/feed.xml"


@pytest.mark.asyncio
async def test_build_source_spec_keeps_explicit_and_fixture_source_name() -> None:
    named = await build_source_spec_from_input(
        "https://example.com/jobs",
        source_name="habr_career",
    )
    assert named.source_name == "habr_career"

    fixture = await build_source_spec_from_input("https://career.habr.com/vacancies")
    assert fixture.source_name == "habr_career"

    geekjob = await build_source_spec_from_input("https://geekjob.ru/vacancies")
    assert geekjob.source_name == "geekjob_ru"


@pytest.mark.asyncio
async def test_build_source_spec_accepts_inline_telegram_group_hint() -> None:
    spec = await build_source_spec_from_input("telegram_group:https://t.me/ml_jobs")

    assert spec.type == "telegram_group"
    assert spec.entity == "ml_jobs"
