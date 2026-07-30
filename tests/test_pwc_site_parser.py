import pytest

from job_ftch.domain.source_spec import CareerSiteSpec
from job_ftch.infrastructure.sources.site_parsers.pwc import PwcParser


@pytest.mark.asyncio
async def test_pwc_discovers_workday_postings() -> None:
    class _Response:
        status_code = 200
        text = """<a href="https://pwc.wd3.myworkdayjobs.com/Global_Experienced_Careers/job/Warszawa/AI-Engineer_580549WD/apply">AI</a>"""

        def raise_for_status(self) -> None:
            pass

    class _Client:
        async def get(self, *args: object, **kwargs: object) -> _Response:
            return _Response()

    client = _Client()
    urls = await PwcParser().discover(
        CareerSiteSpec(url="https://www.pwc.pl/pl/kariera.html"), client
    )
    assert urls == [
        "https://pwc.wd3.myworkdayjobs.com/Global_Experienced_Careers/job/Warszawa/AI-Engineer_580549WD"
    ]
