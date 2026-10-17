import { $, el, esc, api } from './dom.js';
import { state, requestNewProject } from './state.js';
import { emptyState } from './ui.js';
import { renderOverview, renderMetrics, renderDefine } from './metrics.js';
import { renderData } from './data.js';
import { renderChat } from './chat.js';

const ICONS={
  overview:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>',
  metrics:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/></svg>',
  data:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></svg>',
  chat:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
  define:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h16M4 12h10M4 17h7"/></svg>',
};
const NAV=[['overview','개요'],['metrics','지표'],['data','데이터 탐색'],['chat','AI 채팅'],['define','정의(YAML)']];

export function buildNav(){
  const nav=$('#nav'); nav.innerHTML='<div class="sec">작업 공간</div>';
  for(const [v,label] of NAV){
    const a=el('a'); a.dataset.view=v; a.className=v===state.view?'active':'';
    a.innerHTML=ICONS[v]+'<span>'+label+'</span>';
    a.onclick=()=>setView(v); nav.appendChild(a);
  }
}
export function setView(v){state.view=v;buildNav();$('#cbView').textContent=(NAV.find(x=>x[0]===v)||[])[1]||v;render();}

// ---------- orgs ----------
export async function loadOrgs(){
  const {projects}=await api('/api/projects'); state.projects=projects;
  const menu=$('#orgMenu'); menu.innerHTML='';
  for(const o of state.projects){
    const row=el('div'); row.style.cssText='display:flex;align-items:center';
    const b=el('button'); b.style.flex='1';
    b.innerHTML='<span class="av" style="width:20px;height:20px;font-size:11px">'+esc(o.name[0]||'?')+'</span><span style="flex:1;text-align:left">'+esc(o.name)+'</span>'+(o.mode==='live'?'<span class="badge live">live</span>':'<span class="badge">copy</span>');
    b.onclick=()=>{setOrg(o.key);toggleMenu(false);};
    const del=el('button'); del.textContent='🗑'; del.title='단체 삭제'; del.style.cssText='flex:none;width:40px;color:var(--muted)';
    del.onclick=async(e)=>{e.stopPropagation(); if(!confirm('단체 “'+o.name+'”와 그 안의 모든 데이터·지표를 삭제할까요?'))return;
      try{await api('/api/projects/'+encodeURIComponent(o.key),{method:'DELETE'}); if(state.project===o.key)state.project=null; await loadOrgs();}catch(err){alert(err.message);}};
    row.append(b,del); menu.appendChild(row);
  }
  const nb=el('button','new','＋ 새 단체 만들기'); nb.onclick=()=>{requestNewProject();toggleMenu(false);}; menu.appendChild(nb);
  if(state.projects.length){ if(!state.project||!state.projects.find(o=>o.key===state.project))state.project=state.projects[0].key; setOrg(state.project); }
  else { state.project=null; $('#orgNm').textContent='단체 없음'; $('#orgAv').textContent='–'; $('#cbOrg').textContent='—'; render(); }
}
function setOrg(key){
  state.project=key; const o=state.projects.find(x=>x.key===key); if(!o)return;
  state.messages=[]; state.session=null;   // 단체 바뀌면 대화도 새로 시작
  state.mode=o.mode; $('#orgNm').textContent=o.name; $('#orgAv').textContent=(o.name[0]||'?');
  $('#cbOrg').textContent=o.name;
  $('#cbMode').textContent=o.mode==='live'?'라이브':'복사본'; $('#cbMode').className='badge'+(o.mode==='live'?' live':'');
  render();
}
function toggleMenu(f){$('#orgMenu').classList.toggle('show',f===undefined?undefined:f)}




// ---------- view router ----------
export function render(){
  buildNav(); $('#cbView').textContent=(NAV.find(x=>x[0]===state.view)||[])[1]||state.view;
  const c=$('#content'); 
  if(!state.project){ c.innerHTML=''; c.appendChild(emptyState('단체를 먼저 만드세요','좌측 상단 단체 메뉴에서 데이터를 연결해 시작합니다.',true)); return; }
  if(state.view==='overview')return renderOverview(c);
  if(state.view==='metrics')return renderMetrics(c);
  if(state.view==='data')return renderData(c);
  if(state.view==='chat')return renderChat(c);
  if(state.view==='define')return renderDefine(c);
}
