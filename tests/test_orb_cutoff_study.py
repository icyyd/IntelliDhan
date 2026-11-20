"""Offline boundary and paired-accounting checks; no quote credentials or runner."""
from datetime import datetime
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from orb_cutoff_study import historical_1115, summarize, verify_prefix  # noqa: E402
from orb_regime_study import ET, FIVE, VARIANTS, fill_path  # noqa: E402
from paper_trading.strategy import _evaluate, evaluate_sniper  # noqa: E402

OPEN = datetime(2026,11,19,9,30,tzinfo=ET)


def bar(now, op=99.6, hi=100, lo=99.2, cl=99.6):
    return {"started_at": now, "open": op, "high": hi, "low": lo, "close": cl, "volume": 1000}


def test_last_completed_retest_arms_research_but_never_widens_paper_api():
    # Interior closes until 11:00. Breakout at 11:00, retest at 11:05.
    rules = [bar(OPEN+i*FIVE) for i in range(18)] + [
        bar(OPEN+18*FIVE, 99.9,100.5,99.8,100.4),
        bar(OPEN+19*FIVE,100.4,100.6,99.95,100.3)]
    last_legal = OPEN+20*FIVE
    result = historical_1115("SPY", rules, None, last_legal, last_exit_at=None)
    assert result["status"] == "ARMED" and result["signal"]["trigger"] == 100.6
    assert historical_1115("SPY", rules, None, last_legal+FIVE, last_exit_at=None)["signal"] is None
    assert evaluate_sniper("SPY", rules, None, last_legal)["signal"] is None
    with pytest.raises(TypeError):
        evaluate_sniper("SPY", rules, None, last_legal, entry_cutoff=last_legal.time())
    with pytest.raises(ValueError, match="never a live quote"):
        historical_1115("SPY", rules, {"price":100.7}, last_legal, last_exit_at=None)
    assert _evaluate("SPY", rules, {"price":100.7}, last_legal, sniper=True,
                     entry_cutoff=(last_legal+FIVE).time())["status"] == "BLOCKED"


def test_latest_entry_flattens_at_1130_open_not_1130_extremes():
    sig = {"direction":"CALL", "symbol":"SPY", "trigger":100, "stop":90,
           "target":110, "evidence":{"ema9":99.8}}
    rows = [bar(OPEN+i*FIVE,100,100.5,99.5,100.3) for i in range(20,24)]
    rows.append(bar(OPEN+24*FIVE,100.1,140,60,115))
    trade = fill_path(sig, rows)
    assert trade["exit"] == OPEN+24*FIVE and trade["reason"] == "TIME"
    assert trade["net_bps"] == pytest.approx(8)


def trade(index, gross=4):
    return {"entry": OPEN+index*FIVE, "exit":OPEN+(index+1)*FIVE,
            "symbol":"SPY", "reason":"TIME", "gross_bps":gross, "net_bps":gross-2}


def test_receipt_prefix_is_exact_including_exits_and_rejects_1115_entry():
    old = {v: [trade(10)] for v in VARIANTS}
    new = {v: [trade(10), trade(12)] for v in VARIANTS}
    verify_prefix(old, new)
    for changed in [[{**trade(10),"exit":OPEN+13*FIVE},trade(12)], [trade(12)], [trade(10),trade(21)]]:
        with pytest.raises(ValueError):
            verify_prefix(old, {**new,"rvol":changed})


def test_incremental_first_and_repeat_use_whole_day_and_zero_sessions_pair_at_both_costs():
    old = [[trade(10)], []]+[[]]*8
    new = [[trade(10),trade(12)], [trade(15),trade(18,1)]]+[[]]*8
    info = summarize(old, new)
    assert info["added"]["trades"] == 3 and info["added"]["flat_sessions"] == 8
    assert info["added_first"]["trades"] == 1 and info["added_repeat"]["trades"] == 2
    assert info["newly_active_sessions"] == 1
    assert [b["trades"] for b in info["added_by_entry_ET"].values()] == [1,1,1]
    paired = info["paired_extended_minus_control"]
    assert paired["cost_2bps"]["mean_bps_per_session"] == .3
    assert paired["cost_5bps"]["mean_bps_per_session"] == -.6
