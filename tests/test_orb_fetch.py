from datetime import date, datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from intellidhan_schemas import Bar


SPEC = importlib.util.spec_from_file_location(
    "fetch_orb_diagnostic", Path(__file__).resolve().parents[1] / "scripts/fetch_orb_diagnostic.py"
)
fetch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch)
AS_OF = date(2026, 10, 6)
OBSERVED = datetime(2026, 10, 6, 16, tzinfo=timezone.utc)


def _frame(stamps=None, **changes):
    stamps = stamps or [pd.Timestamp("2026-10-05 09:30", tz="America/New_York")]
    data = {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5,
            "Volume": 1000.0, "Dividends": 0.0, "Stock Splits": 0.0}
    data.update(changes)
    return pd.DataFrame(data, index=stamps)


def _normalize(frame):
    return fetch.normalize_history(frame, "SPY", date(2026, 8, 8), AS_OF,
                                   observed_at=OBSERVED)


def test_open_timestamp_converts_to_canonical_close_without_adjustment():
    bars, audit = _normalize(_frame())
    assert bars[0].ts_close == datetime(2026, 10, 5, 13, 35, tzinfo=timezone.utc)
    assert bars[0].open == 100.0
    assert bars[0].close == 100.5
    assert bars[0].source == "yahoo_raw_research_diagnostic"
    assert audit["canonical_rows"] == 1
    assert audit["invalid_rows"] == 0


@pytest.mark.parametrize("frame,reason", [
    (_frame([datetime(2026, 10, 5, 9, 30)]), "naive_timestamp"),
    (_frame(Open=float("nan")), "nonfinite_ohlcv"),
    (_frame(Volume=float("inf")), "nonfinite_ohlcv"),
    (_frame(High=100.5 - 1e-12), "invalid_ohlc"),
    (_frame(Low=101), "invalid_ohlc"),
    (_frame(Volume=-1), "negative_volume"),
    (_frame(Low=0), "nonpositive_price"),
    (_frame(Open="bad"), "missing_or_nonnumeric_ohlcv"),
    (_frame().drop(columns="Volume"), "missing_or_nonnumeric_ohlcv"),
    (_frame([pd.Timestamp("2026-10-06 09:30", tz="America/New_York")]),
     "outside_requested_window"),
    (_frame([pd.Timestamp("2026-10-05 09:31", tz="America/New_York")]),
     "off_grid_timestamp"),
])
def test_invalid_rows_are_counted_and_audited_not_repaired(frame, reason):
    bars, audit = _normalize(frame)
    assert bars == []
    assert audit["provider_rows"] == audit["invalid_rows"] == 1
    assert audit["invalid_reason_counts"][reason] == 1
    assert reason in audit["invalid_row_audit"][0]["reasons"]
    assert audit["invalid_row_audit"][0]["provider_row"] == 0


def test_duplicate_timestamps_reject_all_rows_not_arbitrarily_select_one():
    frame = _frame()
    bars, audit = _normalize(pd.concat([frame, frame]))
    assert bars == []
    assert audit["invalid_reason_counts"]["duplicate_timestamp"] == 2


def test_incomplete_bar_is_rejected():
    bars, audit = fetch.normalize_history(
        _frame(), "SPY", date(2026, 10, 5), AS_OF,
        observed_at=datetime(2026, 10, 5, 13, 32, tzinfo=timezone.utc),
    )
    assert bars == []
    assert audit["invalid_reason_counts"]["incomplete_bar"] == 1


def test_daily_dividend_and_split_dates_are_separate_unverified_evidence():
    frame = _frame([
        pd.Timestamp("2026-09-18", tz="America/New_York"),
        pd.Timestamp("2026-10-01", tz="America/New_York"),
    ], Dividends=[1.0, 0.0], **{"Stock Splits": [0.0, 2.0]})
    audit = fetch.collect_action_dates(frame, date(2026, 6, 8), AS_OF)
    assert audit["dividend_dates"] == ["2026-09-18"]
    assert audit["split_dates"] == ["2026-10-01"]
    assert audit["action_dates"] == ["2026-09-18", "2026-10-01"]
    assert audit["status"] == "AVAILABLE_UNVERIFIED"
    assert audit["completeness_verified"] is False
    assert audit["point_in_time_availability_verified"] is False


