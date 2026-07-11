"""Phase 0 exit criterion: recorded session replays end-to-end, deterministically."""

import asyncio
from datetime import timedelta, timezone
from pathlib import Path

from intellidhan_analytics.indicators import IndicatorSnapshot
from intellidhan_analytics.replay import replay_session
from intellidhan_ingestor.backfill import read_recording, write_recording
from intellidhan_ingestor.bus import bar_topic, indicator_topic
from intellidhan_ingestor.sentinel import check_bars
from intellidhan_schemas import Bar, DataQuality, Timeframe

FIXTURE = Path(__file__).parent.parent / "fixtures/golden-sessions/qqq-complex-5m.jsonl"


def test_fixture_is_clean():
    bars = read_recording(FIXTURE)
    assert len(bars) >= 1000
    symbols = {b.symbol for b in bars}
    assert symbols == {"QQQ", "SPY", "SMH", "TQQQ"}
    for sym in symbols:
        series = [b for b in bars if b.symbol == sym]
        assert check_bars(sym, series).quality == DataQuality.OK


def test_replay_end_to_end_and_deterministic():
    bus1, digest1, n1 = asyncio.run(replay_session(FIXTURE))
    bus2, digest2, n2 = asyncio.run(replay_session(FIXTURE))
    assert n1 == n2 == len(read_recording(FIXTURE))
    # Determinism contract (doc 01 §7): byte-identical snapshot streams
    assert digest1 == digest2
    # Bus topics populated per the doc 01 §1.1 taxonomy
    assert bar_topic("5m") in bus1.topics()
    assert indicator_topic("QQQ", "5m") in bus1.topics()
    # Snapshots round-trip through the bus and end warmed-up
    entries = asyncio.run(bus1.read(indicator_topic("QQQ", "5m"), IndicatorSnapshot, count=10_000))
    last = entries[-1][1]
    assert last.symbol == "QQQ" and last.ema50 is not None and 0 <= last.rsi14 <= 100
    assert last.vwap is not None and last.atr14 > 0


def test_replay_detects_input_change(tmp_path):
    """Any input perturbation must change the digest — no silent divergence."""
    bars = read_recording(FIXTURE)
    mutated = bars[:500] + [
        Bar(
            symbol=bars[500].symbol, timeframe=bars[500].timeframe,
            ts_close=bars[500].ts_close, open=bars[500].open,
            high=bars[500].high + 5, low=bars[500].low,
            close=bars[500].close + 0.01, volume=bars[500].volume, source="mutated",
        )
    ] + bars[501:]
    p = tmp_path / "mutated.jsonl"
    write_recording(p, mutated)
    _, digest_orig, _ = asyncio.run(replay_session(FIXTURE))
    _, digest_mut, _ = asyncio.run(replay_session(p))
    assert digest_orig != digest_mut


def test_sentinel_still_flags_intrasession_gap():
    bars = read_recording(FIXTURE)
    qqq = [b for b in bars if b.symbol == "QQQ"]
    with_hole = qqq[:50] + qqq[60:100]  # remove 10 bars mid-session
    report = check_bars("QQQ", with_hole)
    assert report.quality == DataQuality.DEGRADED
    assert any("gap" in i for i in report.issues)


def test_recording_roundtrip(tmp_path):
    bars = read_recording(FIXTURE)[:20]
    p = tmp_path / "rt.jsonl"
    write_recording(p, bars)
    assert read_recording(p) == sorted(bars, key=lambda b: (b.ts_close, b.symbol))
    assert all(b.timeframe == Timeframe.M5 for b in bars)
    assert all(b.ts_close.utcoffset() == timedelta(0) for b in bars)
    assert all(b.ts_close.tzinfo is not None for b in bars)
    _ = timezone.utc  # keep import used
