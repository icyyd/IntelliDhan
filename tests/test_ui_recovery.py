from pathlib import Path
import shutil
import subprocess


SOURCE = Path("web/index.html").read_text(encoding="utf-8")


def _section(start, end):
    return SOURCE[SOURCE.index(start):SOURCE.index(end, SOURCE.index(start))]


def _run(script):
    node = shutil.which("node")
    assert node, "Node.js is required for inline terminal behavior tests"
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout


def test_database_outage_honors_retry_after_without_repeating_requests_or_signing_out():
    functions = _section("function backendRetryDelay", "\nasync function refresh(){")
    _run(r"""
const assert=require("node:assert/strict");
let apiRetryAt=0,sessionNeedsRecovery=false,dataConnectionUnavailable=false;
let workspaceRefreshFailed=false,lastState=null,ownerAuthenticated=true;
let requests=0,closed=0,cleared=0;
const pauseLiveConnection=()=>{closed++;};
const clearPersonalState=()=>{cleared++;ownerAuthenticated=false;};
const setHealth=()=>{},updateRefreshStatus=()=>{},renderDataQualityNotice=()=>{};
const fetch=async(_url,options)=>{requests++;if(options?.method==="DELETE")return {
  ok:true,status:200,json:async()=>({authenticated:false,session_revoked:false})
};return {
  ok:false,status:503,headers:{get:()=>"1800"},
  json:async()=>({code:"DATABASE_UNAVAILABLE",retry_after_seconds:60,detail:"Storage unavailable"})
};};
""" + functions + r"""
(async()=>{
  await assert.rejects(fetchJSON("/api/state"),e=>e.status===503&&e.retryAfterMs===1800000);
  await assert.rejects(fetchJSON("/api/auth/session"),e=>e.code==="DATABASE_UNAVAILABLE");
  assert.equal(requests,1);assert.equal(closed,1);assert.equal(cleared,0);
  assert.equal(ownerAuthenticated,true);assert.equal(sessionNeedsRecovery,true);
  assert.equal(dataConnectionUnavailable,true);assert.equal(workspaceRefreshFailed,true);
  assert.ok(apiRetryAt>Date.now()+1799000);
  const dateHeader=new Date(Date.now()+120000).toUTCString();
  assert.ok(backendRetryDelay({headers:{get:()=>dateHeader}}, {})>118000);
  assert.equal(backendRetryDelay({headers:{get:()=>null}}, {}),60000);
  const signedOut=await fetchJSON("/api/auth/session",{method:"DELETE"});
  assert.equal(signedOut.authenticated,false);assert.equal(requests,2);
  await assert.rejects(fetchJSON("/api/autotrade/disarm",{method:"POST"}));
  assert.equal(requests,2);
})().catch(e=>{console.error(e);process.exit(1);});
""")


def test_explicit_logout_discards_private_view_after_cookies_clear_during_outage():
    functions = _section("async function signOutOwner(){", "\nasync function loadPreferences")
    _run(r"""
const assert=require("node:assert/strict");
let ownerAuthenticated=true,cleared=0,opened=0;
const note="Signed out on this browser. Server-side session revocation could not be completed while the database is unavailable.";
const fetchJSON=async()=>({authenticated:false,session_revoked:false,detail:note});
const clearPersonalState=()=>{ownerAuthenticated=false;cleared++;};
const values=new Map();
const sessionStorage={setItem:(key,value)=>values.set(key,value),getItem:key=>values.get(key),removeItem:key=>values.delete(key)};
const status={textContent:""},dialog={open:false,showModal:()=>{opened++;}};
const document={getElementById:id=>id==="owner-dialog"?dialog:status};
""" + functions + r"""
(async()=>{
  await signOutOwner();
  assert.equal(ownerAuthenticated,false);assert.equal(cleared,1);
  showSignOutNotice();
  assert.equal(status.textContent,note);assert.equal(opened,1);
  assert.equal(values.size,0);
  showSignOutNotice();assert.equal(opened,1);
})().catch(e=>{console.error(e);process.exit(1);});
""")


def test_session_outage_preserves_last_verified_identity_and_recovers():
    functions = _section("async function refreshOwnerSession(){", "\nasync function openOwnerDialog")
    _run(r"""
const assert=require("node:assert/strict");
let sessionRefreshInFlight=null,sessionNeedsRecovery=false,ownerAuthenticated=true;
let accountUser={display_name:"Tester",role:"ADMIN"},unavailable=0,requests=0;
const markDataUnavailable=()=>{unavailable++;};
const renderWatchlists=()=>{},renderAccountSession=()=>{};
const loadWatchlists=async()=>{},loadSavedScreens=async()=>{};
const loadPreferences=async()=>{},loadCapitalLimits=async()=>{};
const document={getElementById:()=>({style:{},setAttribute:()=>{}})};
const fetchJSON=async()=>{
  requests++;
  if(requests===1) throw Object.assign(new Error("Storage temporarily unavailable"),{status:503});
  return {configured:true,authenticated:true,user:accountUser};
};
""" + functions + r"""
(async()=>{
  const failed=await refreshOwnerSession();
  assert.equal(failed.unavailable,true);assert.equal(failed.authenticated,true);
  assert.equal(failed.configured,undefined);assert.equal(ownerAuthenticated,true);
  assert.equal(sessionNeedsRecovery,true);assert.equal(unavailable,1);
  const recovered=await refreshOwnerSession();
  assert.equal(recovered.authenticated,true);assert.equal(sessionNeedsRecovery,false);
  assert.equal(requests,2);
})().catch(e=>{console.error(e);process.exit(1);});
""")


