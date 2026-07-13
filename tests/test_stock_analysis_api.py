"""Endpoint contract tests for on-demand stock analysis."""

from fastapi.testclient import TestClient

import intellidhan_gateway.app as gateway


class FakeAnalyzer:
    def __init__(self):
        self.calls = []
        self.error = None

    async def analyze(self, symbol, **kwargs):
        self.calls.append((symbol, kwargs))
        if self.error:
            raise self.error
        return {
            "symbol": symbol.upper(),
            "request": kwargs,
            "consensus": {"label": "MIXED"},
        }


def test_analysis_endpoint_parses_and_forwards_public_parameters(monkeypatch):
    analyzer = FakeAnalyzer()
    monkeypatch.setattr(gateway, "stock_analyzer", analyzer)
    response = TestClient(gateway.app).get(
        "/api/analyze/aapl",
        params={
            "years": 3,
            "risk_budget": 500,
            "include_backtest": False,
            "cost_bps": 25,
        },
    )
    assert response.status_code == 200
    assert response.json()["symbol"] == "AAPL"
    assert analyzer.calls == [
        (
            "aapl",
            {
                "years": 3,
                "risk_budget": 500.0,
                "include_backtest": False,
                "cost_bps": 25.0,
            },
        )
    ]


def test_analysis_endpoint_rejects_out_of_contract_query_before_provider(monkeypatch):
    analyzer = FakeAnalyzer()
    monkeypatch.setattr(gateway, "stock_analyzer", analyzer)
    response = TestClient(gateway.app).get("/api/analyze/AAPL?years=1&cost_bps=101")
    assert response.status_code == 422
    assert analyzer.calls == []


def test_analysis_endpoint_maps_missing_history_to_not_found(monkeypatch):
    analyzer = FakeAnalyzer()
    analyzer.error = LookupError("no completed adjusted daily bars found for BAD")
    monkeypatch.setattr(gateway, "stock_analyzer", analyzer)
    response = TestClient(gateway.app).get("/api/analyze/BAD")
    assert response.status_code == 404
    assert response.json()["detail"] == "no completed adjusted daily bars found for BAD"
