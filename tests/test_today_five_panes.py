from pathlib import Path


SOURCE = Path("web/index.html").read_text(encoding="utf-8")


def test_today_view_has_five_named_panes():
    for pane_id in ("todayTrend", "todayHighlights", "todayEvents", "todayMovers", "todaySignals"):
        assert f'id="{pane_id}"' in SOURCE
    assert ".today-grid" in SOURCE
    assert 'aria-label="Today\'s market overview"' in SOURCE


def test_today_trend_has_explicit_bullish_bearish_chop_states():
    assert '"BULLISH"' in SOURCE
    assert '"BEARISH"' in SOURCE
    assert '"CHOP"' in SOURCE
    assert '"PENDING"' in SOURCE
    assert "Waiting for validated SPX, SPY and QQQ trend data." in SOURCE
    assert "today-trend-orb" in SOURCE


def test_premarket_watch_surfaces_volume_trend_and_open_interest_context():
    assert "Premarket watch" in SOURCE
    assert "volume_ratio_5d_to_prior_20d" in SOURCE
    assert "open_interest??row.options_open_interest??row.oi" in SOURCE
    assert "OI when available" in SOURCE
    assert "Completed-bar scan" in SOURCE


def test_visible_brief_shows_freshness_and_generation_time():
    assert 'id="todayBriefStatus"' in SOURCE
    assert "briefFreshness" in SOURCE
    assert "briefGenerated" in SOURCE


def test_today_refreshes_existing_live_data_paths():
    assert "const AUTO_REFRESH_MS = 120000;" in SOURCE
    assert "setInterval(refreshWorkspace, AUTO_REFRESH_MS);" in SOURCE
    assert "refreshDailyBrief()" in SOURCE
    assert "refreshFocus()" in SOURCE
    assert "renderTodayPanes();" in SOURCE
