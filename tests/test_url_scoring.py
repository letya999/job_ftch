from __future__ import annotations

from job_ftch.infrastructure.sources.career_site_source import _rank_detail_urls
from job_ftch.infrastructure.sources.monitors.dom import (
    _auto_listing_candidates,
    extract_static_job_links,
)
from job_ftch.infrastructure.sources.url_scoring import (
    is_same_site_family,
    looks_like_listing_url,
    score_job_url,
)


def test_extract_static_job_links_prefers_hirify_job_details() -> None:
    html = """
    <html><body>
      <a href="/jobs">All jobs</a>
      <a href="/company/about">About</a>
      <a href="/jobs/1234-senior-python-engineer">Senior Python Engineer</a>
    </body></html>
    """

    links = extract_static_job_links(html, "https://hirify.me", limit=3)

    assert links == ["https://hirify.me/jobs/1234-senior-python-engineer"]


def test_extract_static_job_links_rejects_hirify_category_pages() -> None:
    html = """
    <html><body>
      <a href="/ai-engineering-jobs">AI engineering jobs</a>
      <a href="/web3-crypto-jobs">Web3 jobs</a>
    </body></html>
    """

    links = extract_static_job_links(html, "https://hirify.me", limit=3)

    assert links == []


def test_extract_static_job_links_filters_geekjob_content_noise() -> None:
    html = """
    <html><body>
      <a href="/jobs/648644">Senior ML Engineer</a>
      <a href="/content/forgeeks">Forgeeks</a>
    </body></html>
    """

    links = extract_static_job_links(html, "https://geekjob.ru", limit=3)

    assert links == ["https://geekjob.ru/jobs/648644"]


def test_rank_detail_urls_prefers_superjob_vacancy_detail_over_listing() -> None:
    urls = {
        "https://www.superjob.ru/vakansii/programmist.html",
        "https://www.superjob.ru/vakansii/senior-python-razrabotchik-54239872.html",
        "https://www.superjob.ru/clients/acme-123.html",
    }

    ranked = _rank_detail_urls(urls, "https://www.superjob.ru")

    assert ranked[0] == "https://www.superjob.ru/vakansii/senior-python-razrabotchik-54239872.html"
    assert "https://www.superjob.ru/clients/acme-123.html" not in ranked


def test_score_job_url_prefers_indeed_viewjob_over_company_page() -> None:
    viewjob = "https://www.indeed.com/viewjob?jk=abc123def456"
    company = "https://www.indeed.com/cmp/Acme/about"

    assert score_job_url(viewjob, board_url="https://www.indeed.com") > 0
    assert score_job_url(viewjob, board_url="https://www.indeed.com") > score_job_url(
        company, board_url="https://www.indeed.com"
    )


def test_rank_detail_urls_prefers_hireme_vacancy_page_over_listing_root() -> None:
    urls = {
        "https://hireme.kz/vacancies",
        "https://hireme.kz/vacancies/python-developer-4812",
    }

    ranked = _rank_detail_urls(urls, "https://hireme.kz/vacancies")

    assert ranked == ["https://hireme.kz/vacancies/python-developer-4812"]


def test_listing_detection_marks_job_openings_root_as_listing_not_detail() -> None:
    url = "https://hireme.kz/job-openings/"

    assert looks_like_listing_url(url, board_url="https://hireme.kz/") is True
    assert score_job_url(url, board_url="https://hireme.kz/") <= 0


def test_auto_listing_candidates_picks_listing_pages_from_homepage() -> None:
    urls = {
        "https://hireme.kz/job-openings/",
        "https://hireme.kz/company/about",
        "https://hireme.kz/",
    }

    assert _auto_listing_candidates(urls, "https://hireme.kz/") == {
        "https://hireme.kz/job-openings/"
    }


def test_score_job_url_rejects_cross_domain_noise_from_career_page() -> None:
    assert (
        score_job_url(
            "https://www.youtube.com/@kolesagroup6324/featured",
            board_url="https://kolesa.group/career/job",
        )
        < 0
    )
    assert is_same_site_family(
        "https://almaty.hh.kz/vacancy/123",
        board_url="https://hh.kz/search/vacancy",
    )


