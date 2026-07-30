"""Universal garbage prefilter logic."""

from __future__ import annotations

import re


def garbage_reason(text: str) -> str | None:
    head = text.strip()[:300].lower()
    lowered = text.strip().lower()
    scan = lowered[:1500]
    has_job_markers = any(
        s in scan
        for s in (
            "#вакансия",
            "#vacancy",
            "#job",
            "#hiring",
            "#ищу",
            "вакансия",
            "вакансии",
            "ищу ",
            "требуется",
            "ищем",
            "приглашаем",
            "должность",
            "позиция:",
            "responsibilities",
            "requirements",
            "we are looking",
            "hiring",
            "role:",
        )
    )
    has_service_signals = any(
        s in head
        for s in ("#помогу", "#разрабатываю", "разрабатываю кастомные", "создаю:", "под ключ")
    )
    is_hashtag_spam = bool(re.match(r"\s*(?:#\S+\s*){3,}", head))

    if (is_hashtag_spam and has_service_signals and not has_job_markers) or (
        has_service_signals and not has_job_markers
    ):
        return "freelancer service ad"
    if head.startswith("идея:") or "\nидея:" in head or "идея: разработать" in head:
        return "project spec"
    if "правила чата" in head or "правила группы" in head:
        return "channel rules"
    if not has_job_markers:
        if "курс" in scan and (
            "открыть курс" in scan or "скидка" in scan or "реклама" in scan or "erid" in scan
        ):
            return "course ad"
        if head.startswith(("http://", "https://")) and (
            "/blog/" in head or "blog" in head or "завезли" in scan
        ):
            return "external article"
        if (
            scan
            and len(scan) <= 240
            and not re.search(
                r"\b(engineer|developer|manager|architect|designer|аналитик|инженер|"
                r"разработчик|менеджер|архитектор|дизайнер)\b",
                scan,
            )
        ):
            return "short non-job chatter"
    return None
