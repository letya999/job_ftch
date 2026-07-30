"""Tests for structure_detector."""

from job_ftch.infrastructure.sources.structure_detector import (
    extract_structured_fields,
    is_structured_vacancy,
)


class TestExtractStructuredFields:
    def test_russian_template(self):
        text = (
            "Вакансия: ML Engineer\n"
            "Компания: Яндекс\n"
            "ЗП: от 400 000 руб\n"
            "Локация: Москва\n"
            "Обязанности:\n"
            "- Разработка моделей\n"
        )
        fields = extract_structured_fields(text)
        assert fields["title"] == "ML Engineer"
        assert fields["company"] == "Яндекс"
        assert fields["salary"] == "от 400 000 руб"
        assert fields["location"] == "Москва"
        assert fields["description_marker"] == "true"

    def test_english_template(self):
        text = (
            "Position: Senior Backend Developer\n"
            "Company: Stripe\n"
            "Salary: $180k-$250k\n"
            "Location: Remote\n"
            "Requirements:\n"
            "- 5+ years experience\n"
        )
        fields = extract_structured_fields(text)
        assert fields["title"] == "Senior Backend Developer"
        assert fields["company"] == "Stripe"
        assert fields["salary"] == "$180k-$250k"
        assert fields["location"] == "Remote"

    def test_freeform_text_returns_empty(self):
        text = "Привет всем! Кто-нибудь знает хорошие курсы по ML? Хочу сменить профессию."
        fields = extract_structured_fields(text)
        assert len(fields) < 3

    def test_partial_structure(self):
        text = "Вакансия: Data Analyst\nОпыт от 3 лет"
        fields = extract_structured_fields(text)
        assert "title" in fields
        assert len(fields) == 1


class TestIsStructuredVacancy:
    def test_structured_returns_true(self):
        text = "Должность: AI Engineer\nКомпания: OpenAI\nЗарплата: $300k\nГород: San Francisco\n"
        is_struct, fields = is_structured_vacancy(text)
        assert is_struct is True
        assert fields["title"] == "AI Engineer"

    def test_unstructured_returns_false(self):
        text = "Just chatting about tech and AI stuff"
        is_struct, fields = is_structured_vacancy(text)
        assert is_struct is False