def test_hidden_workspace_and_retry_window_do_no_network_work():
    functions = _section('function updateRefreshStatus(mode="idle"){', "\nfunction focusMetric")
    _run(r"""
const assert=require("node:assert/strict");
const AUTO_REFRESH_MS=120000;
let workspaceRefreshInFlight=null,workspaceRefreshScope=null,lastWorkspaceRefreshAt=0,ownerAuthenticated=true;
let apiRetryAt=0,workspaceRefreshFailed=false,sessionNeedsRecovery=false,activeView="0dte",discoverLoaded=false;
let calls=0,homeCalls=0;
const status={classList:{toggle:()=>{}}},label={textContent:""};
const document={visibilityState:"hidden",getElementById:id=>id==="refreshStatus"?status:label};
const fmtAge=()=>"just now";
const counted=async()=>{calls++;return true;};
const homeOnly=async()=>{homeCalls++;return true;};
const refreshCalibration=counted,refresh=counted,refreshAutotrade=counted;
const refreshBriefing=homeOnly,refreshDailyBrief=homeOnly,refreshFocus=homeOnly;
""" + functions + r"""
(async()=>{
  assert.equal(await refreshWorkspace(),false);assert.equal(calls,0);
  document.visibilityState="visible";apiRetryAt=Date.now()+1800000;
  assert.equal(await refreshWorkspace(),false);assert.equal(calls,0);
  assert.ok(label.textContent.startsWith("Updates paused"));
  apiRetryAt=0;
  assert.equal(await refreshWorkspace(),true);assert.equal(calls,3);assert.equal(homeCalls,0);
  activeView="signals";
  assert.equal(await refreshWorkspace(),true);assert.equal(calls,6);assert.equal(homeCalls,3);
})().catch(e=>{console.error(e);process.exit(1);});
""")


def test_websocket_storage_close_checks_session_before_backoff_and_hidden_tabs_disconnect():
    functions = _section("let wsRetry = 1000", "\nlet resizeTimer;")
    _run(r"""
const assert=require("node:assert/strict");
let ownerAuthenticated=true,apiRetryAt=0,sessionNeedsRecovery=false,probes=0,updates=0;
let timers=[],sockets=[];
const document={visibilityState:"visible",getElementById:()=>({open:false})};
const location={protocol:"https:",host:"example.test"};
const setTimeout=(fn,delay)=>{const timer={fn,delay};timers.push(timer);return timer;};
const clearTimeout=timer=>{if(timer)timer.cancelled=true;};
const markDataUnavailable=()=>{};
const refresh=()=>{updates++;},refreshAutotrade=()=>{},refreshAutotradeJournal=()=>{};
const refreshOwnerSession=async()=>{probes++;apiRetryAt=Date.now()+1800000;return {unavailable:true};};
class WebSocket{
  constructor(){sockets.push(this);}
  close(){this.closed=true;}
}
""" + functions + r"""
(async()=>{
  connectWS();assert.equal(sockets.length,1);
  await sockets[0].onclose({code:1013});
  assert.equal(probes,1);assert.equal(ownerAuthenticated,true);
  assert.ok(timers.at(-1).delay>=1799000);
  document.visibilityState="hidden";
  pauseLiveConnection();connectWS();pollVisibleAutotrade();
  assert.equal(sockets.length,1);assert.equal(timers.at(-1).cancelled,true);
  apiRetryAt=0;document.visibilityState="visible";connectWS();
  sockets[1].onmessage();sockets[1].onmessage();
  assert.equal(timers.filter(timer=>!timer.cancelled).length,1);
  timers.at(-1).fn();assert.equal(updates,1);
  pauseLiveConnection();assert.equal(sockets[1].closed,true);
})().catch(e=>{console.error(e);process.exit(1);});
""")


def test_unavailable_transport_suppresses_cached_signal_actions():
    functions = _section("function activeAlerts(s){", "\nfunction selectedAlert")
    _run(r"""
const assert=require("node:assert/strict");
let dataConnectionUnavailable=true;
const rawActiveAlerts=()=>[{alert_id:"old",symbol:"SPY"}];
const isDataQuarantined=()=>false,symbolDataQuality=()=>({});
""" + functions + r"""
assert.deepEqual(activeAlerts({}),[]);
dataConnectionUnavailable=false;
assert.equal(activeAlerts({}).length,1);
""")


def test_background_refresh_and_quick_levels_never_request_paid_ai_review():
    workspace = _section("async function refreshWorkspace(){", "\nfunction focusMetric")
    quick_levels = _section("async function runHomeSearch(symbol){", '\ndocument.getElementById("homeStockSearchForm")')
    assert "/api/dossier" not in quick_levels
    assert "/api/analyze/" in quick_levels
    assert "analyzeStock(" not in workspace
    assert "reviewFocusNameWithAI(" not in workspace
    assert "setInterval(pollVisibleAutotrade, 15000);" in SOURCE
    assert "else if(!session.unavailable) clearPersonalState" in SOURCE
