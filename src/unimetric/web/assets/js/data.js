import { el, esc, api } from './dom.js';
import { state } from './state.js';
import { vhead, emptyState } from './ui.js';
import { buildTable } from './results.js';

// ---------- Data explorer ----------
export async function renderData(c){
  c.innerHTML=''; vhead(c,'데이터 탐색','소스 테이블을 직접 들여다봅니다 (복사 없이 라이브 조회)');
  const wrap=el('div'); wrap.style.cssText='display:grid;grid-template-columns:240px 1fr;gap:18px;align-items:start';
  const left=el('div','mlist'),right=el('div'); wrap.append(left,right); c.appendChild(wrap);
  const {tables}=await api('/api/tables?project='+encodeURIComponent(state.project));
  if(!tables.length){left.appendChild(emptyState('테이블 없음',''));return;}
  tables.forEach((t,i)=>{const it=el('div','mitem');it.innerHTML='<div class="ic">▦</div><div class="nm">'+esc(t)+'</div>';
    it.onclick=()=>{[...left.children].forEach(x=>x.classList.remove('active'));it.classList.add('active');showTable(right,t);};
    left.appendChild(it);if(i===0)it.classList.add('active');});
  showTable(right,tables[0]);
}
async function showTable(right,t){
  right.innerHTML='';const p=el('div','panel');p.innerHTML='<div class="ph"><span class="gr">'+esc(t)+'</span><span class="faint">미리보기 200행</span></div>';
  const pb=el('div','pb');pb.style.overflowX='auto';pb.innerHTML='<span class="muted">불러오는 중…</span>';p.appendChild(pb);right.appendChild(p);
  try{const r=await api('/api/preview?project='+encodeURIComponent(state.project)+'&table='+encodeURIComponent(t));pb.innerHTML='';pb.appendChild(buildTable(r.rows));}
  catch(e){pb.innerHTML='<span class="err">'+esc(e.message)+'</span>';}
}
