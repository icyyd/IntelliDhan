"""Contract tests for blind research specialists and deterministic reconciliation."""

from intellidhan_gateway.research_consensus import build_research_consensus


def analysis(net_vote=4, validation="OUTPERFORMS_BASE", volatility=25):
    return {
        "consensus": {"label": "STRONG_UPTREND", "net_vote": net_vote},
        "risk": {"realized_volatility_20d_annualized_pct": volatility},
        "forecast": {
            "strategy_context_status": "VALIDATED_CONTEXT",
            "horizons": {
                "one_month": {"walk_forward_validation": {"status": validation}}
            },
        },
    }


def intelligence(fundamental_score=75, news=72, social=68, coverage=5):
    return {
        "fundamentals": {
            "score": fundamental_score,
            "coverage": coverage,
            "metrics": [
                {"key": "revenue_growth", "value": 12},
                {"key": "gross_margin", "value": 55},
                {"key": "net_margin", "value": 18},
                {"key": "fcf_margin", "value": 16},
                {"key": "liabilities_to_assets", "value": 40},
            ][:coverage],
        },
        "news": {"score": news, "article_count": 4},
        "social": {"score": social, "mentions": 100},
    }


def test_multibrain_buy_requires_price_and_business_agreement():
    result = build_research_consensus(analysis(), intelligence())
    assert result["posture"] == "BUY"
    assert result["status"] == "RESEARCH_ONLY"
    assert [item["key"] for item in result["specialists"]] == [
        "PRICE_RISK",
        "BUSINESS_QUALITY",
        "CATALYST_ATTENTION",
    ]
    assert "cannot rank" in result["policy"]


def test_social_enthusiasm_cannot_override_weak_price_and_business():
    result = build_research_consensus(
        analysis(net_vote=-4), intelligence(fundamental_score=25, news=100, social=100)
    )
    assert result["posture"] == "HOLD"
    assert result["agreement"]["conflict"] is True


def test_missing_filed_business_evidence_fails_closed():
    result = build_research_consensus(analysis(), intelligence(coverage=1))
    assert result["posture"] == "INSUFFICIENT_EVIDENCE"
    assert result["critical_blockers"]


def test_high_volatility_reduces_technical_conviction():
    normal = build_research_consensus(analysis(net_vote=2), intelligence())
    stressed = build_research_consensus(analysis(net_vote=2, volatility=100), intelligence())
    assert normal["specialists"][0]["score"] > stressed["specialists"][0]["score"]

