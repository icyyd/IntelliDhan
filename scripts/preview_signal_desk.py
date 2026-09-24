#!/usr/bin/env python3
"""Read-only, synthetic browser fixtures for the beginner desk; never a trading server.

Run with the existing project Python environment. No real account, database,
provider, credential, execution module, or production gateway is loaded. All
prices, companies' metrics, headlines, and journal rows below are invented UI
test data. This server must never be used for market or trading decisions.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "web"
HOST = "127.0.0.1"
DEMO_NOTICE = (
    "DEMO · Synthetic UI test data only — not current prices, news, signals, or results. "
    "No real account, saving, sign-in, or trading is available. Analyze AAPL to preview research."
)

# Do not import intellidhan_gateway.app: that constructs the real service stack.
# Explicit paths also avoid an editable install pointing at a sibling worktree.
for source in (REPO / "shared-schemas", REPO / "services/gateway"):
    sys.path.insert(0, str(source))


def stamp(value: datetime) -> str:
    return value.isoformat()


def signal_fixture(symbol: str, now: datetime, *, stale: bool = False) -> tuple[dict, dict]:
    """A non-executable option-reference card and its display-only summary."""
    underlying = 200.0 if stale else 100.0
    direction = "DOWN" if stale else "UP"
    sign = -1 if stale else 1
    created = now - timedelta(minutes=75 if stale else 3)
    observed = now - timedelta(minutes=70 if stale else 2)
    alert_id = f"DEMO-{symbol.lower()}-research"
    alert = {
        "alert_id": alert_id, "plan_key": alert_id, "created_at": stamp(created),
        "module": "0DTE", "strategy": "EMA9_MTF_0DTE", "action": "BTO",
        "symbol": symbol, "underlying_price": underlying, "vehicle": "OPTION",
        "legs": [{
            "occ_symbol": f"DEMO-NOT-A-CONTRACT-{symbol}", "side": "BUY",
            "option_type": "PUT" if stale else "CALL", "strike": underlying,
            "expiry": now.date().isoformat(), "delta": None, "iv": None,
            "quote_source": "SYNTHETIC_UI_FIXTURE", "delta_source": None,
            "research_only": True,
        }],
        "equity_qty": None, "entry_limit": 1.20, "entry_zone": [1.15, 1.25],
        "stop_underlying": underlying - sign, "stop_est_vehicle": 0.90,
        "stop_rule": "DEMO price invalidation; this is not an order.",
        "take_profits": [{
            "zone_low": None, "zone_high": None, "underlying": underlying + sign * 2,
            "tranche": 1.0, "basis": "DEMO underlying target only",
        }],
        "contracts": 1, "capital_required": 120.0, "dollar_risk": 120.0,
        "reward_risk": 2.0, "budget_note": "DEMO amount, not personalized sizing.",
        "confidence": 0.0, "factors": {},
        "trend_matrix": {key: direction for key in ("5m", "15m", "1H", "D")},
        "thesis": "DEMO research card for visual testing only.",
        "invalidation": "No real market evidence supports this fixture.",
        "management": [], "risks": ["All data on this preview is synthetic."],
        "valid_until": stamp(created + timedelta(minutes=10)),
        "status": "SHADOW", "research_only": True, "evidence": None,
    }
    summary = {
        "version": "decision-summary-v1", "symbol": symbol, "horizon": "0DTE",
        "horizon_label": "DEMO · intraday research", "horizon_coverage": "RESEARCH_ONLY",
        "horizon_note": "Synthetic example. Same-day options can lose their full premium.",
        "horizons": [], "trend": direction, "thesis_direction": direction,
        "research_view": "WAIT", "next_step": "WAIT",
        "status": "STALE" if stale else "RESEARCH_ONLY",
        "headline": "DEMO · stale idea" if stale else "DEMO · research idea, not an entry",
        "reasons": [
            "DEMO: recorded timeframes illustrate an aligned trend; no market was observed.",
            "A trend is context, not permission to trade. The option contract is unverified.",
        ],
        "blockers": [
            "DEMO: this cached example is stale. Wait for a verified update."
            if stale else "DEMO: this method lacks qualified execution evidence.",
            "Fixture prices and quantities cannot be used to place an order.",
        ],
        "levels": [
            {"key": "entry", "label": "DEMO option entry reference", "kind": "ENTRY",
             "unit": "OPTION_USD_PER_SHARE", "low": 1.15, "high": 1.25, "value": None},
            {"key": "stop", "label": "DEMO stock-price invalidation", "kind": "STOP",
             "unit": "UNDERLYING_USD_PER_SHARE", "value": underlying - sign},
            {"key": "target_1", "label": "DEMO stock-price target", "kind": "TARGET",
             "unit": "UNDERLYING_USD_PER_SHARE", "value": underlying + sign * 2},
        ],
        "as_of": stamp(created), "data_as_of": stamp(observed),
        "age_seconds": (now - observed).total_seconds(),
        "freshness": "STALE" if stale else "CURRENT",
        "source_alert_id": alert_id, "execution_authorized": False,
    }
    return alert, summary


def state_fixture(now: datetime) -> dict:
    pairs = [signal_fixture("SPY", now), signal_fixture("QQQ", now, stale=True)]
    health = {
        symbol: {
            "status": "STALE" if symbol == "QQQ" else "OK",
            "actionable": symbol != "QQQ", "source": "SYNTHETIC_UI_FIXTURE",
            "last_good_bar_at": stamp(now - timedelta(minutes=70 if symbol == "QQQ" else 2)),
        } for symbol in ("SPY", "QQQ", "SPX")
    }
    return {
        "fixture_only": True, "notice": DEMO_NOTICE, "session": "RTH",
        "symbols": {
            symbol: {"last": price, "matrix": {"5m": trend, "D": "UP", "W": "UNKNOWN"},
                     "data_quality": health[symbol]}
            for symbol, price, trend in (("SPY", 100.0, "UP"), ("QQQ", 200.0, "DOWN"),
                                         ("SPX", 3000.0, "NEUTRAL"))
        },
        "alerts": [pair[0] for pair in pairs], "suppressed": [], "performance": {},
        "profiles": {}, "briefing": None,
        "autotrade": {"effective_mode": "SIMULATION", "fixture_only": True},
        "readiness": {"ok": True, "fixture_only": True, "symbols": health},
        "last_poll": stamp(now),
        "signal_desk": {"version": 1, "generated_at": stamp(now),
                        "signals": [pair[1] for pair in pairs]},
    }


def dossier_fixture(now: datetime) -> dict:
    observed = now - timedelta(days=1)
    horizons = [
        {"horizon": key, "label": label, "coverage": coverage, "note": note}
        for key, label, coverage, note in (
            ("0DTE", "Today · 0DTE", "RESEARCH_ONLY", "No executable intraday signal."),
            ("SWING", "Swing · 2–5 sessions", "RESEARCH_ONLY", "Holding rule not validated."),
            ("LEAPS", "Long-term / LEAPS", "UNAVAILABLE", "Strategy not implemented."),
        )
    ]
    return {
        "fixture_only": True, "security": {"symbol": "AAPL", "name": "Apple · DEMO"},
        "watchlists": [],
        "analysis": {
            "symbol": "AAPL", "price": 123.45, "as_of": stamp(observed),
            "source": "SYNTHETIC_UI_FIXTURE", "consensus": {"label": "UPTREND"},
            "key_levels": {"last_close": 123.45}, "backtest": None,
        },
        "intelligence": {
            "generated_at": stamp(now),
            "company": {
                "name": "Apple · DEMO", "sector": "Technology · DEMO", "exchange": "DEMO",
                "description": "DEMO business profile for layout testing. This panel illustrates "
                "how a device-and-services company's business model, competitive position, "
                "and recurring revenue could be explained. These are not researched facts.",
                "overview_limitation": "All prices, metrics, and conclusions below are invented.",
            },
            "fundamentals": {"status": "PARTIAL", "coverage": 3, "score": None, "metrics": [
                {"key": "revenue_growth", "label": "Revenue growth · DEMO", "value": 8.2},
                {"key": "gross_margin", "label": "Gross margin · DEMO", "value": 40.1},
                {"key": "net_margin", "label": "Net margin · DEMO", "value": 20.3},
            ]},
            "filings": {"status": "DEMO", "items": [
                {"form": "DEMO 10-Q", "filed_at": "Synthetic example", "url": None},
            ]},
            "news": {"status": "DEMO", "headlines": [{
                "title": "DEMO: product cycle and business concentration are topics to investigate",
                "source": "Synthetic UI fixture", "published_at": stamp(now), "url": None,
            }]},
            "social": {"status": "NOT_CONFIGURED"},
            "multi_brain": {
                "status": "RESEARCH_ONLY", "posture": "HOLD",
                "summary": "DEMO: independent viewpoints may disagree; uncertainty stays visible.",
                "claude_review": {"status": "NOT_REQUESTED", "reason": "DEMO: no AI is called."},
            },
        },
        "coverage": {"technicals": "DEMO", "fundamentals": "DEMO", "news": "DEMO",
                     "social": "NOT_CONNECTED", "estimates": "NOT_CONNECTED"},
        "decision": {
            "version": "decision-summary-v1", "symbol": "AAPL", "horizon": "DAILY_CONTEXT",
            "horizon_label": "Daily stock research · DEMO", "horizon_coverage": "CONTEXT_ONLY",
            "horizon_note": "Daily context is not an intraday, 2–5-session, or LEAPS option signal.",
            "horizons": horizons, "trend": "UP", "thesis_direction": "UNKNOWN",
            "research_view": "WAIT", "next_step": "WAIT", "status": "RESEARCH_ONLY",
            "headline": "DEMO · wait for verified evidence",
            "reasons": ["A synthetic upward trend illustrates the research layout.",
                        "Business strength, valuation, and catalysts need independent verification."],
            "blockers": ["This is invented UI test data, not an investment thesis.",
                         "No current filing, quote, or social feed was fetched."],
            "levels": [{"key": "last_close", "label": "DEMO last completed close",
                        "kind": "REFERENCE", "unit": "UNDERLYING_USD_PER_SHARE", "value": 123.45},
                       {"key": "risk", "label": "DEMO price risk reference", "kind": "REFERENCE",
                        "unit": "UNDERLYING_USD_PER_SHARE", "value": 118.0}],
            "as_of": stamp(observed), "data_as_of": stamp(observed),
            "age_seconds": 86400, "freshness": "CURRENT", "execution_authorized": False,
        },
    }


def create_app() -> FastAPI:
    from intellidhan_gateway.playbooks import build_playbook_catalog

    app = FastAPI(title="IntelliDhan DEMO fixtures", docs_url=None, redoc_url=None,
                  openapi_url=None)

    @app.middleware("http")
    async def preview_boundary(request: Request, call_next):
        try:
            peer_is_local = bool(request.client and ip_address(request.client.host).is_loopback)
        except ValueError:
            peer_is_local = False
        host_is_local = request.url.hostname in {"127.0.0.1", "localhost", "::1"}
        if not peer_is_local or not host_is_local:
            return JSONResponse({"detail": "DEMO preview is loopback-only."}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            return JSONResponse({"detail": "DEMO is read-only; mutations and login are disabled."},
                                status_code=405, headers={"Allow": "GET, HEAD"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-IntelliDhan-Preview"] = "SYNTHETIC-UI-FIXTURES-ONLY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; frame-ancestors 'none'; "
            "form-action 'self'; base-uri 'none'"
        )
        return response

    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="preview-assets")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        html = (WEB / "desk.html").read_text()
        banner = (
            '<aside role="note" id="demoPreviewBanner" style="padding:12px 20px;'
            'background:#ffbe55;color:#101827;font:700 14px/1.5 system-ui;'
            'text-align:center;position:relative;z-index:10000">'
            f"{DEMO_NOTICE}</aside>"
        )
        html = html.replace('<body class="desk">', '<body class="desk">' + banner, 1)
        if 'id="demoPreviewBanner"' not in html:
            raise HTTPException(500, "DEMO banner could not be inserted; preview refused.")
        return html.replace("<title>", "<title>DEMO — ", 1)

    @app.get("/api/auth/session")
    async def session():
        return {"authenticated": True, "accounts_enabled": True, "fixture_only": True,
                "user": {"user_id": "demo-viewer", "display_name": "Demo Viewer",
                         "email": "demo@example.invalid", "role": "VIEWER", "legacy": False}}

    @app.get("/api/state")
    async def state():
        return state_fixture(datetime.now(timezone.utc))

    @app.get("/api/playbooks")
    async def playbooks():
        return {**build_playbook_catalog(), "fixture_only": True, "notice": DEMO_NOTICE}

    @app.get("/api/news")
    async def news():
        now = stamp(datetime.now(timezone.utc))
        return {"fixture_only": True, "items": [
            {"title": title, "source": "Synthetic UI fixture", "published_at": now, "link": None}
            for title in ("DEMO: yields and broad-market participation deserve attention",
                          "DEMO: a crowded earnings calendar can change the risk picture",
                          "DEMO: waiting is reasonable when price and volume disagree")
        ]}

    @app.get("/api/daily-brief")
    async def brief():
        return {"fixture_only": True, "status": "DEMO", "freshness_label": "DEMO · not live",
                "headline": "Practice reading the day, without risking capital.",
                "generated_at": stamp(datetime.now(timezone.utc)),
                "summary": ["DEMO: begin with market direction, then examine individual setups.",
                            "DEMO: stale or incomplete evidence means wait, even in a strong trend."],
                "economic_events": [
                    {"event": "DEMO inflation release", "time_et": "08:30 ET · example only",
                     "impact": "Synthetic event, not today's calendar"},
                    {"event": "DEMO central-bank remarks", "time_et": "14:00 ET · example only",
                     "impact": "Check an authoritative calendar before trading"},
                ]}

    @app.get("/api/watchlists")
    async def watchlists():
        return {"fixture_only": True, "watchlists": [
            {"watchlist_id": "demo", "name": "DEMO watchlist", "symbols": ["AAPL"]},
        ]}

    @app.get("/api/account/preferences")
    async def preferences():
        return {"fixture_only": True, "theme": "dark", "reduced_motion": True}

    @app.get("/api/dossier/AAPL")
    async def dossier():
        return dossier_fixture(datetime.now(timezone.utc))

    @app.get("/api/trade-log")
    async def journal():
        now = datetime.now(timezone.utc)
        return {"fixture_only": True, "broker_history_visible": False, "automation_trades": [
            {"symbol": "SPY", "event": "ENTRY", "mode": "SIMULATION", "simulated": True,
             "reason": "DEMO entry observation only; no real option was observed or purchased.",
             "observed_at": stamp(now - timedelta(minutes=40)), "option_price": 1.20},
            {"symbol": "SPY", "event": "EXIT", "mode": "SIMULATION", "simulated": True,
             "reason": "DEMO exit after an imagined trend break; not strategy performance.",
             "observed_at": stamp(now - timedelta(minutes=30)), "option_price": 1.10,
             "realized_pnl": -10.0},
        ], "paper_trades": [
            {"symbol": "QQQ", "outcome": "UNRESOLVED_DATA", "realized_r": None,
             "resolution_note": "DEMO: missing exit observation; result remains unscored.",
             "created_at": stamp(now - timedelta(days=1))},
        ]}

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=[HOST], default=HOST,
                        help="Only 127.0.0.1 is permitted; this is not a public server.")
    parser.add_argument("--port", type=int, default=8322)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    import uvicorn

    print(f"DEMO fixtures only: http://{HOST}:{args.port} — no accounts or trades are real.")
    uvicorn.run(create_app(), host=HOST, port=args.port, access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
