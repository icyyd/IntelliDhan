import csv
from datetime import datetime
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from intellidhan_learning.orb_data import ET
from intellidhan_learning.tradingview_coverage import (
    audit_files, parse_record, read_probe_csv, write_audit,
)


def _ms(day, hour, minute=0):
    return int(datetime.fromisoformat(day).replace(
        hour=hour, minute=minute, tzinfo=ET,
    ).timestamp() * 1000)


def _record(day="2023-10-06", symbol="AMEX:SPY", **changes):
    fields = {
        "sym": symbol, "day": day, "r": 78, "rf": _ms(day, 9, 30),
        "rl": _ms(day, 16), "rg": 0, "p": 66, "pf": _ms(day, 4),
        "pl": _ms(day, 9, 30), "pg": 0, "end": "RTH_END", "warm": 15,
    }
    if symbol.endswith(":SPX"):
        fields.update(p="na", pf="na", pl="na", pg="na")
    fields.update(changes)
    return "COV1|" + "|".join(f"{key}={value}" for key, value in fields.items())


def _csv(tmp_path, *records, name="trades.csv", entry=True):
    path = tmp_path / name
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        # Intentionally reordered and with an irrelevant financial-result field.
        writer.writerow(["Net P&L USD", "Signal", "Type", "Trade #"])
        for number, record in enumerate(records, 1):
            writer.writerow(["999999", record, "Exit long", number])
            if entry:
                fields = dict(part.split("=", 1) for part in record.split("|")[1:])
                note = f"BEGIN1|sym={fields['sym']}|day={fields['day']}"
                writer.writerow(["999999", note, "Entry long", number])
    return path


def test_complete_metadata_is_not_three_year_backtest_or_performance(tmp_path):
    path = _csv(tmp_path, _record())
    report = audit_files([path])
    spy = report["instruments"]["SPY"]
    assert spy["expected_sessions"] == 751
    assert spy["complete_rth_metadata_days"] == ["2023-10-06"]
    assert len(spy["missing_days"]) == 750
    assert not spy["rth_metadata_complete"]
    assert spy["premarket"]["complete_metadata_days"] == ["2023-10-06"]
    assert report["sources"][0]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report["calendar"] == "exchange-calendars==4.11.2:XNYS"
    assert not report["three_year_test_complete"]
    assert not report["execution_authorized"]
    assert not spy["warmup_validated"]


def test_early_close_and_dst_are_independently_validated(tmp_path):
    path = _csv(tmp_path,
                _record("2024-11-29", r=42, rl=_ms("2024-11-29", 13), end="NEXT_DAY"),
                _record("2024-11-04"))
    spy = audit_files([path])["instruments"]["SPY"]
    assert len(spy["complete_rth_metadata_days"]) == 2
    assert not spy["invalid_days"]


@pytest.mark.parametrize("changes,reason", [
    ({"r": 77}, "rth_count_mismatch"),
    ({"rg": 1}, "rth_gap_or_malformed_bar"),
    ({"rf": _ms("2023-10-06", 9, 35)}, "rth_first_open_mismatch"),
    ({"rl": _ms("2023-10-06", 15, 55)}, "rth_last_close_mismatch"),
    ({"end": "DATA_END"}, "provisional_data_end"),
])
def test_partial_or_gapped_metadata_is_invalid(tmp_path, changes, reason):
    path = _csv(tmp_path, _record(**changes))
    spy = audit_files([path])["instruments"]["SPY"]
    assert reason in spy["invalid_days"][0]["reasons"]
    assert not spy["complete_rth_metadata_days"]


def test_premarket_missing_separate_from_rth_and_spx_not_applicable(tmp_path):
    spy = _csv(tmp_path, _record(p=0, pf="na", pl="na", pg=1))
    spx = _csv(tmp_path, _record(symbol="SP:SPX"), name="spx.csv")
    report = audit_files([spy, spx])
    assert report["both_instruments_supplied"]
    assert report["instruments"]["SPY"]["complete_rth_metadata_days"]
    assert not report["instruments"]["SPY"]["premarket"]["complete_metadata_days"]
    assert report["instruments"]["SPX"]["premarket"]["metadata_complete"] is None
    assert report["instruments"]["SPX"]["chart_source_id"] == "SP:SPX"


def test_delayed_sp_index_source_remains_separate(tmp_path):
    path = _csv(tmp_path, _record(symbol="SP_DLY:SPX"))
    report = audit_files([path])
    assert report["sources"][0]["chart_source_id"] == "SP_DLY:SPX"
    assert report["instruments"]["SPX"]["chart_source_id"] == "SP_DLY:SPX"
    assert any("not realtime SP:SPX" in item for item in report["limitations"])
    with pytest.raises(ValueError, match="one source"):
        read_probe_csv(_csv(tmp_path, _record(symbol="SP_DLY:SPX"),
                            _record("2023-10-09", symbol="SP:SPX")))


def test_observed_spy_tail_pattern_retains_incomplete_premarket(tmp_path):
    # Synthetic timestamps recreate the UI-observed counts; not an exported archive.
    path = _csv(tmp_path, _record("2026-10-02", symbol="BATS:SPY"),
                _record("2026-10-05", symbol="BATS:SPY", p=64, pg=1))
    spy = audit_files([path])["instruments"]["SPY"]
    assert spy["complete_rth_metadata_days"] == ["2026-10-02", "2026-10-05"]
    assert spy["premarket"]["complete_metadata_days"] == ["2026-10-02"]
    assert spy["premarket"]["invalid_days"] == [{
        "day": "2026-10-05", "reasons": ["premarket_count_mismatch",
                                           "premarket_gap_or_malformed_bar"],
    }]


