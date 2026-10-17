import { $, el, esc, api } from './dom.js';
import { state, requestProjectsRefresh } from './state.js';

// =================== New Org modal ===================
let POLL=null,pollT=null;
export function openModal(){['np_key','np_name','db_host','db_port','db_user','db_password','db_database'].forEach(i=>$('#'+i).value='');
  document.querySelector('input[name=src][value=live]').checked=true;onSrc();
  $('#npErr').textContent='';$('#testMsg').textContent='';$('#tlist').innerHTML='';$('#copyExtra').classList.add('hide');
  $('#progBox').classList.add('hide');$('#progTables').innerHTML='';$('#createNp').style.display='';$('#retryNp').style.display='none';
  $('#cancelNp').textContent='취소';$('#overlay').classList.add('show');}
$('#cancelNp').onclick=()=>{if(pollT){clearTimeout(pollT);pollT=null;}POLL=null;$('#overlay').classList.remove('show');};
function srcVal(){return document.querySelector('input[name=src]:checked').value;}
function onSrc(){const v=srcVal();$('#dbFields').classList.toggle('hide',v==='file');$('#copyExtra').classList.toggle('hide',v!=='copy');
  $('#np_hint').textContent={live:'복사 없이 조회 때마다 소스에 직접 쿼리(항상 최신). 읽기 전용 계정 권장.',copy:'소스를 우리 DB로 전체 복사(스냅샷). 청크·병렬·체크포인트로 안전 적재.',file:'프로젝트만 만들고 이후 파일을 올립니다.'}[v];}
$('#np_seg').addEventListener('change',onSrc);
$('#db_kind').addEventListener('change',()=>$('#db_host4').classList.toggle('hide',$('#db_kind').value==='sqlite'));
function dbCfg(){return{kind:$('#db_kind').value,host:$('#db_host').value||null,port:$('#db_port').value?+$('#db_port').value:null,user:$('#db_user').value||null,password:$('#db_password').value||null,database:$('#db_database').value||null};}
$('#testConn').onclick=async()=>{$('#testMsg').textContent='연결 중…';try{const{tables}=await api('/api/sources/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(dbCfg())});
  $('#testMsg').innerHTML='<span style="color:var(--green)">연결 OK · 테이블 '+tables.length+'개</span>';
  if(srcVal()==='copy'){$('#tlist').innerHTML=tables.map(t=>'<label><input type="checkbox" class="tb" value="'+esc(t)+'" checked> '+esc(t)+'</label>').join('');tcount();}
}catch(e){$('#testMsg').innerHTML='<span class="err">'+esc(e.message)+'</span>';}};
$('#selAll').onclick=()=>{document.querySelectorAll('.tb').forEach(c=>c.checked=true);tcount();};
$('#selNone').onclick=()=>{document.querySelectorAll('.tb').forEach(c=>c.checked=false);tcount();};
function tcount(){const t=document.querySelectorAll('.tb').length,k=document.querySelectorAll('.tb:checked').length;$('#tcount').textContent=k+'/'+t+' 선택';}
document.addEventListener('change',e=>{if(e.target.classList.contains('tb'))tcount();});

$('#createNp').onclick=async()=>{
  const key=$('#np_key').value.trim(),name=$('#np_name').value.trim()||key;
  if(!key){$('#npErr').textContent='단체 키를 입력하세요.';return;}
  $('#npErr').textContent='';const v=srcVal();
  try{
    if(v==='file'){await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key,name})});done(key);}
    else if(v==='live'){const r=await api('/api/projects/live',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key,name,config:dbCfg()})});done(r.project.key);}
    else{const tbs=[...document.querySelectorAll('.tb:checked')].map(c=>c.value);if(!tbs.length){$('#npErr').textContent='가져올 테이블을 선택하세요(연결 테스트 먼저).';return;}
      const r=await api('/api/sources/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key,name,config:dbCfg(),tables:tbs})});
      state.project=r.project;startPoll(r.job_id);}
  }catch(e){$('#npErr').textContent=e.message;}
};
async function done(key){state.project=key;$('#overlay').classList.remove('show');state.view='overview';requestProjectsRefresh();}
function startPoll(job){POLL=job;$('#createNp').style.display='none';$('#cancelNp').textContent='닫기';$('#progBox').classList.remove('hide');poll();}
async function poll(){if(!POLL)return;try{const s=await api('/api/import/jobs/'+POLL);
  $('#progStatus').textContent='상태: '+s.job.status+' · '+(s.job.message||'');
  $('#progTables').innerHTML=s.tables.map(t=>{const mk=t.status==='done'?'<span class="ok">✓</span>':t.status==='failed'?'<span class="bad">✗</span>':t.status==='running'?'<span class="spin"></span>':'·';
    return '<div>'+mk+' '+esc(t.table_name)+' <span class="faint">'+(t.copied_rows||0)+(t.source_rows!=null?'/'+t.source_rows:'')+'</span>'+(t.error?' <span class="bad">'+esc(t.error)+'</span>':'')+'</div>';}).join('');
  if(s.job.status==='running'){pollT=setTimeout(poll,1200);}else{const f=s.tables.filter(t=>t.status==='failed').length;$('#retryNp').style.display=f?'':'none';requestProjectsRefresh();}
}catch(e){$('#progStatus').innerHTML='<span class="err">'+esc(e.message)+'</span>';}}
$('#retryNp').onclick=async()=>{await api('/api/import/jobs/'+POLL+'/retry',{method:'POST'});$('#retryNp').style.display='none';poll();};
