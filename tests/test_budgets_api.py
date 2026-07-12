"""Budgets hot-reload + validation tests (web-editable settings)."""

import pytest

from intellidhan_engine.composer import Budgets
from intellidhan_schemas.signals import Module


@pytest.fixture
def budgets_file(tmp_path):
    p = tmp_path / "budgets.yaml"
    p.write_text("""
budgets:
  0DTE:
    daily_capital: 4000
    risk_cap_pct: 0.25
    max_alerts_per_day: 5
  SWING:
    daily_capital: 12000
    risk_cap_pct: 0.33
  LEAPS:
    standing_capital: 30000
    risk_cap_pct: 0.15
  HODL:
    standing_capital: 50000
    risk_cap_pct: 0.10
""")
    return p


def test_loads_and_reads(budgets_file):
    b = Budgets(str(budgets_file))
    assert b.capital(Module.ZDTE) == 4000.0
    assert b.risk_cap(Module.ZDTE) == 0.25
    assert b.capital(Module.LEAPS) == 30000.0  # standing_capital key


def test_hot_reload_on_mtime_change(budgets_file):
    b = Budgets(str(budgets_file))
    assert b.capital(Module.ZDTE) == 4000.0
    import time
    time.sleep(0.01)
    budgets_file.write_text(budgets_file.read_text().replace("4000", "9000"))
    assert b.capital(Module.ZDTE) == 9000.0  # picked up without re-instantiation


def test_update_persists_and_reloads(budgets_file):
    b = Budgets(str(budgets_file))
    new_cfg = b.as_dict()
    new_cfg["0DTE"]["daily_capital"] = 6500
    new_cfg["0DTE"]["risk_cap_pct"] = 0.20
    b.update(new_cfg)
    assert b.capital(Module.ZDTE) == 6500.0
    assert b.risk_cap(Module.ZDTE) == 0.20
    # a fresh instance reading the same file sees the persisted change
    b2 = Budgets(str(budgets_file))
    assert b2.capital(Module.ZDTE) == 6500.0
    assert b2.as_dict()["0DTE"]["max_alerts_per_day"] == 5  # untouched keys survive


def test_update_rejects_bad_values(budgets_file):
    b = Budgets(str(budgets_file))
    cfg = b.as_dict()

    bad_capital = {**cfg, "0DTE": {**cfg["0DTE"], "daily_capital": -100}}
    with pytest.raises(ValueError, match="positive"):
        b.update(bad_capital)

    bad_risk = {**cfg, "0DTE": {**cfg["0DTE"], "risk_cap_pct": 1.5}}
    with pytest.raises(ValueError, match="risk_cap_pct"):
        b.update(bad_risk)

    bad_module = {**cfg, "NOT_A_MODULE": {"daily_capital": 1, "risk_cap_pct": 0.1}}
    with pytest.raises(ValueError, match="unknown module"):
        b.update(bad_module)

    # rejected update must NOT have partially written the file
    assert b.capital(Module.ZDTE) == 4000.0
