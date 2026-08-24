from __future__ import annotations

import pytest

from job_ftch.application.registry import resolve_site_parser

MAJOR_EMPLOYER_SITES = (
    ("Yandex", "https://yandex.ru/jobs/vacancies"),
    ("Sber", "https://rabota.sber.ru/search"),
    ("Ozon Tech", "https://ozon.tech/vacancies"),
    ("Ozon Career", "https://career.ozon.ru/"),
    ("Alfa-Bank", "https://digital.alfabank.ru/vacancies"),
    ("Wildberries", "https://www.wildberries.ru/services/trudoustroystvo"),
    ("CIAN", "https://www.cian.ru/vacancies/"),
    ("T-Bank", "https://www.tbank.ru/career/vacancies/it/"),
    ("VK", "https://team.vk.company/vacancy/"),
    ("Avito", "https://career.avito.com/vacancies/"),
    ("Getmatch", "https://getmatch.ru/vacancies"),
    ("Hirify", "https://hirify.me/jobs-in-russia"),
    ("YADRO", "https://careers.yadro.com/vacancies"),
    ("T1", "https://career.t1.ru/"),
    ("Innotech", "https://inno.tech/ru/company/contacts/"),
    ("VTB", "https://rabota.vtb.ru/career-it/"),
    ("MTS", "https://job.mts.ru/"),
    ("Rostelecom", "https://job.rt.ru/search"),
    ("MegaFon", "https://career.megafon.ru/"),
    ("Positive Technologies", "https://ptsecurity.com/about/vacancy/"),
    ("Kaspersky", "https://careers.kaspersky.ru/"),
    ("Kontur", "https://kontur.ru/career"),
    ("1C", "https://1c.ru/rus/firm1c/vacan/search?direction=4"),
    ("Astra Group", "https://astra.ru/about/career/vacancies/"),
    ("Selectel", "https://selectel.ru/careers/all/"),
    ("X5 Tech", "https://rabota.x5.ru/vacancies?vacancy_categories=it"),
    ("Lamoda", "https://www.lamoda.ru/career/"),
    ("CSBI", "https://csbi.ru/job/"),
    ("CASIB", "https://casib.eu/ru/in/news/"),
    ("Kaspi.kz", "https://job.kaspi.kz/search"),
    ("Kolesa Group", "https://kolesa.group/career/job"),
    ("Halyk Bank", "https://halykbank.kz/index.php/about/career/vacancies"),
    ("Freedom", "https://job.freedomholdingcorp.com/"),
    ("ForteBank", "https://career.forte.kz/"),
    ("Beeline Kazakhstan", "https://people.beeline.kz/"),
    ("HireMe", "https://hireme.kz/"),
    ("Astana Hub", "https://astanahub.com/ru/vacancy/"),
    ("Qyzmet", "https://qyzmet.kz/"),
    ("Yandex Uzbekistan", "https://yandex.ru/jobs/vacancies/city_tashkent"),
    ("Uzum", "https://people.uzum.com/career/ru/vacancies"),
    ("Click", "https://click.uz/ru/vacancies"),
    ("TBC Uzbekistan", "https://tbcbank.uz/career"),
    ("Beeline Uzbekistan", "https://beeline.uz/ru/vacancies"),
    ("Payme", "https://career.payme.uz/"),
    ("UzJobs", "https://uzjobs.uz/r/vakansy.html"),
    ("HireFi", "https://hirefi.io/"),
)


@pytest.mark.parametrize(("employer", "url"), MAJOR_EMPLOYER_SITES)
def test_every_major_employer_has_a_registered_special_parser(employer: str, url: str) -> None:
    parser = resolve_site_parser(url)
    assert parser is not None, employer
    assert callable(parser.runtime_defaults), employer
    assert parser.__class__.__module__.startswith("job_ftch.infrastructure.sources.site_parsers"), (
        employer
    )
