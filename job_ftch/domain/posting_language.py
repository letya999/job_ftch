"""Lightweight posting-language evidence for installations without a language model."""

import re

from job_ftch.domain.models import LanguageCode

# Distinct words prevent a city, a stack of English product names or page chrome
# from establishing the language of the vacancy by itself.
_WORDS = {
    LanguageCode.RU: "и в на от для из по мы вы команда команду ищем опыт работы лет разработки разработчик который чем предстоит обязанности требуется условия компания вакансия гибрид удаленно удалённо данные данных стек офис зарплата оплата валюте релокация релокацией помогают",
    LanguageCode.EN: "the and for with of our you we is are will to an role experience years hiring join familiarity basic",
    LanguageCode.UK: "і та для на це у в з ми ви що як які досвід роботи шукаємо обов'язки пропонуємо розробки команда команду працювати офіс гібрид віддалено зарплата оплата валюті релокація",
    LanguageCode.KK: "және үшін бұл бар біз сіз жұмыс іздейміз тәжірибесі командаға бойынша талаптар білім біздің міндеттері",
    LanguageCode.UZ: "va uchun ish bilan biz siz tajriba ham bir bo'yicha jamoamizga bo'lishi talablar qilinadi kerak ishlash",
}
_MARKERS = {language: frozenset(words.split()) for language, words in _WORDS.items()}
_DISTINCTIVE = {
    LanguageCode.RU: {
        "удаленно",
        "удалённо",
        "гибрид",
        "обязанности",
        "требуется",
        "разработчик",
        "ищем",
    },
    LanguageCode.UK: {"віддалено", "гібрид", "шукаємо", "працювати"},
    LanguageCode.KK: {"іздейміз", "міндеттері", "талаптар"},
    LanguageCode.UZ: {"tajriba", "jamoamizga", "qilinadi"},
}


def posting_language(text: str) -> LanguageCode:
    words = set(
        re.findall(
            r"[^\W\d_]+(?:'[^\W\d_]+)?", text[:2800].casefold().replace("’", "'").replace("ʻ", "'")
        )
    )
    ranked = sorted(
        ((len(words & markers), language) for language, markers in _MARKERS.items()), reverse=True
    )
    score, language = ranked[0]
    if score >= 2 and score - ranked[1][0] >= 2:
        return language
    distinctive = [code for code, markers in _DISTINCTIVE.items() if markers & words]
    return distinctive[0] if len(distinctive) == 1 and ranked[1][0] <= 2 else LanguageCode.UNKNOWN
