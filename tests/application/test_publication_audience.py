from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from job_ftch.adapters.telegram_bot.public_jobs import PublicJobsCache, build_public_jobs_response
from job_ftch.application.channel_publisher import DeliveryReceipt, publish_jobs
from job_ftch.application.publication_audience import evaluate_audience, publication_facts
from job_ftch.application.publication_backlog import pending_candidate_ids, record_publish_candidate
from job_ftch.domain.job_group import merge_jobs
from job_ftch.domain.models import JobRecord
from job_ftch.domain.publication_policy import PublicationPolicy
from job_ftch.infrastructure.stores.in_memory import InMemoryStore
from job_ftch.publication.card import build_card


def posting(**fields: object) -> JobRecord:
    return JobRecord.model_validate(
        {
            "raw_item_id": "1479323",
            "source_name": "hirify_me",
            "source_kind": "career_site",
            "title": "Staff Product Manager, Agents (AI)",
            "company": "Example",
            "canonical_url": "https://hirify.me/jobs/1479323-staff-product-manager-agents",
            **fields,
        }
    )


@pytest.mark.parametrize(
    ("fields", "match", "expected"),
    [
        (
            {"location": "Almaty, Kazakhstan", "work_mode": "hybrid", "language": "en"},
            "any",
            "allow",
        ),
        (
            {"location": "New York City, United States", "work_mode": "hybrid", "language": "ru"},
            "any",
            "allow",
        ),
        (
            {"location": "New York City, United States", "work_mode": "hybrid", "language": "ru"},
            "all",
            "skip",
        ),
        (
            {
                "location": "Kazakhstan",
                "work_mode": "remote",
                "remote_restrictions": "US only",
                "language": "en",
            },
            "any",
            "skip",
        ),
        (
            {"work_mode": "remote", "remote_restrictions": "worldwide", "language": "ru"},
            "all",
            "allow",
        ),
        ({"work_mode": "remote", "country": "KZ", "language": "kk"}, "any", "review"),
        (
            {
                "work_mode": "remote",
                "description_raw": "Формат: Удалёнка — глобально (возможна работа из офисов компании)",
                "language": "en",
            },
            "any",
            "allow",
        ),
        (
            {
                "work_mode": "remote",
                "description_raw": "Remote worldwide except Kazakhstan",
                "language": "en",
            },
            "any",
            "review",
        ),
        (
            {
                "work_mode": "remote",
                "remote_restrictions": "worldwide except Kazakhstan",
                "language": "kk",
            },
            "any",
            "review",
        ),
    ],
)
def test_audience_examples(fields: dict[str, object], match: str, expected: str) -> None:
    policy = PublicationPolicy.model_validate(
        {
            "eligible_countries": ["KZ"],
            "posting_languages": ["ru"],
            "match": match,
        }
    )
    assert evaluate_audience(posting(**fields), policy).status == expected


def test_company_language_uses_confirmed_employer_or_explicit_statement() -> None:
    policy = PublicationPolicy.model_validate(
        {
            "company_languages": ["ru"],
            "verified_employers": {
                "Example": {
                    "languages": ["ru"],
                    "source": "https://example.org/careers",
                    "checked_on": "2026-11-25",
                }
            },
        }
    )
    assert evaluate_audience(posting(language="en"), policy).status == "allow"
    assert evaluate_audience(posting(company="Other", language="ru"), policy).status == "review"
    team = posting(company="Other", description="We are a Russian-speaking team.")
    assert evaluate_audience(team, policy).status == "review"
    assert (
        evaluate_audience(team, policy.model_copy(update={"accept_team_language": True})).status
        == "allow"
    )
    assert (
        evaluate_audience(
            posting(company="Other", description="Our company working language is Russian."), policy
        ).status
        == "allow"
    )


def test_mandatory_applicant_geography_even_when_post_is_russian() -> None:
    policy = PublicationPolicy(
        eligible_countries=("KZ",), require_country=True, posting_languages=("ru",)
    )
    remote = posting(language="ru", work_mode="remote", country="KZ")
    assert evaluate_audience(remote, policy).status == "review"
    us_only = remote.model_copy(update={"remote_restrictions": "US only"})
    assert evaluate_audience(us_only, policy).status == "skip"
    kz = remote.model_copy(update={"remote_restrictions": "KZ only"})
    assert evaluate_audience(kz, policy).status == "allow"


