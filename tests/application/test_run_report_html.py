import pytest

from job_ftch.adapters.telegram_bot.main import _send_scheduler_run_report
from job_ftch.application.run_report import build_runtime_run_report, render_runtime_run_report_text


def test_runtime_report_escapes_dynamic_html() -> None:
    summary = type(
        "Summary",
        (),
        {"source_failures": [{"source_name": '<future> & "jobs"', "error": "bad <tag> & timeout"}]},
    )()

    text = render_runtime_run_report_text(build_runtime_run_report(summary, duration_seconds=1))

    assert "<future>" not in text
    assert "&lt;future&gt; &amp; &quot;jobs&quot;" in text
    assert "bad &lt;tag&gt; &amp; timeout" in text


@pytest.mark.asyncio
async def test_scheduler_owner_report_failure_is_persisted_without_raising() -> None:
    class Store:
        def __init__(self) -> None:
            self.state: dict[str, str] = {}

        async def set_run_state(self, key: str, value: str) -> None:
            self.state[key] = value

    class Bot:
        async def send_message(self, *_: object, **__: object) -> None:
            raise RuntimeError('bad <future> & "source"')

    store = Store()
    await _send_scheduler_run_report(
        Bot(),
        store=store,
        tenant_id="ai_jobs",
        publish_user_id="42",
        run_result=type("Summary", (), {})(),
        duration_seconds=1,
        channel_sent=3,
    )

    assert store.state["bot_scheduler:last_owner_report_attempt_at"]
    assert store.state["bot_scheduler:last_owner_report_error"] == 'bad <future> & "source"'
    assert "bot_scheduler:last_owner_report_success_at" not in store.state
