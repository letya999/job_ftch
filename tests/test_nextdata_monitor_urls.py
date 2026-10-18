from types import SimpleNamespace

import pytest

from job_ftch.infrastructure.sources.monitors import nextdata


@pytest.mark.asyncio
async def test_nextdata_uses_only_real_job_links(monkeypatch):
    async def fake_page(*args, **kwargs):
        return "<html>embedded</html>"

    monkeypatch.setattr(nextdata, "fetch_page_text", fake_page)
    monkeypatch.setattr(
        nextdata,
        "extract_embedded_json",
        lambda *_: {"jobs": [{"id": 12}, {"urlString": "/en/jobs/ai-engineer"}]},
    )
    spec = SimpleNamespace(
        url="https://staff.am/en/jobs",
        monitor_config={"path": "jobs"},
    )

    jobs = await nextdata.discover(spec, object())
    assert [job.url for job in jobs] == ["https://staff.am/en/jobs/ai-engineer"]
