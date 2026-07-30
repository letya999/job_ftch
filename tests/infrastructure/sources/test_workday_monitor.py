import pytest

from job_ftch.infrastructure.sources.monitors.workday import can_handle


@pytest.mark.asyncio
async def test_workday_can_handle_finds_late_marketing_page_link() -> None:
    class _Response:
        status_code = 200
        content = (b"x" * 7_000) + b"https://sabre.wd1.myworkdayjobs.com/SabreJobs"
        encoding = "utf-8"

        def raise_for_status(self) -> None:
            pass

    class _Client:
        async def get(self, *args: object, **kwargs: object) -> _Response:
            return _Response()

    assert await can_handle("https://www.sabre.com/careers/", _Client()) == {
        "company": "sabre",
        "wd_instance": "wd1",
        "site": "SabreJobs",
    }
