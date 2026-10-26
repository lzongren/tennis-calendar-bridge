"""Self-contained discovery screen; no third-party browser scripts or API credentials."""

from __future__ import annotations

import html
import json

from .models import Config


def discovery_html(config: Config) -> str:
    return (
        PAGE.replace("__HOME__", html.escape(config.app.base_path + "/", quote=True))
        .replace(
            "__API__", json.dumps(config.app.base_path + "/api/discovery").replace("<", "\\u003c")
        )
        .replace("__ZONE__", html.escape(config.app.timezone, quote=True))
    )


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0e7c66"><title>Courses | Tennis Calendar</title>
<link rel="icon" href="__HOME__icons/icon-192.png">
<style>
*{box-sizing:border-box}body{margin:0;background:#f7f8f5;color:#18221d;font:15px system-ui,sans-serif;letter-spacing:0}
main{max-width:1280px;margin:auto;padding:max(22px,env(safe-area-inset-top)) 24px 48px}
a{color:#087863;text-underline-offset:3px}header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #d9dfda;padding-bottom:20px;gap:16px}
h1{font-size:28px;margin:0}h2{font-size:19px;margin:0}h3{font-size:16px;margin:0 0 8px}p{line-height:1.5}
button,input,select,textarea{font:inherit;border:1px solid #b8c5bd;border-radius:6px;background:white;color:inherit;padding:10px 12px;min-height:44px}
button{cursor:pointer}button:hover{background:#e6eee9}button:disabled{opacity:.5;cursor:wait}button.primary{background:#0e7c66;color:white;border-color:#0e7c66}
button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #bf762c;outline-offset:2px}
.toolbar{display:flex;align-items:center;flex-wrap:wrap;gap:10px;padding:20px 0}.toolbar select{flex:1;min-width:150px;max-width:440px}.spacer{flex:1}.muted{color:#627168}.small{font-size:13px}.notice{color:#865313;background:#fff0d5;padding:12px;border-radius:4px;margin:0 0 16px}
.range{font-size:17px;font-weight:650;min-width:190px}.icon{width:44px;padding:8px;font-size:22px}.segment{display:flex}.segment button{border-radius:0}.segment button:first-child{border-radius:6px 0 0 6px}.segment button:last-child{border-radius:0 6px 6px 0}.segment button[aria-pressed=true]{background:#dcefe7;border-color:#0e7c66}
.week{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));border-top:1px solid #d3ddd5;border-left:1px solid #d3ddd5;min-height:320px;background:white}.day{border-right:1px solid #d3ddd5;border-bottom:1px solid #d3ddd5;padding:8px;min-width:0}.day h3{padding:4px 0 10px;color:#59675f}.day.today h3{color:#087863}
.course{border-left:3px solid #0e7c66;background:#edf5f0;padding:10px;margin:0 0 10px;overflow-wrap:anywhere;border-radius:4px}.course:nth-of-type(even){background:#eef2f9;border-color:#5578b0}.course a{font-weight:650;display:block;line-height:1.4}.course p{margin:5px 0}.course time{font-size:13px;font-weight:650}.course .price{color:#744c16}.list .day{border:0;background:transparent;padding:14px 0}.list .course{background:white;border:1px solid #d3ddd5;border-left:3px solid #0e7c66;display:grid;grid-template-columns:160px minmax(0,1fr);gap:12px}.list .day:has(.empty){display:none}
.empty{color:#7c8980;font-size:13px}.sources{margin-top:28px;border-top:1px solid #d3ddd5;padding-top:20px}.source{padding:12px 0;border-bottom:1px solid #e0e6e1;overflow-wrap:anywhere}.source a{display:block}.pending{margin-top:28px}.pending article{padding:15px 0;border-bottom:1px solid #d3ddd5}.pending p{margin:6px 0}
dialog{width:min(560px,calc(100% - 28px));border:1px solid #cbd5ce;border-radius:8px;padding:24px;max-height:90dvh}dialog::backdrop{background:#15261d66}label{display:grid;gap:6px;margin-top:16px}textarea{resize:vertical;min-height:85px;width:100%}.actions{display:flex;justify-content:flex-end;gap:10px;margin-top:22px}input,select{max-width:100%;min-width:0}.error{color:#a52b31;white-space:pre-wrap;overflow-wrap:anywhere}[hidden]{display:none!important}
@media(max-width:760px){main{padding-right:14px;padding-left:14px}header h1{font-size:24px}.week{display:block;min-height:0;border:0;background:transparent}.week .day{border:0;padding:12px 0}.week .day:has(.empty){display:none}.week .course{background:white;border:1px solid #d3ddd5;border-left:3px solid #0e7c66}.list .course{display:block}.toolbar{gap:8px;padding:14px 0}.range{min-width:0;flex:1;font-size:15px}.segment{display:none}.toolbar select{max-width:none}.toolbar .spacer{display:none}header{gap:8px}header a{font-size:14px}.course{padding:12px}#status{line-height:1.6}}
@media(max-width:760px){#locations{flex:1 0 100%;width:100%;max-width:100%}}
</style></head><body><main>
<header><h1>Find Courses</h1><nav><a href="__HOME__walls">Practice walls</a> · <a href="__HOME__">My schedule</a></nav></header>
<div class="toolbar"><select id="locations" aria-label="Saved location"></select><button id="add" class="primary">Add location</button><span class="spacer"></span><button id="refresh">Refresh</button><button id="remove">Remove</button></div>
<p id="status" class="muted small" role="status" aria-live="polite"></p>
<p id="error" class="error" role="alert"></p>
<p class="notice small">Published schedules, not confirmed bookings. Availability and prices must be confirmed with the club.</p>
<div class="toolbar"><button id="prev" class="icon" aria-label="Previous week" title="Previous week">&#8592;</button><strong id="range" class="range"></strong><button id="next" class="icon" aria-label="Next week" title="Next week">&#8594;</button><button id="today">Today</button><span class="spacer"></span><div class="segment" aria-label="Course views"><button id="calendarMode" aria-pressed="true">Calendar</button><button id="listMode" aria-pressed="false">List</button></div></div>
<div id="calendar" class="week"></div><p id="emptyWeek" class="muted" hidden>No source-backed sessions in this week.</p>
<section id="pending" class="pending"></section><section id="sources" class="sources"></section>
<dialog id="locationDialog"><form id="locationForm"><h2>Add location</h2><label>City or area<input name="query" required maxlength="120" placeholder="San Jose, CA and nearby cities"></label><label>Skill level<input name="level" maxlength="80" placeholder="NTRP 3.0"></label><label>Time zone<input name="timezone" required value="__ZONE__"></label><label>Public course URLs (optional)<textarea name="sources" placeholder="https://club.example.com/adult-clinics"></textarea></label><div class="actions"><button type="button" data-close="locationDialog">Cancel</button><button class="primary" type="submit">Find courses</button></div></form></dialog>
<dialog id="authDialog"><form id="authForm"><h2>Administrator access</h2><label>Admin token<input id="adminToken" type="password" required autocomplete="off"></label><div class="actions"><button type="button" data-close="authDialog">Cancel</button><button class="primary" type="submit">Continue</button></div></form></dialog>
<script>
const API=__API__;
let rows=[],selected=null,offset=0,mode='calendar',token='',enabled=false,authResolve=null,pollTimer,initialized=null;
const $=id=>document.getElementById(id);
const text=(tag,value,cls)=>{const el=document.createElement(tag);el.textContent=value;if(cls)el.className=cls;return el};
function link(label,url){const a=text('a',label);try{const u=new URL(url);if(['https:','http:'].includes(u.protocol)){a.href=u.href;a.target='_blank';a.rel='noopener noreferrer'}}catch{}return a}
function current(){return rows.find(r=>r.id===selected)}
function localDay(){return new Intl.DateTimeFormat('en-CA',{timeZone:current()?.timezone||'__ZONE__',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())}
function dayDate(day){return new Date(day+'T12:00:00Z')}
function dayKey(d){return d.toISOString().slice(0,10)}
function monthDay(d){return d.toLocaleDateString(undefined,{month:'short',day:'numeric',timeZone:'UTC'})}
function stamp(s){return new Date(s).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short',timeZone:current()?.timezone})}
function courseNode(c,s){const box=document.createElement('article');box.className='course';box.dataset.start=s.starts_at;box.dataset.end=s.ends_at;const timing=document.createElement('div');timing.append(text('time',new Intl.DateTimeFormat(undefined,{hour:'numeric',minute:'2-digit',timeZone:current().timezone}).format(new Date(s.starts_at))+' - '+new Intl.DateTimeFormat(undefined,{hour:'numeric',minute:'2-digit',timeZone:current().timezone}).format(new Date(s.ends_at))));const details=document.createElement('div');details.append(link(c.title,c.signup_url));details.append(text('p',[c.club,c.venue].filter(Boolean).join(' / '),'small'));if(c.level)details.append(text('p',c.level,'small muted'));if(c.price)details.append(text('p',c.price,'small price'));if(c.schedule_basis){const labels={weekly_timetable:'Weekly timetable · Confirm date',year_inferred:'Year inferred · Confirm date',published_dates:'Published dates'};const label=text('p',labels[c.schedule_basis]||'Confirm date','small confidence');label.title=c.schedule_note||'';details.append(label)}box.append(timing,details);return box}
function initialWeek(){const row=current();if(!row||initialized===row.id)return;const future=(row.result?.courses||[]).flatMap(c=>c.sessions).filter(s=>new Date(s.ends_at)>new Date()).sort((a,b)=>a.starts_at.localeCompare(b.starts_at));if(!future.length)return;const begin=dayDate(localDay());begin.setUTCDate(begin.getUTCDate()-((begin.getUTCDay()+6)%7));offset=Math.max(0,Math.min(4,Math.floor((dayDate(future[0].starts_at.slice(0,10))-begin)/604800000)));initialized=row.id}
function render(){const row=current(),result=row?.result;const today=localDay(),begin=dayDate(today);begin.setUTCDate(begin.getUTCDate()-((begin.getUTCDay()+6)%7)+offset*7);const end=new Date(begin);end.setUTCDate(end.getUTCDate()+6);$('range').textContent=monthDay(begin)+' - '+monthDay(end);$('prev').disabled=offset<=0;$('next').disabled=offset>=4;$('refresh').disabled=!enabled||!row||['queued','running'].includes(row.status);$('remove').disabled=!enabled||!row;$('add').disabled=!enabled;
$('status').textContent=!enabled?'Course discovery is not configured.':!row?'No saved locations.':row.query+' · '+row.timezone+' · '+(row.status==='running'?'Checking public course pages…':row.status==='queued'?'Waiting to refresh…':row.status==='error'?'Refresh failed.':'Ready')+(result?' · Last checked '+stamp(result.checked_at):'');$('error').textContent=row?.error||'';
const cal=$('calendar');cal.replaceChildren();cal.className=mode==='calendar'?'week':'list';let count=0;
for(let i=0;i<7;i++){const d=new Date(begin);d.setUTCDate(d.getUTCDate()+i);const key=dayKey(d);const section=document.createElement('section');section.className='day'+(key===today?' today':'');section.append(text('h3',d.toLocaleDateString(undefined,{weekday:'short',day:'numeric',month:'short',timeZone:'UTC'})));const sessions=[];for(const c of result?.courses||[])for(const s of c.sessions)if(s.starts_at.slice(0,10)===key)sessions.push([c,s]);sessions.sort((a,b)=>a[1].starts_at.localeCompare(b[1].starts_at));for(const [c,s] of sessions)section.append(courseNode(c,s));if(!sessions.length)section.append(text('span','No sessions','empty'));count+=sessions.length;cal.append(section)}$('emptyWeek').hidden=count>0;
const pending=$('pending');pending.replaceChildren();const undated=(result?.courses||[]).filter(c=>!c.sessions.length);if(undated.length){pending.append(text('h2','Unresolved schedules'));for(const c of undated){const item=document.createElement('article');item.append(link(c.title,c.signup_url),text('p',[c.club,c.level,c.price].filter(Boolean).join(' · '),'small'));if(c.schedule_note)item.append(text('p',c.schedule_note,'small'));if(c.evidence)item.append(text('p',c.evidence,'small muted'));pending.append(item)}}
const sources=$('sources');sources.replaceChildren();if(result){sources.append(text('h2','Sources'));if(result.partial)sources.append(text('p','Partial results: some sources or web search were unavailable.','notice small'));for(const s of result.sources){const item=document.createElement('div');item.className='source';item.append(link(s.title||new URL(s.url).hostname,s.url),text('span',s.status==='checked'?'Page checked · Seats not checked':'Could not read this page','small muted'));sources.append(item)}}}
async function load(){try{const r=await fetch(API,{cache:'no-store'});if(!r.ok)throw Error('Unable to load courses.');const data=await r.json();rows=data.locations;enabled=data.enabled;if(!rows.some(r=>r.id===selected))selected=rows[0]?.id||null;const picker=$('locations');picker.replaceChildren();for(const row of rows){const option=text('option',row.query+(row.level?' · '+row.level:''));option.value=row.id;picker.append(option)}if(selected)picker.value=selected;initialWeek();render()}catch(e){$('error').textContent=e.message}clearTimeout(pollTimer);pollTimer=setTimeout(load,rows.some(r=>['queued','running'].includes(r.status))?5000:60000)}
async function authorize(){if(token)return token;$('authDialog').showModal();return new Promise(resolve=>authResolve=resolve)}
async function mutate(payload){const key=await authorize();if(!key)return;try{const r=await fetch(API,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+key},body:JSON.stringify(payload)});if(r.status===401){token='';throw Error('Admin token was not accepted.')}const data=await r.json();if(!r.ok)throw Error(data.error||'Request failed.');if(data.id)selected=data.id;await load();return true}catch(e){$('error').textContent=e.message;return false}}
$('authForm').onsubmit=e=>{e.preventDefault();token=$('adminToken').value;$('adminToken').value='';$('authDialog').close();authResolve?.(token);authResolve=null};$('authDialog').addEventListener('close',()=>{if(authResolve){authResolve('');authResolve=null}});
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());$('add').onclick=()=>$('locationDialog').showModal();$('locationForm').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);const payload={action:'add',query:f.get('query'),level:f.get('level'),timezone:f.get('timezone'),sources:String(f.get('sources')).split(/\n/).map(s=>s.trim()).filter(Boolean)};$('locationDialog').close();if(await mutate(payload)){offset=0;e.target.reset()}};
$('locations').onchange=e=>{selected=Number(e.target.value);offset=0;initialized=null;initialWeek();render()};$('refresh').onclick=()=>mutate({action:'refresh',id:selected});$('remove').onclick=()=>{if(confirm('Remove this saved location and its course results?'))mutate({action:'delete',id:selected})};$('prev').onclick=()=>{offset=Math.max(0,offset-1);render()};$('next').onclick=()=>{offset=Math.min(4,offset+1);render()};$('today').onclick=()=>{offset=0;render()};for(const name of ['calendar','list'])$(name+'Mode').onclick=()=>{mode=name;$('calendarMode').setAttribute('aria-pressed',name==='calendar');$('listMode').setAttribute('aria-pressed',name==='list');render()};load();
</script></main></body></html>"""
