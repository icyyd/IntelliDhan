"""Cross-instance exactly-once Telegram delivery.

Koyeb rolling deploys briefly run two instances at once; each has its own
in-memory bar/plan dedup, so before this fix both delivered the same alert
(the duplicate-Telegram bug). These tests prove the shared-store claim lets
exactly one instance send.
"""

from datetime import datetime, timedelta, timezone

import pytest

from intellidhan_gateway.live import LiveLoop
from intellidhan_gateway.terminal_store import TerminalStore
from intellidhan_schemas.signals import (
    Action,
    Alert,
    Module,
    TakeProfit,
    Vehicle,
)


def make_alert(alert_id="alr_dup_1", plan_key="pln_dup_1") -> Alert:
    now = datetime.now(timezone.utc)
    return Alert(
        alert_id=alert_id, plan_key=plan_key, created_at=now, module=Module.SWING,
        strategy="TEST_STRATEGY", action=Action.EQUITY_BUY, symbol="SPY",
        underlying_price=500.0, vehicle=Vehicle.EQUITY, legs=[], equity_qty=10,
        entry_limit=500.0, entry_zone=(499.5, 500.5), stop_underlying=490.0,
        stop_est_vehicle=490.0, stop_rule="close below 490",
        take_profits=[TakeProfit(zone_low=None, zone_high=None, underlying=520.0,
                                 tranche=1.0, basis="t1")],
        contracts=None, capital_required=5000.0, dollar_risk=100.0, reward_risk=2.0,
        budget_note="test", confidence=0.80, factors={}, trend_matrix={"D": "UP"},
        thesis="t", invalidation="close below 490", management=["protect"],
        risks=[], valid_until=now + timedelta(hours=1),
    )


def test_claim_delivery_is_idempotent(tmp_path):
    store = TerminalStore(tmp_path / "s.sqlite3")
    store.init_schema()
    assert store.claim_delivery("alert:pln_x") is True
    assert store.claim_delivery("alert:pln_x") is False   # already claimed
    assert store.claim_delivery("alert:pln_y") is True     # distinct key


def test_two_stores_on_same_db_claim_exactly_once(tmp_path):
    """Two separate store objects on one DB file model two processes."""
    path = tmp_path / "shared.sqlite3"
    a, b = TerminalStore(path), TerminalStore(path)
    a.init_schema()
    b.init_schema()
    results = [a.claim_delivery("alert:pln_shared"), b.claim_delivery("alert:pln_shared")]
    assert sorted(results) == [False, True]  # exactly one winner


class _CountingTelegram:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)
        return True


@pytest.mark.asyncio
async def test_two_overlapping_instances_deliver_alert_once(tmp_path, monkeypatch):
    """Two LiveLoops sharing one store both process the same alert; only one
    Telegram message goes out (the rolling-deploy overlap scenario)."""
    path = tmp_path / "overlap.sqlite3"
    loop_a = LiveLoop(symbols=["SPY"], store=TerminalStore(path))
    loop_b = LiveLoop(symbols=["SPY"], store=TerminalStore(path))
    for lp in (loop_a, loop_b):
        lp.store.init_schema()
        lp.persistence_ready = True
        lp.telegram = _CountingTelegram()

    alert = make_alert()
    await loop_a._deliver(alert)
    await loop_b._deliver(alert)   # same alert on the overlapping instance

    total = len(loop_a.telegram.sent) + len(loop_b.telegram.sent)
    assert total == 1, f"expected exactly one send across instances, got {total}"


@pytest.mark.asyncio
async def test_delivery_sends_when_store_unavailable(tmp_path, monkeypatch):
    """Fail-open: a claim-store error must never silence a real alert."""
    loop = LiveLoop(symbols=["SPY"], store=TerminalStore(tmp_path / "s.sqlite3"))
    loop.persistence_ready = True
    loop.telegram = _CountingTelegram()

    def boom(_key):
        raise RuntimeError("db down")

    monkeypatch.setattr(loop.store, "claim_delivery", boom)
    await loop._deliver(make_alert())
    assert len(loop.telegram.sent) == 1  # sent despite the store error


def test_briefing_and_settlement_keys_are_namespaced(tmp_path):
    """Alert, settlement, and briefing keys never collide in the claim table."""
    store = TerminalStore(tmp_path / "s.sqlite3")
    store.init_schema()
    assert store.claim_delivery("alert:pln_z") is True
    assert store.claim_delivery("settle:pln_z:STOPPED") is True   # distinct namespace
    assert store.claim_delivery("briefing:2026-07-15") is True
    assert store.claim_delivery("alert:pln_z") is False
