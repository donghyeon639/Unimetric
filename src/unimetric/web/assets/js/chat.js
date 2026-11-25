import { $, el, esc, api } from './dom.js';
import { state } from './state.js';
import { selectedProvider } from './providers.js';
import { renderResult } from './results.js';

// ---------- Chat (the star) ----------

function newChat(){state.messages=[];state.session=null;renderChat($('#content'));}
export function renderChat(c){
  c.innerHTML='';
  const grid=el('div','chatgrid');
  // 왼쪽: 세션 목록 (ChatGPT/Claude식)
  const side=el('div','chatside');
  const nc=el('button','btn pri sm','＋ 새 대화');nc.style.width='100%';nc.onclick=newChat;
  const slist=el('div','sesslist');slist.id='sessList';
  side.append(nc,slist);
  // 오른쪽: 대화
  const wrap=el('div','chatwrap');
  const thread=el('div','thread');thread.id='thread';
  const chips=el('div','chips');
  ['답변을 상태별로 세줘','이씨 성을 가진 직원 수','사용자당 평균 답변 수','월별 거래 추이 지표'].forEach(t=>{
    const ch=el('button','chip',t);ch.onclick=()=>{$('#cin').value=t;send();};chips.appendChild(ch);});
  const comp=el('div','composer');
  comp.innerHTML='<textarea id="cin" placeholder="예) ‘게시글 등록 수’ 지표 만들어줘 (삭제분 제외)"></textarea><button class="btn pri" id="csend" style="height:46px">보내기</button>';
  wrap.append(thread,chips,comp);
  grid.append(side,wrap);c.appendChild(grid);
  if(!state.messages.length){addMsg('ai','안녕하세요! 이 단체의 데이터로 무엇이 궁금하세요? 질문하면 관련 테이블을 찾아 답하고, 지표로 저장할 수 있어요.');}
  else state.messages.forEach(m=>renderMsg(thread,m));
  $('#csend',comp).onclick=send;
  $('#cin',comp).addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}});
  loadSessions();
  thread.scrollTop=thread.scrollHeight;
}
// 현재 단체의 세션 목록을 불러와 사이드바에 그린다.
async function loadSessions(){
  const box=$('#sessList');if(!box||!state.project)return;
  try{
    const {sessions}=await api('/api/chat/sessions?project='+encodeURIComponent(state.project));
    box.innerHTML='';
    sessions.forEach(s=>{
      const it=el('div','sessitem'+(s.id===state.session?' active':''));
      const title=(s.title||'새 대화').slice(0,40);
      it.innerHTML='<span class="t">'+esc(title)+'</span><span class="x">×</span>';
      it.querySelector('.t').onclick=()=>openSession(s.id);
      it.querySelector('.x').onclick=async(e)=>{e.stopPropagation();await api('/api/chat/session/'+s.id,{method:'DELETE'});if(s.id===state.session){state.messages=[];state.session=null;}renderChat($('#content'));};
      box.appendChild(it);
    });
  }catch(e){/* 세션 목록 실패는 조용히 무시 */}
}
// 세션을 골라 그 대화를 불러온다.
async function openSession(id){
  try{
    const {messages}=await api('/api/chat/session/'+id);
    state.session=id;
    state.messages=messages.map(m=>({role:m.role==='assistant'?'ai':'user',
      content:(m.content||'').replace(/\n\[(제안 지표|실행 SQL|되물음):[\s\S]*$/,'')}));
    renderChat($('#content'));
  }catch(e){alert(e.message);}
}
function addMsg(role,content,extra){const m={role,content,extra};state.messages.push(m);const th=$('#thread');if(th){renderMsg(th,m);th.scrollTop=th.scrollHeight;}return m;}
function renderMsg(th,m){
  const d=el('div','msg '+(m.role==='user'?'user':'ai'));
  d.innerHTML='<div class="ava">'+(m.role==='user'?'나':'AI')+'</div>';
  const body=el('div');body.style.maxWidth='820px';
  body.appendChild(el('div','bubble',esc(m.content)));
  if(m.extra)renderExtra(body,m.extra);
  d.appendChild(body);th.appendChild(d);
}
// AI 응답의 부가물(선택지 / 지표제안+결과 / 분석SQL+결과)을 렌더.
function renderExtra(body,ex){
  // ① 정보 부족 → 선택지 버튼
  if(ex.clarify){
    const box=el('div','proposal');
    box.innerHTML='<div class="kv">'+esc(ex.clarify.question||'')+'</div>';
    const row=el('div','chips');row.style.marginTop='8px';
    (ex.clarify.options||[]).forEach(opt=>{const b=el('button','chip',esc(opt));b.onclick=()=>sendText(opt);row.appendChild(b);});
    box.appendChild(row);body.appendChild(box);
  }
  // ② 지표 제안(+자동 실행 결과)
  if(ex.spec){
    body.appendChild(proposalCard(ex.spec));
    if(ex.result){const o=el('div');o.style.marginTop='8px';renderResult(o,ex.result);body.appendChild(o);}
    else if(ex.result_error){body.appendChild(el('div','err','결과 실행 실패: '+esc(ex.result_error)));}
  }
  // ③ 분석 답변(조인 등) — SQL + 결과 + "지표로 저장"(자유 SQL 지표)
  if(ex.answer){
    const o=el('div','proposal');o.innerHTML='<div class="pt">📊 분석 결과</div>';
    const r=el('div');renderResult(r,ex.answer);o.appendChild(r);
    const row=el('div','row');row.style.marginTop='10px';
    const sv=el('button','btn pri sm','이 결과를 지표로 저장');
    const msg=el('span','muted');msg.style.marginLeft='8px';
    sv.onclick=async()=>{
      const name=prompt('지표 이름(영문/숫자/밑줄):','');
      if(!name)return;
      try{
        await api('/api/builder/save',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({project:state.project,spec:{
            type:'sql',name:name.trim(),sql:ex.answer.sql,description:'',chart:ex.answer.chart,
          }})});
        sv.textContent='저장됨 ✓';sv.disabled=true;
      }catch(e){msg.innerHTML='<span class="err">'+esc(e.message)+'</span>';}
    };
    row.append(sv,msg);o.append(row);body.appendChild(o);
  } else if(ex.answer_error){body.appendChild(el('div','err','쿼리 실행 실패: '+esc(ex.answer_error)));}
}
function proposalCard(spec){
  const calc=spec.type==='ratio'?`${spec.numerator?.agg}(${spec.numerator?.expr}) / ${spec.denominator?.agg}(${spec.denominator?.expr})`:`${spec.measure?.agg}(${spec.measure?.expr})`;
  const card=el('div','proposal');
  card.innerHTML='<div class="pt">📐 '+esc(spec.name||'(이름 없음)')+'</div>'+
    '<div class="kv">테이블 <code>'+esc(spec.table)+'</code> · 계산 <code>'+esc(calc)+'</code></div>'+
    (spec.filter?'<div class="kv">조건 <code>'+esc(spec.filter)+'</code></div>':'')+
    (spec.dimensions&&spec.dimensions.length?'<div class="kv">차원 <code>'+esc(spec.dimensions.join(', '))+'</code></div>':'')+
    (spec.description?'<div class="kv">'+esc(spec.description)+'</div>':'');
  const row=el('div','row');row.style.marginTop='10px';
  const sv=el('button','btn pri sm','지표로 저장');
  const out=el('div');out.style.marginTop='8px';
  sv.onclick=async()=>{try{await api('/api/builder/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project:state.project,table:spec.table,spec})});sv.textContent='저장됨 ✓';sv.disabled=true;}catch(e){out.innerHTML='<span class="err">'+esc(e.message)+'</span>';}};
  row.append(sv);card.append(row,out);return card;
}
// 선택지 클릭 등으로 특정 텍스트를 바로 전송.
function sendText(t){const inp=$('#cin');if(inp){inp.value=t;send();}}
async function send(){
  const inp=$('#cin');const text=inp.value.trim();if(!text)return;inp.value='';
  addMsg('user',text);
  const th=$('#thread');const t=el('div','msg ai');t.innerHTML='<div class="ava">AI</div><div class="bubble"><span class="typing"><i></i><i></i><i></i></span></div>';th.appendChild(t);th.scrollTop=th.scrollHeight;
  try{
    const r=await api('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project:state.project,session_id:state.session,message:text,provider:selectedProvider()})});
    const wasNew = !state.session;
    state.session=r.session_id||state.session;
    t.remove(); addMsg('ai',r.reply||'(응답 없음)',r);
    if(wasNew) loadSessions();   // 새 세션이면 목록 갱신
  }catch(e){t.remove();addMsg('ai','⚠️ '+e.message);}
}
