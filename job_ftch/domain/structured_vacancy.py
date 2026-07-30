"""Regex-based structure detector for vacancy posts."""

from __future__ import annotations

import re

_FIELD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "title",
        re.compile(
            r"(?:^|\n)\s*(?:Должность|Позиция|Вакансия|Position|Role|Title|Job\s*Title)"
            r"\s*[:：]\s*(.+)",
            re.IGNORECASE,
        ),
    ),
    (
        "company",
        re.compile(
            r"(?:^|\n)\s*(?:Компания|Работодатель|Company|Employer|Организация|Organization)"
            r"\s*[:：]\s*(.+)",
            re.IGNORECASE,
        ),
    ),
    (
        "salary",
        re.compile(
            r"(?:^|\n)\s*(?:ЗП|Зарплата|Оклад|Salary|Compensation|Вилка)"
            r"\s*[:：]\s*(.+)",
            re.IGNORECASE,
        ),
    ),
    (
        "location",
        re.compile(
            r"(?:^|\n)\s*(?:Локация|Город|Формат|Location|City|Офис|Office|Регион)"
            r"\s*[:：]\s*(.+)",
            re.IGNORECASE,
        ),
    ),
    (
        "description_marker",
        re.compile(
            r"(?:^|\n)\s*(?:Описание|Обязанности|Задачи|Requirements|Responsibilities"
            r"|What you.ll do|Чем предстоит заниматься|Мы ожидаем|Мы предлагаем|Условия)"
            r"\s*[:：]",
            re.IGNORECASE,
        ),
    ),
]

MIN_FIELDS_FOR_STRUCTURED = 3


def extract_structured_fields(text: str) -> dict[str, str]:
    """Extract key-value fields from a vacancy-template text."""
    result: dict[str, str] = {}
    for field_name, pattern in _FIELD_PATTERNS:
        match = pattern.search(text)
        if match:
            if field_name == "description_marker":
                result[field_name] = "true"
            else:
                value = match.group(1).strip()
                if value:
                    result[field_name] = value
    return result


def is_structured_vacancy(text: str) -> tuple[bool, dict[str, str]]:
    """Check if text looks like a structured vacancy template."""
    fields = extract_structured_fields(text)
    return len(fields) >= MIN_FIELDS_FOR_STRUCTURED, fields