def test_score_job_url_rejects_learning_and_blog_pages() -> None:
    assert (
        score_job_url(
            "https://az.linkedin.com/learning/topics/career-development-5",
            board_url="https://az.linkedin.com/jobs",
        )
        < 0
    )
    assert (
        score_job_url(
            "https://job.mts.ru/blog/samye-vostrebovannye-it-professii-i-speczialnosti-v-2025-m",
            board_url="https://job.mts.ru/",
        )
        < 0
    )
    # A long article slug with a number must not bypass the negative
    # ``candidates`` word check and be mistaken for a detail page.
    assert (
        score_job_url(
            "https://itexpert.work/ru/100-kontaktov-kandidatov-besplatno-instrumenty-dlya-sorsing-steka-rekrutera/",
            board_url="https://itexpert.work/",
        )
        < 0
    )


def test_score_job_url_rejects_career_direction_page() -> None:
    assert (
        score_job_url(
            "https://career.avito.com/directions/data-science/",
            board_url="https://career.avito.com/vacancies/data-science/",
        )
        < 0
    )


def test_score_job_url_rejects_jobs_category_pages_without_stable_id() -> None:
    assert (
        score_job_url(
            "https://az.linkedin.com/jobs/accounting-jobs-almaty",
            board_url="https://az.linkedin.com/jobs",
        )
        < 0
    )


def test_score_job_url_rejects_tracking_redirect_api_links() -> None:
    assert (
        score_job_url(
            "https://content.hh1.az/api/v1/vacancy_of_the_day/click?vacancyId=131738624&contentId=0",
            board_url="https://hh1.az/",
        )
        < 0
    )


def test_score_job_url_rejects_marketplace_category_links() -> None:
    assert (
        score_job_url(
            "https://qyzmet.kz/catalog/bez-podgotovki",
            board_url="https://qyzmet.kz",
        )
        < 0
    )
    assert (
        score_job_url(
            "https://naimi.kz/almaty/service/blagoustoistvo-territorii",
            board_url="https://naimi.kz/",
        )
        < 0
    )


def test_score_job_url_rejects_talent_and_hub_pages() -> None:
    assert (
        score_job_url(
            "https://www.linkedin.com/talent/post-a-job",
            board_url="https://az.linkedin.com/jobs",
        )
        < 0
    )
    assert (
        score_job_url(
            "https://az.linkedin.com/hubs/top-colleges/",
            board_url="https://az.linkedin.com/jobs",
        )
        < 0
    )


def test_score_job_url_rejects_legal_policy_pages() -> None:
    assert (
        score_job_url(
            "https://az.linkedin.com/legal/cookie-policy?trk=homepage-jobseeker_auth-button_cookie-policy",
            board_url="https://az.linkedin.com/jobs",
        )
        < 0
    )
    assert (
        score_job_url(
            "https://www.linkedin.com/legal/privacy-policy",
            board_url="https://www.linkedin.com/jobs",
        )
        < 0
    )
    assert (
        score_job_url(
            "https://www.linkedin.com/psettings/guest-controls?trk=homepage-jobseeker_footer-guest-controls",
            board_url="https://www.linkedin.com/jobs",
        )
        < 0
    )


def test_score_job_url_rejects_cian_blogs_and_forum_slugs() -> None:
    board = "https://career.cian.ru/"
    blogs = "https://www.cian.ru/blogs-kak-snyat-kvartiru-345215/"
    forum = "https://www.cian.ru/forum-rieltorov-12345/"
    assert score_job_url(blogs, board_url=board) < 8
    assert score_job_url(blogs, board_url=board) < 0
    assert score_job_url(forum, board_url=board) < 8
    assert score_job_url(forum, board_url=board) < 0


def test_score_job_url_keeps_kadrof_and_aijobs_details() -> None:
    assert (
        score_job_url(
            "https://www.kadrof.ru/work/110953",
            board_url="https://www.kadrof.ru/work",
        )
        >= 8
    )
    assert (
        score_job_url(
            "https://aijobs.net/job/senior-ml-engineer-123",
            board_url="https://aijobs.net/jobs",
        )
        >= 8
    )


def test_extract_static_job_links_still_prefers_hirify_numbered_job() -> None:
    html = """
    <html><body>
      <a href="/blogs-office-news-345215">Office blog</a>
      <a href="/jobs/1234-senior-python-engineer">Senior Python Engineer</a>
    </body></html>
    """
    links = extract_static_job_links(html, "https://hirify.me", limit=3)
    assert links == ["https://hirify.me/jobs/1234-senior-python-engineer"]
