import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "orb_pattern_study", Path(__file__).parents[1] / "scripts/orb_pattern_study.py",
)
STUDY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STUDY)


def manifest(path):
    return dict(bars_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                provider="synthetic_test", price_basis="raw",
                instrument_mapping={"SPY": "SPY", "SPX": "SPX"})


def test_archive_requires_integrity_and_raw_price_basis(tmp_path):
    archive = tmp_path / "bars.jsonl"
    archive.write_text("")
    m = manifest(archive)
    assert STUDY.load_archive(archive, m) == []
    with pytest.raises(ValueError, match="hash"):
        STUDY.load_archive(archive, {**m, "bars_sha256": "wrong"})
    with pytest.raises(ValueError, match="raw"):
        STUDY.load_archive(archive, {**m, "price_basis": "adjusted"})


def test_archive_rejects_naive_and_broken_rows(tmp_path):
    archive = tmp_path / "bars.jsonl"
    row = dict(symbol="SPY", timeframe="5m", ts_close="2026-09-01T09:35:00",
               open=100, high=101, low=99, close=100, volume=10, source="synthetic_test")
    archive.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="row 1"):
        STUDY.load_archive(archive, manifest(archive))
    row["ts_close"] += "+00:00"
    archive.write_text(json.dumps(row) + "\n")
    assert len(STUDY.load_archive(archive, manifest(archive))) == 1


def test_empty_archive_is_incomplete_not_a_successful_backtest(tmp_path):
    archive, source = tmp_path / "bars.jsonl", tmp_path / "manifest.json"
    archive.write_text("")
    source.write_text(json.dumps(manifest(archive)))
    report = STUDY.run(archive, source, tmp_path / "report.json")
    assert not report["three_year_test_complete"]
    assert not report["archive_coverage_complete"]
    assert not report["premarket_coverage_complete"]
    assert report["data_audit"]["SPY"]["total_expected"] == 751
    assert report["data_audit"]["SPX"]["missing"] == 751
    assert len(report["full_study_blockers"]) == 5


def test_outputs_cannot_destroy_sources_or_previous_results(tmp_path):
    source = tmp_path / "source.json"
    source.write_text("preserve")
    out = tmp_path / "out.json"
    out.write_text("previous")
    with pytest.raises(ValueError, match="alias"):
        STUDY._check_paths([source], [source], True)
    with pytest.raises(ValueError, match="alias"):
        STUDY._check_paths([source], [out, out], True)
    with pytest.raises(FileExistsError):
        STUDY._check_paths([source], [out], False)
    alias = tmp_path / "alias.json"
    alias.symlink_to(source)
    with pytest.raises(ValueError):
        STUDY._check_paths([source], [alias], True)
    assert source.read_text() == "preserve"
    assert out.read_text() == "previous"


def test_protocol_drift_fails_closed():
    protocol = json.loads(STUDY.PROTOCOL.read_text())
    STUDY.validate_protocol(protocol)
    for key, wrong in [("atr_period", 10), ("atr_method", "WILDER"),
                       ("narrow_or_atr_max", .2), ("wide_or_atr_min", .5),
                       ("live_eligible", True), ("premarket_symbols", ["SPY", "SPX"])]:
        with pytest.raises(ValueError, match="protocol"):
            STUDY.validate_protocol({**protocol, key: wrong})


def test_hard_link_outputs_cannot_truncate_source(tmp_path):
    source, alias = tmp_path / "source.json", tmp_path / "alias.json"
    source.write_text("immutable evidence")
    os.link(source, alias)
    with pytest.raises(ValueError, match="hard-linked"):
        STUDY._check_paths([source], [alias], True)
    with pytest.raises(ValueError, match="hard-linked"):
        STUDY._check_paths([], [source, alias], True)
    assert source.read_text() == "immutable evidence"
