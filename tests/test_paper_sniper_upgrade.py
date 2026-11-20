"""Validate the one-time paper-session migration without touching a live process."""
import fcntl
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import paper_trader as launcher
from paper_trading.ledger import LEGACY_V1_POLICY, Ledger, timestamp
from paper_trading.runner import atomic_json

DAY = "2026-11-20"
NOW = timestamp("2026-11-20T08:00-05:00")


@pytest.fixture
def legacy(tmp_path):
    data = tmp_path / "paper"
    data.mkdir(mode=0o700)
    atomic_json(data / "session.json", launcher.session_config(DAY, legacy=True))
    (data / "runner.lock").touch(mode=0o600)
    ledger = Ledger(data / "journal.sqlite", policy=LEGACY_V1_POLICY)
    ledger.record_event("PREFLIGHT", "Existing audit", NOW, event_id="original")
    return data


def test_prepare_new_session_v2_default_and_refuse_implicit_v1(legacy, tmp_path):
    assert launcher.prepare(tmp_path / "new", DAY)["max_entries"] is None
    with pytest.raises(ValueError, match="will not be reset"):
        launcher.prepare(legacy, DAY)
    assert launcher.inspect_empty_upgrade(legacy, DAY, now=NOW)["stage"] == "READY"


def test_empty_before_open_upgrade_preserves_history_and_is_idempotent(legacy):
    before = json.loads((legacy / "session.json").read_text())
    assert before["strategy"] == "ORB_RETEST_PAPER_V1"
    for _ in range(2):
        assert launcher.upgrade_empty_session(legacy, DAY, now=NOW)["stage"] == "ALREADY_UPGRADED"
    assert launcher.prepare(legacy, DAY)["strategy"] == "ORB_RETEST_SNIPER_V2"
    with sqlite3.connect(legacy / "journal.sqlite") as db:
        events = db.execute("SELECT kind FROM events ORDER BY rowid").fetchall()
        assert events == [("PREFLIGHT",), ("PAPER_POLICY_MIGRATION",)]
        assert db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_recover_after_db_commit_before_config_replacement(legacy, monkeypatch):
    from paper_trading import runner
    original = runner.atomic_json
    def failure(*args, **kwargs):
        raise OSError("injected interrupted config write")
    monkeypatch.setattr(runner, "atomic_json", failure)
    with pytest.raises(OSError):
        launcher.upgrade_empty_session(legacy, DAY, now=NOW)
    assert launcher.inspect_empty_upgrade(legacy, DAY, now=NOW)["stage"] == "RESUME_CONFIG"
    with pytest.raises(ValueError, match="will not be reset"):
        launcher.prepare(legacy, DAY)
    monkeypatch.setattr(runner, "atomic_json", original)
    assert launcher.upgrade_empty_session(legacy, DAY, now=NOW)["stage"] == "ALREADY_UPGRADED"


def test_active_owner_and_lock_both_block_changes(legacy, monkeypatch):
    with (legacy / "runner.lock").open("r") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="owns"):
            launcher.upgrade_empty_session(legacy, DAY, now=NOW)
    monkeypatch.setattr(launcher, "process_matches", lambda _: True)
    assert launcher.inspect_empty_upgrade(legacy, DAY, now=NOW)["observer_running"]
    with pytest.raises(ValueError, match="running"):
        launcher.upgrade_empty_session(legacy, DAY, now=NOW)
    assert json.loads((legacy / "session.json").read_text())["max_entries"] == 2


@pytest.mark.parametrize("at", ["2026-11-20T09:30:00-05:00", "2026-11-23T09:20:00-05:00"])
def test_reject_open_or_past_session(legacy, at):
    with pytest.raises(ValueError, match="unopened"):
        launcher.upgrade_empty_session(legacy, DAY, now=timestamp(at))


@pytest.mark.parametrize("artifact", ["trade", "receipt", "audit_conflict", "partial", "capital"])
def test_reject_filled_or_unrecognized_state(legacy, artifact):
    with sqlite3.connect(legacy / "journal.sqlite") as db:
        if artifact == "trade":
            db.execute("INSERT INTO trades VALUES ('t','sig','{}')")
        elif artifact == "receipt":
            db.execute("INSERT INTO events VALUES ('e','now','EXIT','closed','{}')")
        elif artifact == "audit_conflict":
            db.execute("INSERT INTO events VALUES ('policy:ORB_RETEST_PAPER_V1:ORB_RETEST_SNIPER_V2','now','OTHER','unexpected','{}')")
        elif artifact == "partial":
            db.execute("INSERT INTO events VALUES ('fake','now','PAPER_POLICY_MIGRATION','manual','{}')")
        else:
            db.execute("UPDATE metadata SET value='{}'")
    before = (legacy / "session.json").read_bytes()
    with pytest.raises(ValueError):
        launcher.upgrade_empty_session(legacy, DAY, now=NOW)
    assert (legacy / "session.json").read_bytes() == before


def test_reject_untrusted_paths_and_anomalous_config(legacy, tmp_path):
    (legacy / "journal.sqlite-wal").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="private"):
        launcher.inspect_empty_upgrade(legacy, DAY, now=NOW)
    (legacy / "journal.sqlite-wal").unlink()
    altered = launcher.session_config(DAY, legacy=True)
    altered["max_premium"] = 999
    atomic_json(legacy / "session.json", altered)
    with pytest.raises(ValueError, match="altered"):
        launcher.upgrade_empty_session(legacy, DAY, now=NOW)


def test_normal_start_cannot_recreate_missing_session(legacy):
    (legacy / "session.json").unlink()
    with pytest.raises(ValueError, match="no session configuration"):
        launcher.prepare(legacy, DAY)
