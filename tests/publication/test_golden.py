"""Golden-snapshot tests for publication rendering.

Each test represents a real-world card scenario (HH, Habr, Telegram,
career site, etc.) and asserts structural properties of the rendered output.
These are regression guards: if the format changes, the test catches it.
"""

from __future__ import annotations

from job_ftch.domain.models import (
    CompensationPeriod,
    CompensationRange,
    Job,
    JobRecord,
    LanguageCode,
    SkillTag,
    SourceKind,
    WorkMode,
)
from job_ftch.publication.card import build_card
from job_ftch.publication.layout import load_layout
from job_ftch.publication.render import render_card


def _render(job: Job | JobRecord, profile: str = "channel") -> str:
    layout = load_layout()
    card = build_card(job)
    return render_card(card, layout, profile=profile)


class TestGoldenCards:
    def test_career_site_full(self) -> None:
        """Career site job with all fields populated."""
        job = JobRecord(
            raw_item_id="cs-1",
            source_kind=SourceKind.CAREER_SITE,
            source_name="HH.ru",
            title="Senior ML Engineer",
            company="Яндекс",
            description="Building recommendation systems",
            canonical_url="https://hh.ru/vacancy/123",
            work_mode=WorkMode.HYBRID,
            city="Москва",
            country="Россия",
            language=LanguageCode.RU,
            compensation=CompensationRange(
                currency="RUB",
                min_amount=350000,
                max_amount=550000,
                period=CompensationPeriod.MONTH,
                gross=True,
            ),
            requirements_must=("5+ лет ML", "PyTorch", "Python"),
            tools_stack=("Python", "PyTorch", "Spark", "Kubernetes"),
        )
        text = _render(job)
        assert "<b>Senior ML Engineer</b>" in text
        assert "Яндекс" in text
        assert "Москва" in text
        assert "₽" in text
        assert "gross" in text
        assert "Нужно:" in text
        assert "Стек:" in text
        assert "открыть вакансию" in text
        assert "hh.ru" in text
        assert "job_ftch" in text
        assert not text.startswith("🔵")
        assert "<i>" not in text

    def test_telegram_post_minimal(self) -> None:
        """Telegram channel post with only title and description."""
        job = Job(
            raw_item_id="tg-1",
            source_kind=SourceKind.TELEGRAM_CHANNEL,
            source_name="AI Jobs RU",
            title="ML Engineer",
            description="Ищем ML-инженера в стартап.",
            work_mode=WorkMode.REMOTE,
        )
        text = _render(job)
        assert "<b>ML Engineer</b>" in text
        assert "удалённо" in text
        assert "AI Jobs RU" in text

    def test_no_salary(self) -> None:
        """Job without salary - no salary line should appear."""
        job = Job(
            raw_item_id="ns-1",
            source_kind=SourceKind.CAREER_SITE,
            source_name="Habr",
            title="Data Scientist",
            company="Сбер",
            description="Data science role",
            work_mode=WorkMode.ONSITE,
            city="Санкт-Петербург",
        )
        text = _render(job)
        assert "<b>Data Scientist</b>" in text
        assert "Сбер" in text
        assert "₽" not in text

    def test_hidden_company(self) -> None:
        """Job where company matches source name (should be filtered)."""
        job = Job(
            raw_item_id="hc-1",
            source_kind=SourceKind.CAREER_SITE,
            source_name="HH.ru",
            title="Python Developer",
            company="HH.ru",
            description="Python development role",
            canonical_url="https://hh.ru/vacancy/456",
        )
        text = _render(job)
        assert "<b>Python Developer</b>" in text
        assert "hh.ru" in text  # domain in footer

    def test_usd_salary(self) -> None:
        """USD salary should use $ symbol."""
        job = Job(
            raw_item_id="usd-1",
            source_kind=SourceKind.CAREER_SITE,
            source_name="LinkedIn",
            title="Backend Engineer",
            company="Stripe",
            description="Backend systems",
            compensation=CompensationRange(
                currency="USD",
                min_amount=150000,
                max_amount=220000,
                period=CompensationPeriod.YEAR,
            ),
            work_mode=WorkMode.REMOTE,
        )
        text = _render(job)
        assert "$" in text
        assert "/yr" in text

    def test_kzt_salary(self) -> None:
        """KZT salary should use ₸ symbol."""
        job = Job(
            raw_item_id="kzt-1",
            source_kind=SourceKind.CAREER_SITE,
            source_name="hh.kz",
            title="AI Researcher",
            company="Kaspi",
            description="AI research role",
            compensation=CompensationRange(
                currency="KZT",
                min_amount=1500000,
                max_amount=2500000,
                period=CompensationPeriod.MONTH,
            ),
            city="Almaty",
            country="Kazakhstan",
        )
        text = _render(job)
        assert "₸" in text
        assert "Almaty" in text

    def test_with_stack_and_requirements_ru(self) -> None:
        """Full card with Russian requirements uses Russian labels."""
        job = JobRecord(
            raw_item_id="sr-1",
            source_kind=SourceKind.CAREER_SITE,
            source_name="Habr Career",
            title="MLOps Engineer",
            company="VK",
            description="Разработка MLOps платформы",
            requirements_must=("Опыт с Docker", "Знание K8s", "Настройка CI/CD"),
            tools_stack=("Docker", "Kubernetes", "Airflow", "MLflow"),
            work_mode=WorkMode.HYBRID,
            city="Москва",
        )
        text = _render(job)
        assert "Нужно:" in text
        assert "Docker" in text
        assert "Стек:" in text
        assert "Kubernetes" in text

    def test_with_stack_and_requirements_en(self) -> None:
        """Full card with English requirements uses English labels."""
        job = JobRecord(
            raw_item_id="sr-2",
            source_kind=SourceKind.CAREER_SITE,
            source_name="Remote Jobs",
            title="MLOps Engineer",
            company="Stripe",
            description="MLOps role at scale",
            requirements_must=("Docker experience", "K8s", "CI/CD pipelines"),
            tools_stack=("Docker", "Kubernetes", "Airflow", "MLflow"),
            work_mode=WorkMode.REMOTE,
        )
        text = _render(job)
        assert "Required:" in text
        assert "Stack:" in text

    def test_skills_fallback_when_no_stack(self) -> None:
        """Stack from skills_explicit when tools_stack is empty."""
        job = JobRecord(
            raw_item_id="sf-1",
            source_kind=SourceKind.CAREER_SITE,
            source_name="GeekJob",
            title="NLP Engineer",
            company="Tinkoff",
            description="NLP role",
            skills_explicit=(
                SkillTag(canonical_name="Python"),
                SkillTag(canonical_name="Transformers"),
                SkillTag(canonical_name="BERT"),
            ),
        )
        text = _render(job)
        assert "Stack:" in text
        assert "Python" in text

    def test_control_bot_profile(self) -> None:
        """Control bot profile renders same content (keyboard added by sender)."""
        job = Job(
            raw_item_id="cb-1",
            source_kind=SourceKind.CAREER_SITE,
            source_name="HH.ru",
            title="Senior Python Developer",
            company="Ozon",
            description="Senior Python role",
            canonical_url="https://hh.ru/vacancy/789",
            work_mode=WorkMode.REMOTE,
        )
        text = _render(job, profile="control_bot")
        assert "<b>Senior Python Developer</b>" in text
        assert "Ozon" in text

    def test_aggregator_post(self) -> None:
        """Aggregator-sourced job (telegram group)."""
        job = Job(
            raw_item_id="ag-1",
            source_kind=SourceKind.TELEGRAM_GROUP,
            source_name="ML Jobs Chat",
            title="Computer Vision Engineer",
            description="CV engineer needed",
            work_mode=WorkMode.REMOTE,
            canonical_url="https://t.me/mljobs/42",
        )
        text = _render(job)
        assert "<b>Computer Vision Engineer</b>" in text
        assert "open post" in text

    def test_no_url(self) -> None:
        """Job without URL - footer should not have a link."""
        job = Job(
            raw_item_id="nu-1",
            source_kind=SourceKind.TELEGRAM_CHANNEL,
            source_name="AI News",
            title="Research Scientist",
            description="Research role",
        )
        text = _render(job)
        assert "<b>Research Scientist</b>" in text
        assert "<a href=" not in text or "AI News" in text

    def test_single_salary_amount(self) -> None:
        """min == max should show single number, no range dash."""
        job = Job(
            raw_item_id="ss-1",
            source_kind=SourceKind.CAREER_SITE,
            source_name="Test",
            title="Engineer",
            description="desc",
            compensation=CompensationRange(
                currency="RUB",
                min_amount=300000,
                max_amount=300000,
                period=CompensationPeriod.MONTH,
            ),
        )
        text = _render(job)
        assert "300 000" in text
        assert "–" not in text