@pytest.mark.parametrize(
    ("family", "expected"),
    [
        ("software_development", "developer"),
        ("ai/ml", "data_ml"),
        ("cloud architecture", "architect"),
        ("quality engineering", "qa"),
        ("unclassified specialist", None),
    ],
)
def test_catalog_groups_production_role_spellings(family: str, expected: str | None) -> None:
    assert publication_facts(posting(role_family=family, title="Specialist")).role == expected


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("Для нашої команди ми шукаємо розробника з досвідом роботи.", "uk"),
        ("Біз жұмыс үшін маман іздейміз. Біздің талаптар мен міндеттері.", "kk"),
        ("Biz jamoamizga dasturchi izlaymiz. Ish uchun tajriba kerak.", "uz"),
        (
            "AI Developer | Cyprus. Международный IT-холдинг ищет разработчика для создания продуктов. Опыт работы в команде.",
            "ru",
        ),
        ("Описание: We are looking for an engineer to join our team.", "en"),
    ],
)
def test_original_posting_language_overrides_legacy_script_guess(body: str, code: str) -> None:
    assert publication_facts(posting(language="ru", description_raw=body)).languages == (code,)


@pytest.mark.asyncio
async def test_review_is_scoped_and_policy_change_cannot_redeliver() -> None:
    store = InMemoryStore()
    job = posting(job_id="hirify-1479323", language="en")
    await record_publish_candidate(store, job)
    sender = SimpleNamespace(
        send=AsyncMock(return_value=DeliveryReceipt("@jobs", 5447, datetime.now(UTC)))
    )
    policy = PublicationPolicy(company_languages=("ru",))
    result = await publish_jobs(
        [job], target="@jobs", sender=sender, policy=policy, store=store, send_limit=1
    )
    assert result.terminal_skip_reasons == {"audience_review": 1}
    sender.send.assert_not_awaited()
    assert not await pending_candidate_ids(store, "@jobs", policy_version=policy.version)
    changed_post = job.model_copy(update={"description_raw": "Updated vacancy details in English."})
    await record_publish_candidate(store, changed_post)
    assert await pending_candidate_ids(store, "@jobs", policy_version=policy.version) == {
        job.job_id
    }
    await publish_jobs(
        [changed_post], target="@jobs", sender=sender, policy=policy, store=store, send_limit=1
    )
    assert not await pending_candidate_ids(store, "@jobs", policy_version=policy.version)
    assert await pending_candidate_ids(store, "@other", policy_version=policy.version) == {
        job.job_id
    }
    shadow = policy.model_copy(update={"shadow": True})
    assert await pending_candidate_ids(store, "@jobs", policy_version=shadow.version) == {
        job.job_id
    }
    result = await publish_jobs(
        [job],
        target="@jobs",
        sender=sender,
        policy=shadow,
        store=store,
        send_limit=1,
        throttle_seconds=0,
    )
    assert result.sent == 1
    changed = PublicationPolicy(posting_languages=("en",))
    result = await publish_jobs(
        [job], target="@jobs", sender=sender, policy=changed, store=store, send_limit=1
    )
    assert result.skipped_already_published == 1
    sender.send.assert_awaited_once()


def test_conflicting_group_locations_agree_across_card_and_public_facts() -> None:
    group = merge_jobs(
        [
            posting(city="Москва", country="Россия"),
            posting(
                source_kind="telegram_channel",
                location="Астана, Казахстан",
                seniority="senior",
                role_family="analyst",
                language="ru",
                work_mode="remote",
                remote_restrictions="KZ only",
            ),
        ]
    )
    facts = publication_facts(group)
    card = build_card(group)
    assert facts.country is None and facts.city is None and card.geo is None
    assert card.hashtags == "#senior #аналитик #язык_ru #remote"
    assert evaluate_audience(group, PublicationPolicy(eligible_countries=("KZ",))).status == "allow"


@pytest.mark.asyncio
async def test_catalog_filters_before_limit_and_invalidates_cached_policy() -> None:
    policies = {"site": PublicationPolicy(eligible_countries=("KZ",))}
    jobs = [posting(country="US")] * 2000 + [posting(country="KZ", language="kk")]

    class Runner:
        def get_runtime(self, tenant_id: str) -> object:
            return SimpleNamespace(tenant=SimpleNamespace(publication_policies=policies))

        async def latest_jobs(self, tenant_id: str, *, limit: int) -> list[JobRecord]:
            return jobs[:limit]

    cache = PublicJobsCache()
    api = await build_public_jobs_response(Runner(), "ai_jobs", limit=1, cache=cache)
    assert api["jobs"][0]["country"] == "Казахстан"
    assert api["jobs"][0]["publicationTags"] == ["#staff", "#менеджер", "#казахстан", "#язык_kk"]
    policies["site"] = PublicationPolicy(eligible_countries=("US",))
    api = await build_public_jobs_response(Runner(), "ai_jobs", limit=1, cache=cache)
    assert api["jobs"][0]["country"] == "США"