def test_non_session_day_and_wrong_early_close_are_invalid(tmp_path):
    path = _csv(tmp_path, _record("2024-11-28"), _record("2024-11-29"))
    invalid = audit_files([path])["instruments"]["SPY"]["invalid_days"]
    assert invalid[0]["reasons"] == ["not_an_exchange_session"]
    assert invalid[1]["reasons"] == ["rth_count_mismatch", "rth_last_close_mismatch"]


def test_premarket_gaps_and_wrong_session_bounds_stay_visible(tmp_path):
    path = _csv(tmp_path, _record(p=65, pf=_ms("2023-10-06", 4, 5), pg=1))
    invalid = audit_files([path])["instruments"]["SPY"]["premarket"]["invalid_days"]
    assert invalid[0]["reasons"] == ["premarket_count_mismatch",
                                      "premarket_first_open_mismatch",
                                      "premarket_gap_or_malformed_bar"]


@pytest.mark.parametrize("signal", [
    _record() + "|extra=1", _record().replace("|r=78", "|rg=78"),
    _record(r="78.0"), _record(r=-1), _record(rg=2), _record(warm="na"),
    _record(end="OPEN"), _record(day="2026-10-06"), _record(symbol="SPY"),
    _record(symbol="AMEX:AVGO"), _record(symbol="SP:SPX", p=66),
    _record(symbol="CFD:SPX"), _record(symbol="TVC:SPX"),
    _record(p=0, pf="na", pl="na", pg=0), _record(p=66, pf="na"),
])
def test_strict_record_schema(signal):
    with pytest.raises(ValueError):
        parse_record(signal)


def test_duplicate_days_and_mixed_sources_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        read_probe_csv(_csv(tmp_path, _record(), _record()))
    with pytest.raises(ValueError, match="one source"):
        read_probe_csv(_csv(tmp_path, _record(), _record("2023-10-09", symbol="BATS:SPY")))
    a = _csv(tmp_path, _record(), name="a.csv")
    b = _csv(tmp_path, _record("2023-10-09"), name="b.csv")
    with pytest.raises(ValueError, match="one CSV per symbol"):
        audit_files([a, b])


@pytest.mark.parametrize("note", [
    "BEGIN1|sym=SP:SPX|day=2023-10-06",
    "BEGIN1|sym=AMEX:SPY|day=2023-10-09",
    "BEGIN1|sym=CFD:SPX|day=2023-10-06",
    "BEGIN1|sym=AMEX:SPY|day=2026-10-06",
    "BEGIN1|sym=AMEX:SPY|day=2023-10-06|extra=1",
    "BEGIN1|sym=AMEX:SPY|sym=AMEX:SPY",
    "BEGIN1|sym=AMEX:SPY|day=not-a-day",
    "BEGIN1|ignored=entry",
])
def test_entry_identity_is_strict_and_must_match_exit(tmp_path, note):
    path = _csv(tmp_path, _record(), entry=False)
    with path.open("a", newline="") as stream:
        csv.writer(stream).writerow(["0", note, "Entry long", "1"])
    with pytest.raises(ValueError):
        read_probe_csv(path)


def test_unpaired_exit_and_duplicate_or_dangling_entry_rejected(tmp_path):
    path = _csv(tmp_path, _record(), entry=False)
    with pytest.raises(ValueError, match="pair one-to-one"):
        read_probe_csv(path)
    path = _csv(tmp_path, _record())
    with path.open("a", newline="") as stream:
        csv.writer(stream).writerow([
            "0", "BEGIN1|sym=AMEX:SPY|day=2023-10-06", "Entry long", "1",
        ])
    with pytest.raises(ValueError, match="duplicate BEGIN1"):
        read_probe_csv(path)
    path = _csv(tmp_path, _record())
    with path.open("a", newline="") as stream:
        csv.writer(stream).writerow([
            "0", "BEGIN1|sym=AMEX:SPY|day=2023-10-09", "Entry long", "2",
        ])
    with pytest.raises(ValueError, match="pair one-to-one"):
        read_probe_csv(path)


@pytest.mark.parametrize("content", [
    "Signal\nCOV1\n", "Type,Signal,Signal\nExit long,x,x\n", "Type,Signal\n",
    "Type,Signal\nExit long,buy\n", "Type,Signal\nExit long\n",
    "Type,Signal\nExit long,x,extra\n", "Type,Signal\nEntry long,BEGIN1|x=1\n",
    "Type,Signal\nEntry short,BEGIN1|x=1\n",
    'Type,Signal\nExit long,"truncated\n',
])
def test_wrong_or_truncated_file_rejected(tmp_path, content):
    path = tmp_path / "bad.csv"
    path.write_text(content)
    with pytest.raises((ValueError, csv.Error)):
        read_probe_csv(path)


def test_no_clobber_aliases_and_private_output(tmp_path):
    path = _csv(tmp_path, _record())
    raw = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_audit([path], path)
    output = tmp_path / "audit.json"
    write_audit([path], output)
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_audit([path], output)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(FileExistsError):
        write_audit([path], link)
    hard = tmp_path / "hard.json"
    hard.hardlink_to(path)
    with pytest.raises(FileExistsError):
        write_audit([path], hard)
    assert path.read_bytes() == raw


def test_cli_reports_metadata_only(tmp_path):
    path = _csv(tmp_path, _record())
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "scripts/audit_tradingview_coverage.py"),
         "--input", str(path), "--out", str(tmp_path / "out.json")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "1/751" in result.stdout and "Metadata only" in result.stdout
