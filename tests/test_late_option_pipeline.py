"""Synthetic OpenD transport → real signal, contract selection, journal and report."""
from datetime import datetime, timedelta
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from paper_late_options import validate_snapshot  # noqa: E402
from paper_trading.ledger import ET, Ledger  # noqa: E402
from paper_trading.late_option_feed import LateOptionFeed  # noqa: E402
from paper_trading.late_option_log import LateOptionLog  # noqa: E402
from paper_trading.late_options import observe_step  # noqa: E402
from test_paper_journal_feed import FakeContext, chain_row, option_snapshot, order_book  # noqa: E402


@pytest.mark.parametrize("missing_timestamp", [False, True])
def test_observed_option_pnl_never_uses_etf_return_or_changes_primary(tmp_path, missing_timestamp):
    opening = datetime(2026, 11, 20, 9, 30, tzinfo=ET)
    now = opening + timedelta(minutes=100, seconds=1)
    day = opening.date().isoformat()
    folder = tmp_path / ("research-late-options-" + day)
    folder.mkdir(mode=0o700)
    logpath = folder / "late-options.sqlite"
    journal, primary = LateOptionLog(logpath, day), Ledger(tmp_path / "primary.sqlite")

    def primary_snapshot():
        s = dict(primary.snapshot(now), mode="SIMULATION", session_date=day, now=now.isoformat(),
                 agent={"heartbeat_at": now.isoformat(), "status": "SESSION_COMPLETE"}, validation="RESEARCH_ONLY")
        return validate_snapshot(s, day, now)

    ctx = FakeContext()
    code = "US.SPY261120C100000"
    ctx.chain = [chain_row(code, 100)]
    ctx.snapshots = {"US.SPY": {"code": "US.SPY", "sec_status": "NORMAL", "suspension": False,
                                "update_time": now.isoformat(), "last_price": 100.75},
                     code: {**option_snapshot(code, 100), "update_time": now.isoformat(),
                            "ask_price": 2.0, "option_theta": -0.18}}
    book = {**order_book(code), "Bid": [(1.96, 5, 1, {})], "Ask": [(2.00, 7, 1, {})],
            "svr_recv_time_bid": now.isoformat(), "svr_recv_time_ask": None if missing_timestamp else now.isoformat()}
    ctx.books = {code: book}
    values = [(99.6, 100, 99.2, 99.6)] * 18 + [(99.9, 100.5, 99.8, 100.4), (100.4, 100.6, 99.95, 100.3)]
    ctx.bar_rows = [dict(code="US.SPY", time_key=(opening + timedelta(minutes=5*(i+1))).isoformat(),
                         open=op, high=hi, low=lo, close=cl, volume=1000) for i, (op, hi, lo, cl) in enumerate(values)]
    sdk = SimpleNamespace(RET_OK=0, OpenQuoteContext=lambda **kwargs: ctx, Session=SimpleNamespace(RTH="RTH"),
                          SubType=SimpleNamespace(ORDER_BOOK="BOOK", K_5M="K5"), KLType=SimpleNamespace(K_5M="K5"),
                          AuType=SimpleNamespace(QFQ="QFQ"), OptionType=SimpleNamespace(CALL="CALL", PUT="PUT"))
    feed = LateOptionFeed(sdk=sdk)
    feed.connect()
    def poll():
        return observe_step(day, feed=feed, source=primary_snapshot, journal=journal, clock=lambda: now)

    poll()
    assert not primary.snapshot(now)["trades"]
    if missing_timestamp:
        assert not journal.state()["trades"] and journal.report()["net_closed_pnl_usd"] is None
        assert any(e["kind"] == "TRIGGER" for e in journal.events())
        feed.close()
        return
    pos = journal.state()["open_position"]
    assert pos and pos["option_id"] == code and pos["entry_option_quote"]["provider_theta"] == -0.18
    now = opening + timedelta(hours=2)
    ctx.books[code] = {**book, "Bid": [(2.20, 5, 1, {})], "Ask": [(2.23, 7, 1, {})],
                       "svr_recv_time_bid": now.isoformat(), "svr_recv_time_ask": now.isoformat()}
    ctx.snapshots[code]["update_time"] = now.isoformat()
    ctx.snapshots["US.SPY"]["update_time"] = now.isoformat()
    poll()
    out = LateOptionLog(logpath, day, readonly=True).report()
    assert out["net_closed_pnl_usd"] == 16.70 and out["closed"] == 1
    assert "11:30" in out["trades"][0]["exit_reason"]
    assert primary.snapshot(now)["account"]["cash"] == 1000 and not primary.snapshot(now)["trades"]
    feed.close()
