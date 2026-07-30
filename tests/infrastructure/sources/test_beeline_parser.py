from __future__ import annotations

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.beeline import BeelineUzParser


def test_beeline_parser_builds_safe_detail_item() -> None:
    vacancy = {
        "id": 566,
        "name": "<strong>Sales specialist</strong>",
        "slug": "sales-specialist",
        "created_at": "2026-02-20T11:19:50.653295",
        "content": "<p>Build strong customer relationships.</p>",
        "description": {
            "regionTitle": "Tashkent",
            "responsibilities": "<ul><li>Sell services</li></ul>",
            "requirements": "<p>One year of experience</p>",
            "conditions": "<p>Medical insurance</p>",
            "hr_email": "not-in-output@example.test",
        },
        "creator": {"password": "must-not-leak"},  # pragma: allowlist secret
    }

    item = BeelineUzParser._item_from_payload(
        vacancy,
        CareerSiteSpec(url="https://beeline.uz/ru/vacancies", source_name="beeline-test"),
    )

    assert item is not None
    assert item.external_id == "566"
    assert str(item.url) == "https://beeline.uz/ru/vacancies/sales-specialist"
    assert item.metadata["location"] == "Tashkent"
    assert item.metadata["detail_vacancy_confirmed"] is True
    assert "Sell services" in item.text
    assert "must-not-leak" not in item.text
    assert "not-in-output@example.test" not in item.text
