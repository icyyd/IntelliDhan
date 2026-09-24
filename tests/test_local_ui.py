from pathlib import Path
import shutil
import subprocess


SOURCE = Path("web/index.html").read_text()


def run_js(start, end, harness):
    node = shutil.which("node")
    assert node
    function = SOURCE[SOURCE.index(start):SOURCE.index(end, SOURCE.index(start))]
    result = subprocess.run([node, "-e", harness[0] + function + harness[1]], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_local_profile_ui_disables_live_option_without_affecting_cloud():
    run_js("function renderAutotrade(data){", "\nasync function refreshAutotrade", (
        'const assert=require("node:assert/strict");let autotradeCache;'
        'const live={value:"LIVE",disabled:false};const nodes={autoMode:{options:[live]}};'
        'const document={getElementById:id=>nodes[id]??(nodes[id]={})};',
        'renderAutotrade({local_only:true,effective_mode:"SIMULATION"});'
        'assert.equal(live.disabled,true);assert.match(nodes.autoMode.title,/real orders are disabled/);'
        'assert.equal(nodes.autotradeExpiry.textContent,"Not enabled");'
        'renderAutotrade({local_only:false,effective_mode:"SIMULATION"});'
        'assert.equal(live.disabled,false);',
    ))


def test_unresolved_paper_results_are_visible_and_never_presented_as_option_profits():
    run_js("function renderPerformance(s){", "\nfunction renderRisk", (
        'const assert=require("node:assert/strict");const node={};'
        'const document={getElementById:()=>node};',
        'renderPerformance({performance:{decided:0,unresolved_data:3}});'
        'assert.match(node.innerHTML,/3 not scored/);assert.match(node.innerHTML,/Outcome unknown/);'
        'assert.match(node.innerHTML,/not verified option profitability/);'
        'assert.doesNotMatch(node.innerHTML,/win rate/);',
    ))


def test_research_card_does_not_claim_calibrated_reliability():
    assert 'historical reliability score' not in SOURCE
    assert 'research score; not a calibrated win probability' in SOURCE