def test_absent_daily_actions_never_become_verified_empty_dates():
    audit = fetch.collect_action_dates(_frame().drop(columns="Dividends"),
                                       date(2026, 6, 8), AS_OF)
    assert audit["status"] == "UNAVAILABLE_OR_INCOMPLETE"
    assert audit["invalid_rows"] == 1
    assert audit["action_dates"] == []
    assert fetch.collect_action_dates(pd.DataFrame(), date(2026, 6, 8), AS_OF)["status"] \
        == "UNAVAILABLE_OR_INCOMPLETE"


def test_fetch_queries_private_files_hashes_and_manifest_are_explicit(tmp_path):
    calls = []

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **query):
            calls.append((self.symbol, query))
            return _frame(Dividends=1.0) if query["interval"] == "1d" else _frame()

    out = tmp_path / "private"
    result = fetch.fetch_diagnostic(as_of=AS_OF, days=59, out_dir=out,
                                   ticker_factory=Ticker, observed_at=OBSERVED)
    assert [(symbol, query["interval"], query["prepost"]) for symbol, query in calls] == [
        ("SPY", "5m", True), ("^SPX", "5m", False), ("SPY", "1d", False),
    ]
    assert calls[0][1]["start"] == "2026-08-08"
    assert calls[2][1]["start"] == "2026-06-08"
    for _, query in calls:
        assert query["end"] == "2026-10-06"
        assert query["auto_adjust"] is query["back_adjust"] is query["repair"] is False
        assert query["actions"] is query["keepna"] is True
    assert result["price_basis"] == "raw"
    assert result["instrument_mapping"] == {"SPY": "SPY", "SPX": "^SPX"}
    assert result["live_eligible"] is False
    assert result["corporate_actions_verified"] is False
    assert result["point_in_time_availability_verified"] is False
    assert result["three_year_coverage"] is False
    assert result["spy_action_dates"] == ["2026-10-05"]
    assert result["bar_count"] == 2
    payload = (out / result["bars_file"]).read_bytes()
    assert result["bars_sha256"] == hashlib.sha256(payload).hexdigest()
    bars = [Bar.model_validate_json(line) for line in payload.splitlines()]
    assert {bar.symbol for bar in bars} == {"SPY", "SPX"}
    manifest = next(out.glob("*.manifest.json"))
    assert json.loads(manifest.read_text()) == result
    assert out.stat().st_mode & 0o777 == 0o700
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert (out / result["bars_file"]).stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        fetch.fetch_diagnostic(as_of=AS_OF, days=59, out_dir=out,
                               ticker_factory=Ticker, observed_at=OBSERVED)


def test_provider_failure_is_visible_without_exception_content_or_fallback(tmp_path, capsys):
    calls = []

    class FailedTicker:
        def __init__(self, symbol):
            calls.append(symbol)

        def history(self, **query):
            print("do-not-log-provider-content")
            raise RuntimeError("do-not-log-provider-content")

    result = fetch.fetch_diagnostic(as_of=AS_OF, days=59, out_dir=tmp_path / "private",
                                   ticker_factory=FailedTicker, observed_at=OBSERVED)
    assert calls == ["SPY", "^SPX", "SPY"]
    assert result["status"] == "INCOMPLETE_OR_UNAVAILABLE"
    assert result["bar_count"] == 0
    assert result["symbols"]["SPY"]["provider_error_type"] == "RuntimeError"
    assert "do-not-log" not in json.dumps(result)
    assert "do-not-log" not in capsys.readouterr().out


@pytest.mark.parametrize("days", [0, 60, -1, True, 1.5])
def test_window_is_bounded(tmp_path, days):
    with pytest.raises(ValueError, match="between 1 and 59"):
        fetch.fetch_diagnostic(as_of=AS_OF, days=days, out_dir=tmp_path,
                               ticker_factory=lambda _: None, observed_at=OBSERVED)


def test_cli_requires_explicit_as_of():
    with pytest.raises(SystemExit) as exc:
        fetch.main([])
    assert exc.value.code == 2
