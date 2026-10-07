from pathlib import Path
import shutil
import subprocess


SOURCE = Path("web/index.html").read_text(encoding="utf-8")


def test_terminal_reduces_visible_navigation_and_duplicate_actions():
    mobile_start = SOURCE.index('<nav class="mobile-nav"')
    mobile_end = SOURCE.index("</nav>", mobile_start)
    mobile = SOURCE[mobile_start:mobile_end]
    assert mobile.count("mobile-nav-item") == 3
    assert 'id="mobileDeskSelect"' in mobile
    assert '<option value="" disabled>Desks</option>' in mobile
    assert 'onclick="openBudgets()" aria-label="Open capital' not in SOURCE
    assert 'id="refreshFocusButton"' not in SOURCE
    assert "Refresh feeds" not in SOURCE
    assert "data-focus-ai" not in SOURCE
    assert "data-focus-watch" not in SOURCE
    assert 'class="focus-card-actions single"' in SOURCE


def test_secondary_evidence_is_progressive_disclosure():
    assert '<details class="bottom-drawer">' in SOURCE
    assert 'class="bottom-drawer-summary"' in SOURCE
    assert "Evidence &amp; paper results" in SOURCE


def test_workspace_refresh_contract_is_two_minutes_and_visibility_aware():
    assert "const AUTO_REFRESH_MS = 120000;" in SOURCE
    assert "setInterval(refreshWorkspace, AUTO_REFRESH_MS);" in SOURCE
    assert 'setInterval(updateRefreshStatus, 15000);' in SOURCE
    assert 'document.addEventListener("visibilitychange"' in SOURCE
    assert "if(workspaceRefreshInFlight){" in SOURCE
    assert 'workspaceRefreshScope!=="authenticated"' in SOURCE
    assert 'refreshAutotrade({force:true})' in SOURCE
    assert 'role="status" aria-live="polite" aria-atomic="true"' in SOURCE
    assert "loadDiscoveryPresets();\nrefreshCalibration();" not in SOURCE
    assert "setInterval(refreshDailyBrief, 300000);" not in SOURCE
    assert "setInterval(refreshFocus, 300000);" not in SOURCE


