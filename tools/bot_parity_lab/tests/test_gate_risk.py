from __future__ import annotations

from paritylab.gate_risk import assess_live_gate_risk
from paritylab.models import ProbeRecord, SessionState


def test_live_gate_risk_extracts_only_observed_positive_signals() -> None:
    state = SessionState(
        session_id="session-a",
        client_name="test",
        client_family="test",
        expected_failure=False,
        gate_enabled=True,
        created_at="2026-08-05T00:00:00+00:00",
    )
    state.probes.extend(
        [
            ProbeRecord(
                session_id=state.session_id,
                observed_at=state.created_at,
                realm="window",
                sequence=1,
                data={
                    "runtime": {"userAgent": "HeadlessChrome/140", "webdriver": True},
                    "rendering": {"renderer": "ANGLE (SwiftShader)"},
                },
            ),
            ProbeRecord(
                session_id=state.session_id,
                observed_at=state.created_at,
                realm="vendor:botd",
                sequence=1,
                data={"result": {"bot": True, "botKind": "headless_chrome"}},
            ),
        ]
    )

    risk = assess_live_gate_risk(state)

    assert set(risk.hard_codes) == {
        "CAT_WEBGL_SWIFTSHADER",
        "JS_HEADLESS_UA",
        "JS_WEBDRIVER_TRUE",
        "VENDOR_BOTD_AUTOMATION",
    }
    assert risk.medium_codes == ()
