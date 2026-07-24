from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess


class _IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, _tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])


def test_today_declutter_removes_duplicate_visible_surfaces():
    source = Path("web/index.html").read_text(encoding="utf-8")
    assert '<section class="strategy-shelf"' not in source
    assert '<section class="panel market-panel" hidden' in source
    assert '<section class="panel context-panel" id="contextPanel"' in source
    assert 'id="contextPanel" aria-labelledby="contextTitle" hidden' in source
    assert '<div class="panel risk-panel" id="riskPanel" hidden>' in source
    assert '<div class="auto-agent">' not in source
    assert 'data-tab="suppression"' not in source
    assert '<div class="bd-panel" id="bdSuppression" hidden>' in source


def test_today_declutter_uses_progressive_disclosure_and_conditional_detail():
    source = Path("web/index.html").read_text(encoding="utf-8")
    hero_start = source.index('<section class="terminal-hero"')
    hero_end = source.index("</section>", hero_start)
    assert 'id="homeStockSearchForm"' in source[hero_start:hero_end]
    assert '<details class="curated-panel">' in source
    assert "if(!selectionTouched||!alert){ panel.hidden=true; return; }" in source
    assert "selectedAlertId = latest?.alert_id" not in source
    assert 'type:"Analyze"' in source
    assert "analyzeCandidate(s);" in source


def test_risk_limit_failure_stays_visible_and_fail_closed():
    source = Path("web/index.html").read_text(encoding="utf-8")
    assert 'budgetsStatus="error"' in source
    assert "panel.hidden=!ownerAuthenticated" in source
    assert "Risk limits unavailable." in source
    assert "Do not approve execution until they reload." in source
    assert "onclick=\"loadCapitalLimits()\"" in source
    assert 'const stateKey=`error:${budgetsError||"unavailable"}`;' in source
    assert "if(output.dataset.riskState!==stateKey||!output.firstElementChild)" in source
    assert 'output.dataset.riskState="ready"' in source


def test_repeated_risk_error_render_preserves_retry_node_identity():
    node = shutil.which("node")
    assert node, "Node.js is required for inline terminal behavior tests"
    source = Path("web/index.html").read_text(encoding="utf-8")
    start = source.index("function renderRisk(s){")
    end = source.index("\n}\n\nfunction renderEvidencePanel", start) + 2
    function_source = source[start:end]
    harness = r"""
let ownerAuthenticated=true, budgetsCache=null, budgetsStatus="error";
let budgetsError="Budget feed unavailable.", replacementCount=0;
const panel={hidden:true};
const output={
  dataset:{}, firstElementChild:null, _html:"",
  set innerHTML(value){
    this._html=value;
    this.firstElementChild={identity:++replacementCount,focused:false};
  },
  get innerHTML(){ return this._html; }
};
const document={getElementById:(id)=>id==="riskPanel"?panel:output};
const esc=(value)=>String(value);
""" + function_source + r"""
renderRisk({alerts:[]});
const first=output.firstElementChild;
first.focused=true;
renderRisk({alerts:[]});
if(output.firstElementChild!==first||!first.focused||replacementCount!==1) process.exit(1);
budgetsError="A different failure.";
renderRisk({alerts:[]});
if(output.firstElementChild===first||replacementCount!==2) process.exit(2);
"""
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_autocomplete_invalidates_stale_results_and_exposes_keyboard_state():
    source = Path("web/index.html").read_text(encoding="utf-8")
    assert '.search-suggestion[aria-selected="true"]' in source
    assert "homeSearchRequest++;" in source
    assert "suggestions.replaceChildren();" in source
    assert "const request=homeSearchRequest;" in source
    assert 'if(event.key==="Escape"){ if(open) event.preventDefault(); hideHomeSuggestions(); }' in source


def test_decluttered_terminal_has_unique_element_ids():
    parser = _IdCollector()
    parser.feed(Path("web/index.html").read_text(encoding="utf-8"))
    duplicates = sorted({value for value in parser.ids if parser.ids.count(value) > 1})
    assert duplicates == []