def test_concurrent_workspace_refreshes_share_one_request_batch():
    node = shutil.which("node")
    assert node, "Node.js is required for inline terminal behavior tests"
    start = SOURCE.index('function updateRefreshStatus(mode="idle"){')
    end = SOURCE.index("\n}\n\nfunction focusMetric", start) + 2
    function_source = SOURCE[start:end]
    harness = r"""
const AUTO_REFRESH_MS=120000;
let workspaceRefreshInFlight=null,workspaceRefreshScope=null,lastWorkspaceRefreshAt=0,ownerAuthenticated=true;
let apiRetryAt=0,workspaceRefreshFailed=false,sessionNeedsRecovery=false,activeView="signals",discoverLoaded=false;
let calls=0;
const status={classList:{toggle:()=>{}}};
const label={textContent:""};
const document={getElementById:(id)=>id==="refreshStatus"?status:label};
const fmtAge=()=>"just now";
const pause=()=>new Promise(resolve=>setTimeout(resolve,5));
const counted=async()=>{ calls+=1; await pause(); return true; };
const refreshCalibration=counted,refresh=counted,refreshBriefing=counted;
const refreshDailyBrief=counted,refreshFocus=counted,refreshAutotrade=counted;
""" + function_source + r"""
Promise.all([refreshWorkspace(),refreshWorkspace()]).then(()=>{
  if(calls!==6) process.exit(1);
  if(!lastWorkspaceRefreshAt) process.exit(2);
});
"""
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_failed_workspace_refresh_keeps_old_freshness_and_reports_delay():
    node = shutil.which("node")
    assert node, "Node.js is required for inline terminal behavior tests"
    start = SOURCE.index('function updateRefreshStatus(mode="idle"){')
    end = SOURCE.index("\n}\n\nfunction focusMetric", start) + 2
    function_source = SOURCE[start:end]
    harness = r"""
const AUTO_REFRESH_MS=120000;
let workspaceRefreshInFlight=null,workspaceRefreshScope=null,lastWorkspaceRefreshAt=0,ownerAuthenticated=true;
let apiRetryAt=0,workspaceRefreshFailed=false,sessionNeedsRecovery=false,activeView="signals",discoverLoaded=false;
const status={classList:{toggle:()=>{}}};
const label={textContent:""};
const document={getElementById:(id)=>id==="refreshStatus"?status:label};
const fmtAge=()=>"just now";
const failed=async()=>false;
const refreshCalibration=failed,refresh=failed,refreshBriefing=failed;
const refreshDailyBrief=failed,refreshFocus=failed,refreshAutotrade=failed;
""" + function_source + r"""
refreshWorkspace().then(result=>{
  if(result!==false) process.exit(1);
  if(lastWorkspaceRefreshAt!==0) process.exit(2);
  if(label.textContent!=="Refresh delayed") process.exit(3);
  updateRefreshStatus();
  if(label.textContent!=="Refresh delayed") process.exit(4);
});
"""
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_sign_in_queues_authenticated_refresh_after_anonymous_batch():
    node = shutil.which("node")
    assert node, "Node.js is required for inline terminal behavior tests"
    start = SOURCE.index('function updateRefreshStatus(mode="idle"){')
    end = SOURCE.index("\n}\n\nfunction focusMetric", start) + 2
    function_source = SOURCE[start:end]
    harness = r"""
const AUTO_REFRESH_MS=120000;
let workspaceRefreshInFlight=null,workspaceRefreshScope=null,lastWorkspaceRefreshAt=0,ownerAuthenticated=false;
let apiRetryAt=0,workspaceRefreshFailed=false,sessionNeedsRecovery=false,activeView="signals",discoverLoaded=false;
let calls=0,calibrationCalls=0,releaseCalibration;
const status={classList:{toggle:()=>{}}};
const label={textContent:""};
const document={getElementById:(id)=>id==="refreshStatus"?status:label};
const fmtAge=()=>"just now";
const refreshCalibration=()=>{
  calls+=1; calibrationCalls+=1;
  if(calibrationCalls===1) return new Promise(resolve=>{releaseCalibration=()=>resolve(true);});
  return Promise.resolve(true);
};
const counted=async()=>{calls+=1; return true;};
const refresh=counted,refreshBriefing=counted,refreshDailyBrief=counted;
const refreshFocus=counted,refreshAutotrade=counted;
""" + function_source + r"""
const anonymous=refreshWorkspace();
ownerAuthenticated=true;
const authenticated=refreshWorkspace();
releaseCalibration();
Promise.all([anonymous,authenticated]).then(()=>{
  if(calls!==7) process.exit(1);
  if(!lastWorkspaceRefreshAt) process.exit(2);
});
"""
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_forced_autotrade_refresh_reads_after_inflight_poll():
    node = shutil.which("node")
    assert node, "Node.js is required for inline terminal behavior tests"
    start = SOURCE.index("async function refreshAutotrade({force=false}={}){")
    end = SOURCE.index("\nfunction listValue", start)
    function_source = SOURCE[start:end]
    harness = r"""
let ownerAuthenticated=true,autotradeCache=null,autotradeRefreshInFlight=null;
let calls=0,releaseOld,rendered="";
const fetchJSON=()=>{
  calls+=1;
  if(calls===1) return new Promise(resolve=>{releaseOld=()=>resolve({effective_mode:"ARMED"});});
  return Promise.resolve({effective_mode:"OFF"});
};
const renderAutotrade=data=>{rendered=data.effective_mode;};
""" + function_source + r"""
const poll=refreshAutotrade();
const forced=refreshAutotrade({force:true});
releaseOld();
Promise.all([poll,forced]).then(()=>{
  if(calls!==2) process.exit(1);
  if(rendered!=="OFF") process.exit(2);
});
"""
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
