"""Static and dependency-free formatting checks for the isolated paper portal."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "web/paper.html"
CSS = ROOT / "web/assets/paper.css"
JS = ROOT / "web/assets/paper.js"


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.resources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        if tag in {"script", "img"} and values.get("src"):
            self.resources.append(values["src"])
        if tag == "link" and values.get("href"):
            self.resources.append(values["href"])


def test_paper_portal_ids_and_local_assets() -> None:
    page = Page()
    page.feed(HTML.read_text())
    assert len(page.ids) == len(set(page.ids))
    targets = set(re.findall(r'(?:set\(|\$\()"([a-z-]+)"', JS.read_text()))
    assert targets.issubset(set(page.ids))
    assert page.resources
    for resource in page.resources:
        assert resource.startswith("/assets/")
        assert (ROOT / "web" / resource.lstrip("/")).is_file()


def test_paper_portal_safety_and_autorefresh_copy() -> None:
    html = HTML.read_text()
    script = JS.read_text()
    assert "Paper only" in html
    assert "No real orders" in html
    assert "unvalidated research strategy" in html
    assert "Daily profit is a goal, not a guarantee" in html
    assert "No averaging down" in html
    assert "POLL_MS = 5000" in script
    assert 'credentials: "same-origin"' in script
    assert "AbortController" in script
    assert "state.mode !== \"SIMULATION\"" in script
    assert "innerHTML" not in script
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "prices and P/L may be stale" in script
    assert "response.status === 401" in script
    assert 'method: "POST"' not in script
    assert "/api/autotrade" not in script


def test_paper_portal_responsive_and_accessible_baseline() -> None:
    css = CSS.read_text()
    html = HTML.read_text()
    assert "font-size: 16px" in css
    assert "max-width: 1050px" in css
    assert "max-width: 780px" in css
    assert "max-width: 580px" in css
    assert "minmax(0," in css
    assert "overflow-wrap: anywhere" in css
    assert "prefers-reduced-motion" in css
    assert ":focus-visible" in css
    assert 'class="skip-link"' in html
    assert 'aria-live="polite"' in html
    assert 'name="viewport"' in html
    # A static layout avoids the sticky bars that overlapped the older terminal.
    assert "position: fixed" not in css
    assert "position: sticky" not in css


def test_paper_portal_formatting_does_not_invent_missing_values() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable for JavaScript formatting test")
    code = f"""
const assert = require('node:assert/strict');
const ui = require({json.dumps(str(JS))});
for (const value of [null, undefined, '', ' ', false, true, [], {{}}, NaN, Infinity]) {{
  assert.equal(ui.money(value), '—');
  assert.equal(ui.tone(value), '');
}}
assert.equal(ui.money(0), '$0.00');
assert.equal(ui.money('1000.25'), '$1,000.25');
assert.equal(ui.money(-12.5, true), '-$12.50');
assert.equal(ui.money(12.5, true), '+$12.50');
assert.equal(ui.tone(-1), 'negative');
assert.equal(ui.tone(1), 'positive');
const now = Date.parse('2026-11-20T14:45:00Z');
assert.equal(ui.heartbeatFresh('2026-11-20T14:44:30Z', now), true);
assert.equal(ui.heartbeatFresh('2026-11-20T14:43:30Z', now), false);
assert.equal(ui.heartbeatFresh('2026-11-20T14:46:00Z', now), false);
assert.equal(ui.heartbeatFresh(null, now), false);
assert.equal(ui.time('invalid'), '—');
assert.equal(ui.sessionDate('2026-11-20'), 'Friday, November 20, 2026');
"""
    subprocess.run([node, "--check", str(JS)], check=True, capture_output=True, text=True)
    subprocess.run([node, "-e", code], check=True, capture_output=True, text=True)


def test_paper_portal_renders_ledger_schema_and_pending_exit() -> None:
    """Exercise actual DOM-building code without a browser or external package."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable for JavaScript rendering test")
    code = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
class Element {
  constructor() { this.children = []; this.dataset = {}; this.value = ''; }
  set textContent(value) { this.value = String(value); this.children = []; }
  get textContent() { return this.value + this.children.map(child => child.textContent).join(' '); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this.value = ''; }
  setAttribute() {}
  querySelectorAll() { return []; }
}
const elements = new Map();
const document = {
  getElementById: id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  },
  createElement: () => new Element(),
  createElementNS: () => new Element(),
  addEventListener() {},
};
const state = {
  mode: 'SIMULATION', session_date: '2026-11-20',
  agent: {status: 'EXIT_PENDING', reason: 'Exit quote unavailable', feed_status: 'BLOCKED', heartbeat_at: new Date().toISOString()},
  account: {initial_capital: 1000, equity: null, today_pnl: null, realized_pnl: -1.3, settled_cash: 600, unrealized_pnl: null},
  policy: {max_premium: 500, max_daily_loss: 500, max_entries_per_day: 2},
  open_position: {symbol: 'SPY', option_id: '<script>not executable</script>', option_type: 'CALL', quantity: 1, entry_price: 3.99, stop: 550, target: 555, entered_at: '2026-11-20T15:00:00Z', entry_reason: 'Retest confirmed', status: 'EXIT_PENDING', exit_reason: 'Stop crossed', mark: {bid: 3.75}},
  trades: [{trade_id:'one', symbol:'SPY', option_id:'test', entry_price:3.99, entry_fee_cents:65, exit_fee_cents:65, realized_pnl:-1.3, entered_at:'2026-11-20T15:00:00Z', exited_at:'2026-11-20T15:01:00Z', exit_reason:'Stop crossed'}],
  events: [{recorded_at:'2026-11-20T15:01:00Z',kind:'EXIT_PENDING',reason:'Stop crossed',details:{symbol:'SPY'}}],
  watchlist: [],
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
  document, Date, Intl, Number, String, Array, Set, JSON, console,
  AbortController, setTimeout: () => 1, clearTimeout: () => {},
  fetch: async () => ({ok:true,status:200,json:async () => state}),
});
setImmediate(() => {
  assert.equal(elements.get('account-equity').textContent, '—');
  assert.equal(elements.get('daily-pnl').textContent, '—');
  assert.equal(elements.get('available-cash').textContent, '$600.00');
  assert.equal(elements.get('max-premium').textContent, '$500.00');
  assert.equal(elements.get('max-entries').textContent, '2');
  assert.equal(elements.get('position-status').textContent, 'Exit waiting for a quote');
  assert.match(elements.get('position-content').textContent, /Stop crossed/);
  assert.doesNotMatch(elements.get('position-content').textContent, /\$3\.75/);
  assert.match(elements.get('trade-list').textContent, /\$1\.30/);
  assert.match(elements.get('activity-list').textContent, /SPY/);
  assert.match(elements.get('activity-list').textContent, /10:01:00 AM/);
});
"""
    subprocess.run([node, "-e", code, str(JS)], check=True, capture_output=True, text=True)
