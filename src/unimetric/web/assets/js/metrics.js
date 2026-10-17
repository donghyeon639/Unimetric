import { $, el, esc, api } from './dom.js';
import { state, requestRender } from './state.js';
import { vhead, emptyState } from './ui.js';
import { fmt, renderResult } from './results.js';

// ---------- Overview: KPI panels ----------
export async function renderOverview(c){
  c.innerHTML=''; vhead(c,'개요','이 단체의 인증 지표 현황');
  const {metrics}=await api('/api/metrics?project='+encodeURIComponent(state.project));
  if(!metrics.length){c.appendChild(emptyState('아직 지표가 없어요','‘AI 채팅’ 탭에서 대화로 첫 지표를 만들어 보세요.'));return;}
  const grid=el('div','grid kpi'); c.appendChild(grid);
  for(const m of metrics){
    const p=el('div','panel kpicard');
    p.innerHTML='<div class="ph"><span class="gr">'+esc(m.name)+'</span>'+(m.certified?'<span class="cert">✓</span>':'')+'</div><div class="pb"><div class="val">…</div><div class="sub">'+esc(m.type)+(m.description?' · '+esc(m.description):'')+'</div></div>';
    grid.appendChild(p);
    const ph=$('.ph',p);
    const edb=el('button','iconbtn','✏️');edb.title='수정';edb.onclick=()=>editMetric(m.name);
    const dlb=el('button','iconbtn','🗑');dlb.title='삭제';dlb.onclick=()=>deleteMetric(m.name);
    ph.append(edb,dlb);
    api('/api/metrics/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project:state.project,metric:m.name})})
      .then(r=>{const v=r.rows[0]?Object.values(r.rows[0])[0]:'—';$('.val',p).textContent=fmt(v);})
      .catch(()=>{$('.val',p).textContent='—';$('.val',p).style.color='var(--faint)';});
  }
}

// ---------- Metrics: list + run panel ----------

export async function renderMetrics(c){
  c.innerHTML='';
  const nb=el('button','btn pri sm','＋ 새 지표'); nb.onclick=()=>openMetricEditor(null);
  vhead(c,'지표','정의된 지표를 조회·수정·삭제합니다',[nb]);
  const wrap=el('div'); wrap.style.cssText='display:grid;grid-template-columns:360px 1fr;gap:18px;align-items:start';
  const left=el('div','mlist'), right=el('div'); wrap.append(left,right); c.appendChild(wrap);
  const {metrics}=await api('/api/metrics?project='+encodeURIComponent(state.project));
  if(!metrics.length){left.appendChild(emptyState('지표 없음','‘＋ 새 지표’ 또는 AI 채팅으로 만들기'));return;}
  metrics.forEach((m,i)=>{
    const it=el('div','mitem');
    it.innerHTML='<div class="ic">Σ</div><div style="flex:1;min-width:0"><div class="nm">'+esc(m.name)+'</div><div class="ds">'+esc(m.description||m.entity)+'</div></div><span class="pill '+m.type+'">'+m.type+'</span>'+(m.certified?'<span class="cert" style="color:var(--accent)">✓</span>':'');
    const acts=el('div'); acts.style.cssText='display:flex;gap:1px;flex:none';
    const ed=el('button','iconbtn','✏️'); ed.title='수정'; ed.onclick=(e)=>{e.stopPropagation();editMetric(m.name);};
    const dl=el('button','iconbtn','🗑'); dl.title='삭제'; dl.onclick=(e)=>{e.stopPropagation();deleteMetric(m.name);};
    acts.append(ed,dl); it.appendChild(acts);
    it.onclick=()=>{[...left.children].forEach(x=>x.classList.remove('active'));it.classList.add('active');openRun(right,m);};
    left.appendChild(it); if(i===0)it.classList.add('active');
  });
  openRun(right,metrics[0]);
}
// 지표 수정: 상세 명세를 불러와 편집 모달을 연다.
async function editMetric(name){try{const {spec}=await api('/api/metrics/detail?project='+encodeURIComponent(state.project)+'&metric='+encodeURIComponent(name));openMetricEditor(spec);}catch(e){alert(e.message);}}
// 지표 삭제.
async function deleteMetric(name){if(!confirm('지표 “'+name+'”를 삭제할까요?'))return;try{await api('/api/metrics?project='+encodeURIComponent(state.project)+'&metric='+encodeURIComponent(name),{method:'DELETE'});requestRender();}catch(e){alert(e.message);}}
function openRun(right,m){
  right.innerHTML='';
  const p=el('div','panel');
  p.innerHTML='<div class="ph"><span class="gr">'+esc(m.name)+'</span><span class="pill '+m.type+'">'+m.type+'</span></div>';
  const pb=el('div','pb');
  pb.innerHTML='<div class="row" style="margin-bottom:12px"><input class="in" id="gb" placeholder="그룹 기준(차원) — 예: feedback_status" style="flex:1"><button class="btn pri" id="runBtn">실행 ▸</button></div><div id="runOut"></div>';
  p.appendChild(pb); right.appendChild(p);
  $('#runBtn',pb).onclick=async()=>{
    const gb=$('#gb',pb).value.split(',').map(s=>s.trim()).filter(Boolean);
    $('#runOut',pb).innerHTML='<span class="muted">실행 중…</span>';
    try{const r=await api('/api/metrics/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project:state.project,metric:m.name,group_by:gb})});
      renderResult($('#runOut',pb),r);}catch(e){$('#runOut',pb).innerHTML='<span class="err">'+esc(e.message)+'</span>';}
  };
  $('#runBtn',pb).click();
}

// ---------- Definition (YAML) ----------
const ADD_PLACEHOLDER=`# 지표 1개만 적고 "추가" → 기존 카탈로그에 누적됩니다.
entities:
  - name: orders
    table: orders
    measures:
      - { name: cnt, agg: count, expr: id }
    metrics:
      - { name: order_count, type: simple, measure: cnt }
# 또는 조인 등 자유 SQL 지표:
# sql_metrics:
#   - name: my_metric
#     sql: |
#       SELECT COUNT(*) AS value FROM "OwnPart" WHERE status='SELL'`;

export async function renderDefine(c){
  c.innerHTML='';vhead(c,'정의(YAML)','지표를 1개씩 정의해 누적 저장하거나(추가), 전체 카탈로그를 직접 편집합니다.');
  const {project}=await api('/api/projects/'+encodeURIComponent(state.project));

  // ① 지표 추가(누적) — 기존 지표 유지
  const add=el('div','panel');
  add.innerHTML='<div class="ph"><span class="gr">＋ 지표 추가 (1개씩 · 누적)</span><span class="faint" id="ast"></span></div>';
  const apb=el('div','pb');
  const ata=el('textarea','in mono');ata.style.cssText='min-height:200px;resize:vertical';ata.placeholder=ADD_PLACEHOLDER;
  const arow=el('div','row');arow.style.marginTop='10px';
  const ab=el('button','btn pri','추가 (기존 유지)');arow.appendChild(ab);
  apb.append(ata,arow);add.appendChild(apb);c.appendChild(add);
  ab.onclick=async()=>{
    if(!ata.value.trim()){$('#ast').textContent='지표 YAML을 입력하세요.';return;}
    try{
      await api('/api/projects/'+encodeURIComponent(state.project)+'/metrics-yaml/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({metrics_yaml:ata.value})});
      ata.value='';$('#ast').textContent='추가됨 ✓';
      await renderDefine(c);  // 전체 YAML·목록 갱신
    }catch(e){$('#ast').innerHTML='<span class="err">'+esc(e.message)+'</span>';}
  };

  // ② 전체 카탈로그 편집(주의: 저장 시 전체 교체)
  const p=el('div','panel');p.style.marginTop='16px';
  p.innerHTML='<div class="ph"><span class="gr">전체 카탈로그 (metrics.yaml · 저장 시 전체 교체)</span><span class="faint" id="yst"></span></div>';
  const pb=el('div','pb');const ta=el('textarea','in mono');ta.style.cssText='min-height:320px;resize:vertical';ta.value=project.metrics_yaml||'';
  const row=el('div','row');row.style.marginTop='12px';const sv=el('button','btn','전체 저장(교체)');row.appendChild(sv);
  pb.append(ta,row);p.appendChild(pb);c.appendChild(p);
  sv.onclick=async()=>{try{await api('/api/projects/'+encodeURIComponent(state.project)+'/metrics-yaml',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({metrics_yaml:ta.value})});$('#yst').textContent='저장됨 ✓';}catch(e){$('#yst').innerHTML='<span class="err">'+esc(e.message)+'</span>';}};
}

// ---------- 지표 편집/생성 모달 ----------
const ED_AGGS=[['count','개수'],['count_distinct','고유개수'],['sum','합계'],['avg','평균'],['min','최소'],['max','최대']];
function edFillAgg(s){s.innerHTML=ED_AGGS.map(([v,t])=>`<option value="${v}">${t}(${v})</option>`).join('');}
function edType(){const r=$('#ed_type').value==='ratio';$('#ed_ratio').classList.toggle('hide',!r);$('#ed_simple').classList.toggle('hide',r);}
$('#ed_type').onchange=edType;
// 선택 테이블 '하나'의 컬럼만 가볍게 로드(전체 스키마 X → 빠름). prefill spec은 컬럼 로드 후 적용.
async function edLoadCols(prefill){
  const t=$('#ed_table').value;if(!t)return;
  ['#ed_m_expr','#ed_n_expr','#ed_d_expr'].forEach(id=>$(id).innerHTML='<option>불러오는 중…</option>');
  $('#ed_dims').innerHTML='<span class="faint">불러오는 중…</span>';
  const {columns}=await api('/api/columns?project='+encodeURIComponent(state.project)+'&table='+encodeURIComponent(t));
  const cols=columns.map(c=>c.column);
  const opt='<option value="*">행 전체 (*)</option>'+cols.map(c=>`<option>${esc(c)}</option>`).join('');
  ['#ed_m_expr','#ed_n_expr','#ed_d_expr'].forEach(id=>$(id).innerHTML=opt);
  $('#ed_dims').innerHTML=cols.length?cols.map(c=>`<label style="margin-right:12px;font-size:13px"><input type="checkbox" class="eddim" value="${esc(c)}"> ${esc(c)}</label>`).join(''):'<span class="faint">컬럼 없음</span>';
  if(prefill){
    if(prefill.type==='ratio'&&prefill.numerator){$('#ed_n_agg').value=prefill.numerator.agg;$('#ed_n_expr').value=prefill.numerator.expr;$('#ed_d_agg').value=prefill.denominator.agg;$('#ed_d_expr').value=prefill.denominator.expr;}
    else if(prefill.measure){$('#ed_m_agg').value=prefill.measure.agg;$('#ed_m_expr').value=prefill.measure.expr;}
    const dims=new Set(prefill.dimensions||[]);document.querySelectorAll('.eddim').forEach(c=>c.checked=dims.has(c.value));
  }
}
$('#ed_table').onchange=()=>edLoadCols();
async function openMetricEditor(spec){
  // 동기 항목 먼저 채우고 모달을 '즉시' 띄운다(네트워크 기다리지 않음).
  $('#edTitle').textContent=spec?'지표 수정':'새 지표';$('#ed_err').textContent='';$('#ed_out').innerHTML='';
  ['#ed_m_agg','#ed_n_agg','#ed_d_agg'].forEach(id=>edFillAgg($(id)));
  $('#ed_name').value=spec?.name||'';$('#ed_desc').value=spec?.description||'';
  $('#ed_type').value=spec?.type||'simple';$('#ed_filter').value=spec?.filter||'';$('#ed_cert').checked=!!spec?.certified;
  edType();
  $('#ed_table').innerHTML='<option>'+esc(spec?.table||'불러오는 중…')+'</option>';
  $('#ed_dims').innerHTML='<span class="faint">불러오는 중…</span>';
  $('#edOverlay').classList.add('show');   // ← 즉시 표시(빠르게 뜸)
  // 무거운 로드는 이후 비동기로.
  try{
    const {tables}=await api('/api/tables?project='+encodeURIComponent(state.project));
    $('#ed_table').innerHTML=tables.map(t=>`<option>${esc(t)}</option>`).join('');
    if(spec?.table)$('#ed_table').value=spec.table;
    await edLoadCols(spec||null);   // 선택 테이블 컬럼만 로드 + spec prefill
  }catch(e){$('#ed_err').textContent=e.message;}
}
function edSpec(){const s={name:$('#ed_name').value.trim(),description:$('#ed_desc').value.trim(),type:$('#ed_type').value,certified:$('#ed_cert').checked,filter:$('#ed_filter').value.trim(),dimensions:[...document.querySelectorAll('.eddim:checked')].map(c=>c.value)};
  if(s.type==='ratio'){s.numerator={agg:$('#ed_n_agg').value,expr:$('#ed_n_expr').value};s.denominator={agg:$('#ed_d_agg').value,expr:$('#ed_d_expr').value};}
  else{s.measure={agg:$('#ed_m_agg').value,expr:$('#ed_m_expr').value};}return s;}
function edBody(){const sp=edSpec();return{project:state.project,table:$('#ed_table').value,spec:sp,group_by:sp.dimensions};}
$('#ed_cancel').onclick=()=>$('#edOverlay').classList.remove('show');
$('#ed_preview').onclick=async()=>{if(!$('#ed_name').value.trim()){$('#ed_err').textContent='이름을 입력하세요.';return;}
  $('#ed_out').innerHTML='<span class="muted">미리보기…</span>';
  try{const r=await api('/api/builder/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(edBody())});$('#ed_out').innerHTML='';renderResult($('#ed_out'),r);}
  catch(e){$('#ed_out').innerHTML='<span class="err">'+esc(e.message)+'</span>';}};
$('#ed_save').onclick=async()=>{if(!$('#ed_name').value.trim()){$('#ed_err').textContent='이름을 입력하세요.';return;}
  try{await api('/api/builder/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(edBody())});$('#edOverlay').classList.remove('show');requestRender();}
  catch(e){$('#ed_err').textContent=e.message;}};
