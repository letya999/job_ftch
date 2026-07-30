from job_ftch.infrastructure.sources.site_parsers.nokia import _DETAIL_URL


def test_nokia_parser_keeps_only_oracle_job_details() -> None:
    assert _DETAIL_URL.match("https://jobs.nokia.com/de/job/20886")
    assert not _DETAIL_URL.match("https://jobs.nokia.com/de/sites/CX_1")
