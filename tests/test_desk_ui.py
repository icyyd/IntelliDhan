"""Behavioral checks for the beginner desk; tests execute the shipped JS."""

from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "web/assets/desk.js"


def run_js(body: str):
    node = shutil.which("node")
    assert node, "Node.js is required to test the browser controller"
    setup = f"""
const assert = require('node:assert/strict');
const desk = require({str(SCRIPT)!r});
const response = (body, status=200, retry=null) => ({{
  ok:status>=200&&status<300,status,headers:{{get:()=>retry}},json:async()=>body
}});
const user = {{authenticated:true,accounts_enabled:true,user:{{display_name:'Test',role:'TRADER'}}}};
const deferred = () => {{let resolve;const promise=new Promise(r=>resolve=r);return {{resolve,promise}};}};
"""
    result = subprocess.run(
        [node, "-e", setup + "\n(async()=>{\n" + body + "\n})().catch(error=>{console.error(error);process.exit(1);});"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_research_actions_never_become_buy_instructions():
    run_js("""
const research={symbol:'SPY',horizon:'0DTE',research_view:'BUY',next_step:'REVIEW_SETUP',
status:'RESEARCH_ONLY',freshness:'CURRENT',horizon_coverage:'RESEARCH_ONLY',levels:[]};
assert.equal(desk.actionLabel(research),'Wait');
const validated={...research,status:'CURRENT',horizon_coverage:'VALIDATED_SETUP'};
assert.equal(desk.actionLabel(validated),'Review buy setup');
assert.equal(desk.actionLabel(validated,true),'Wait');
assert.equal(desk.actionLabel({...validated,freshness:'UNKNOWN'}),'Wait');
assert.equal(desk.actionLabel({...validated,status:'STALE'}),'Wait');
assert.equal(desk.actionLabel({...validated,next_step:'TRACK_ONLY'}),'Track only');
assert.equal(desk.actionLabel({...validated,research_view:'SELL'}),'Review bearish setup');
const html=desk.signalHTML(research,{vehicle:'OPTION',legs:[{option_type:'PUT'}],dollar_risk:120},false);
assert.match(html,/Buy put candidate · benefits from a fall/);
assert.match(html,/Research only/);assert.match(html,/>Wait</);
assert.doesNotMatch(html,/BTO|STO|% confidence/);
assert.match(html,/No order is placed/);
""")


def test_signal_order_prioritizes_current_qualified_then_research_then_stale():
    run_js("""
const make=(id,as_of,extra={})=>({source_alert_id:id,as_of,horizon:'0DTE',
research_view:'BUY',next_step:'WAIT',status:'RESEARCH_ONLY',freshness:'CURRENT',
horizon_coverage:'RESEARCH_ONLY',...extra});
const qualified={status:'CURRENT',horizon_coverage:'VALIDATED_SETUP',next_step:'REVIEW_SETUP'};
const rows=[make('research-old','2026-09-24T13:00:00Z'),
make('stale-new','2026-09-24T15:30:00Z',{status:'STALE',freshness:'STALE'}),
make('qualified-old','2026-09-24T13:30:00Z',qualified),
make('research-invalid','not-a-date'),
make('other-horizon','2026-09-24T16:00:00Z',{horizon:'SWING'}),
make('research-new','2026-09-24T15:00:00Z'),
make('qualified-new','2026-09-24T14:30:00Z',qualified),
make('unavailable-invalid',null,{status:'UNAVAILABLE',freshness:'UNKNOWN'})];
const original=JSON.stringify(rows);
assert.deepEqual(desk.orderedSignals(rows,'0DTE').map(row=>row.source_alert_id),[
'qualified-new','qualified-old','research-new','research-old','research-invalid','stale-new','unavailable-invalid']);
assert.equal(JSON.stringify(rows),original);
assert.equal(desk.orderedSignals(rows,'SWING').length,1);
assert.deepEqual(desk.orderedSignals(null,'LEAPS'),[]);
const paused=desk.orderedSignals(rows,'0DTE',true);
assert.equal(paused[0].source_alert_id,'stale-new');
assert.ok(paused.every(row=>desk.actionLabel(row,true)==='Wait'));
""")


def test_stale_signal_direction_and_data_time_are_visible_before_disclosure():
    run_js("""
const summary={symbol:'QQQ',horizon:'0DTE',trend:'DOWN',research_view:'SELL',next_step:'WAIT',
status:'STALE',freshness:'STALE',horizon_coverage:'RESEARCH_ONLY',
as_of:'2026-09-24T14:00:00Z',data_as_of:'2026-09-24T14:05:00Z'};
const html=desk.signalHTML(summary,null,false);
const visible=html.split('<details')[0];
assert.match(visible,/Last-known downward/);assert.match(visible,/Data: stale/);
assert.match(visible,/Setup: Sep 24, 10:00 AM ET/);assert.match(visible,/Data: Sep 24, 10:05 AM ET/);
assert.doesNotMatch(visible,/>Downward trend</);assert.match(visible,/>Wait</);
const unknown=desk.signalHTML({...summary,trend:'UNKNOWN',freshness:'UNKNOWN',status:'UNAVAILABLE'},null,false).split('<details')[0];
assert.match(unknown,/Unverified trend/);assert.match(unknown,/Data: unverified/);
const paused=desk.signalHTML({...summary,status:'CURRENT',freshness:'CURRENT'},null,true).split('<details')[0];
assert.match(paused,/Data: updates paused/);assert.match(paused,/Last-known downward/);
""")


def test_analyst_daily_session_dates_and_stale_trend_are_not_live_quotes():
    run_js("""
assert.equal(desk.dailyDateLabel('2026-09-23T04:00:00Z'),'Sep 23, 2026');
assert.equal(desk.dailyDateLabel('2026-01-15T05:00:00Z'),'Jan 15, 2026');
assert.equal(desk.dailyDateLabel('2026-09-23'),'Sep 23, 2026');
assert.equal(desk.dailyDateLabel('invalid'),'date unavailable');
assert.equal(desk.displayTrend('UP',true).label,'Last-known upward');
assert.equal(desk.displayTrend('UP',true).tone,'wait');
assert.equal(desk.displayTrend('UNKNOWN',true).label,'Unverified trend');
assert.equal(desk.displayTrend('UP',false).label,'Upward trend');
""")
    script = SCRIPT.read_text()
    assert "Daily price history dated ${dailyDateLabel(analysis.as_of)}" in script
    assert "const visibleTrend = displayTrend(decision.trend, stale)" in script


def test_output_escaping_links_and_price_units_are_safe():
    run_js(r"""
assert.equal(desk.safeURL('javascript:alert(1)'),null);
assert.equal(desk.safeURL('data:text/html,test'),null);
assert.equal(desk.safeURL('//example.test'),null);
assert.equal(desk.safeURL('https://example.test/'),'https://example.test/');
assert.equal(desk.number(null),null);assert.equal(desk.number(''),null);assert.equal(desk.number(false),null);
assert.equal(desk.number('NaN'),null);assert.equal(desk.number(0),0);
assert.equal(desk.dateLabel('2026-09-24'),'Sep 24, 2026 · daily data');
assert.match(desk.levelHTML({label:'Entry',unit:'OPTION_USD_PER_SHARE',low:1.2,high:1.3}),/Option premium per share/);
assert.match(desk.levelHTML({label:'Stop',unit:'UNDERLYING_USD_PER_SHARE',value:500}),/Stock \/ index level/);
const html=desk.signalHTML({symbol:'<script>bad</script>',reasons:['<img src=x onerror=bad>'],
blockers:['<iframe>'],levels:[],horizon_coverage:'RESEARCH_ONLY'},null,true);
assert.doesNotMatch(html,/<script>|<img|<iframe>/);
assert.match(html,/&lt;script&gt;/);assert.match(html,/updates paused/);
""")


def test_real_playbook_catalog_renders_string_setup_and_horizon_filter():
    from intellidhan_gateway.playbooks import build_playbook_catalog

    catalog = build_playbook_catalog()
    run_js(f"const catalog={json.dumps(catalog)};" + """
const html=desk.playbookHTML(catalog,'0DTE');
for(const item of catalog.playbooks.filter(item=>item.horizon_id==='0DTE')){
assert.equal(typeof item.setup,'string');assert.ok(html.includes(desk.esc(item.setup)));
assert.ok(html.includes(desk.esc(item.exit_review)));
}
assert.doesNotMatch(html,/Setup rules are not supplied/);
assert.ok(!html.includes('Swing trend pullback'));
assert.match(html,/not a promise of profit/);
""")


def test_source_times_do_not_confuse_report_assembly_with_evidence_freshness():
    run_js("""
const html=desk.sourceObservationsHTML({research_generated_at:'2026-09-24T16:00:00Z',
source_observations:[{name:'<SEC>',status:'AVAILABLE',as_of:'2026-08-01T16:00:00Z',
timestamp_kind:'PROVIDER_OBSERVATION',freshness:'NOT_ASSESSED',note:'Retrieval is not a filing date.'}]});
assert.match(html,/&lt;SEC&gt;/);assert.match(html,/Provider retrieval/);
assert.match(html,/not assessed/);assert.match(html,/Aug 1/);
assert.match(html,/This is not evidence freshness/);assert.match(html,/Retrieval is not a filing date/);
""")


def test_transport_outage_preserves_identity_and_honors_cooldown():
    run_js("""
let now=100000, calls=0;
const client=desk.createController({now:()=>now,fetchImpl:async()=>{calls++;return response({code:'DATABASE_UNAVAILABLE'},503,'1800');}});
client.state.session=user;client.state.snapshot={private:'existing'};
assert.equal(await client.refresh(),false);
assert.equal(client.state.session.authenticated,true);
assert.equal(client.state.snapshot.private,'existing');assert.equal(client.state.paused,true);
assert.equal(client.state.retryAt,1900000);
await client.refresh();assert.equal(calls,1);
""")


def test_verified_expired_session_clears_every_private_surface():
    run_js("""
const client=desk.createController({fetchImpl:async()=>response({authenticated:false,accounts_enabled:true})});
Object.assign(client.state,{session:user,snapshot:{secret:true},dossier:{secret:true},journal:{secret:true},
news:{secret:true},brief:{secret:true},watchlists:[{symbols:['SPY']}],symbol:'SPY'});
await client.refresh();
assert.equal(client.state.session.authenticated,false);
for(const key of ['snapshot','dossier','journal','news','brief'])assert.equal(client.state[key],null,key);
assert.deepEqual(client.state.watchlists,[]);assert.equal(client.state.symbol,'');
""")


def test_logout_bypasses_cooldown_and_does_not_claim_server_revocation():
    run_js("""
let calls=0;
const client=desk.createController({fetchImpl:async(url,options)=>{
calls++;assert.equal(url,'/api/auth/session');assert.equal(options.method,'DELETE');
return response({authenticated:false,session_revoked:false});
}});
client.state.session=user;client.state.snapshot={private:true};client.state.retryAt=Date.now()+1800000;
assert.equal(await client.signOut(),true);assert.equal(calls,1);
assert.equal(client.state.session.authenticated,false);assert.equal(client.state.snapshot,null);
assert.match(client.state.accountMessage,/Server-side revocation could not be completed/);
""")


def test_hidden_tabs_do_not_poll_and_concurrent_refreshes_share_one_batch():
    run_js("""
let visible=false,calls=0;
const pending=deferred();
const client=desk.createController({visible:()=>visible,fetchImpl:async(url)=>{
calls++;if(url==='/api/auth/session')return pending.promise;
if(url==='/api/watchlists')return response({watchlists:[]});
return response({});
}});
await client.refresh();assert.equal(calls,0);
visible=true;
const one=client.refresh(),two=client.refresh();
pending.resolve(response(user));await Promise.all([one,two]);
assert.equal(calls,7);assert.equal(client.state.paused,false);
assert.ok(client.state.lastUpdated);
""")


def test_navigation_queues_latest_view_after_old_scoped_refresh():
    run_js("""
const delayed=deferred();let stateCalls=0,journalCalls=0;
const client=desk.createController({fetchImpl:async(url)=>{
if(url==='/api/auth/session')return response(user);
if(url==='/api/state'){stateCalls++;return stateCalls===1?delayed.promise:response({});}
if(url.startsWith('/api/trade-log')){journalCalls++;return response({signals:[],paper_trades:[]});}
if(url==='/api/watchlists')return response({watchlists:[]});
return response({});
}});
const starting=client.refresh();
while(stateCalls===0)await new Promise(resolve=>setImmediate(resolve));
const navigation=client.setView('journal');
delayed.resolve(response({}));await Promise.all([starting,navigation]);
assert.equal(stateCalls,2);assert.equal(journalCalls,1);
assert.ok(client.state.journal);assert.equal(client.state.view,'journal');
""")


def test_anonymous_public_analysis_is_not_cleared_by_session_checks():
    run_js("""
const client=desk.createController({fetchImpl:async(url)=>{
if(url==='/api/auth/session')return response({authenticated:false,accounts_enabled:true});
if(url.startsWith('/api/dossier/'))return response({analysis:{symbol:'SPY'},decision:{freshness:'CURRENT'}});
return response({});
}});
await client.analyze('SPY');assert.equal(client.state.dossier.analysis.symbol,'SPY');
await client.refresh();assert.equal(client.state.dossier.analysis.symbol,'SPY');
assert.equal(client.state.session.authenticated,false);
""")


def test_old_analysis_response_cannot_replace_new_symbol_or_survive_logout():
    run_js("""
const first=deferred(),second=deferred();
const client=desk.createController({fetchImpl:async(url)=>{
if(url.includes('/AAPL?'))return first.promise;
if(url.includes('/SPY?'))return second.promise;
return response({authenticated:false});
}});
const old=client.analyze('AAPL'),latest=client.analyze('SPY');
second.resolve(response({analysis:{symbol:'SPY'}}));await latest;
first.resolve(response({analysis:{symbol:'AAPL'}}));await old;
assert.equal(client.state.dossier.analysis.symbol,'SPY');
const pending=deferred();
const other=desk.createController({fetchImpl:async(url)=>url.startsWith('/api/dossier/')?pending.promise:response({authenticated:false})});
const running=other.analyze('QQQ');await other.signOut();
pending.resolve(response({analysis:{symbol:'QQQ'}}));await running;
assert.equal(other.state.dossier,null);assert.equal(other.state.symbol,'');
""")


def test_explicit_analysis_can_review_but_background_refresh_skips_paid_review():
    run_js("""
const urls=[];
const client=desk.createController({fetchImpl:async(url)=>{urls.push(url);return response({analysis:{symbol:'SPY'}});}});
await client.analyze('SPY');await client.analyze('SPY',{background:true});
assert.equal(urls.length,2);
assert.match(urls[0],/include_review=true/);assert.match(urls[1],/include_review=false/);
assert.ok(urls.every(url=>url.startsWith('/api/dossier/SPY?')));
assert.ok(urls.every(url=>url.includes('include_backtest=false')));
""")


def test_pause_aborts_current_work_and_resume_can_start_a_fresh_batch():
    run_js("""
const old=deferred();let sessions=0;
const client=desk.createController({fetchImpl:async(url)=>{
if(url==='/api/auth/session'){sessions++;return sessions===1?old.promise:response({authenticated:false});}
return response({});
}});
const previous=client.refresh();client.pause();await client.refresh();
assert.equal(sessions,2);assert.equal(client.state.session.authenticated,false);
old.resolve(response(user));await previous;
assert.equal(client.state.session.authenticated,false);
""")


def test_login_uses_only_same_origin_cookie_session_and_no_broker_calls():
    run_js("""
const calls=[];
const client=desk.createController({fetchImpl:async(url,options)=>{
calls.push([url,options]);
if(url==='/api/auth/session')return response(user);
if(url==='/api/watchlists')return response({watchlists:[]});
return response({});
}});
assert.equal(await client.signIn('test@example.test','a-test-password'),true);
const [url,options]=calls[0];assert.equal(url,'/api/auth/session');assert.equal(options.method,'POST');
assert.equal(options.credentials,'same-origin');
assert.deepEqual(JSON.parse(options.body),{email:'test@example.test',password:'a-test-password'});
assert.ok(calls.every(([path])=>!path.includes('/autotrade')&&!path.includes('robinhood')));
assert.ok(calls.every(([,opts])=>!opts.headers?.Authorization));
""")


class Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, _tag, attrs):
        value = dict(attrs).get("id")
        if value:
            self.ids.append(value)


def test_shell_has_unique_ids_accessible_navigation_and_preserves_advanced_tools():
    source = (ROOT / "web/desk.html").read_text()
    parser = Ids()
    parser.feed(source)
    assert len(parser.ids) == len(set(parser.ids))
    assert 'src="/assets/desk.js" defer' in source
    assert 'href="/assets/desk.css"' in source
    assert 'href="/advanced"' in source
    for identifier in ("deskSignals", "deskAnalyst", "deskJournal", "deskAccountDialog"):
        assert identifier in parser.ids
    assert 'role="tablist" aria-label="Holding period"' in source
    assert 'aria-controls="deskSignalPanel"' in source
    script = SCRIPT.read_text()
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "setInterval(() => client.refresh(), REFRESH_MS)" in script
    assert "client.pause()" in script
    assert "/api/autotrade" not in script
    assert "summaries.slice(0,6)" in script
    assert "deskSignalCount" in parser.ids
