import pytest

from job_ftch.domain import RiskLevel
from job_ftch.nodes.risk import RiskScoringNode


@pytest.mark.anyio
async def test_risk_crypto_token_detected(make_job_record):
    node = RiskScoringNode()
    job = make_job_record(description="Work in our crypto startup " * 10)
    processed = await node.process(job)
    assert "suspicious_domain" in processed.risk_signals


@pytest.mark.anyio
async def test_risk_telegram_dm_no_url_detected(make_job_record):
    node = RiskScoringNode()
    job = make_job_record(description="Contact @recruiter via Telegram DM " * 5, canonical_url=None)
    processed = await node.process(job)
    assert "contact_only_apply_flow" in processed.risk_signals


@pytest.mark.anyio
async def test_risk_low_information_density(make_job_record):
    node = RiskScoringNode()
    job = make_job_record(description="Too short")
    processed = await node.process(job)
    assert "low_information_density" in processed.risk_signals


@pytest.mark.anyio
async def test_risk_high_score_triggers_review_reason(make_job_record):
    node = RiskScoringNode(review_threshold=0.4)
    # suspicious_domain, contact_only_apply_flow, low_information_density
    job = make_job_record(description="crypto telegram dm", canonical_url=None)
    processed = await node.process(job)
    assert processed.risk_score >= 0.4
    assert "high_risk_signals" in processed.review_reasons


@pytest.mark.anyio
async def test_risk_clean_job_has_no_signals(make_job_record):
    node = RiskScoringNode()
    job = make_job_record(
        description="Standard ML job description " * 10, canonical_url="https://ok.com"
    )
    processed = await node.process(job)
    assert len(processed.risk_signals) == 0
    assert processed.risk_score == 0.0


@pytest.mark.anyio
async def test_risk_level_classification(make_job_record):
    node = RiskScoringNode()
    # 1 signal -> 0.2 -> LOW
    job1 = make_job_record(description="crypto " * 20, canonical_url="https://ok.com")
    res1 = await node.process(job1)
    assert res1.risk_level == RiskLevel.LOW

    # 2 signals -> 0.4 -> MEDIUM
    job2 = make_job_record(
        description="crypto ", canonical_url="https://ok.com"
    )  # suspicious_domain, low_info
    res2 = await node.process(job2)
    assert res2.risk_level == RiskLevel.MEDIUM

    # 4 signals -> 0.8 -> HIGH
    job4 = make_job_record(
        description="crypto telegram dm", canonical_url=None, risk_signals=("pre_existing",)
    )
    res4 = await node.process(job4)
    assert res4.risk_level == RiskLevel.HIGH
