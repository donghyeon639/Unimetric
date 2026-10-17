import { $, api } from './dom.js';

export function selectedProvider(){return $('#llmProvider').value||null;}
export async function loadProviders(){
  const select=$('#llmProvider'),status=$('#llmStatus');
  try{
    const data=await api('/api/llm-status');
    select.replaceChildren(new Option('서버 기본: '+data.provider,''));
    for(const p of data.providers){
      const option=new Option(p.label+(p.available?'':' · 설치/설정 필요'),p.id);
      option.disabled=!p.available;select.appendChild(option);
    }
    let saved='';try{saved=localStorage.getItem('unimetric.llmProvider')||'';}catch(e){}
    if([...select.options].some(o=>o.value===saved&&!o.disabled))select.value=saved;
    const update=()=>{
      const provider=selectedProvider()||data.provider;
      status.textContent=provider==='none'?'AI 미선택':'새 질문에 적용';
      try{localStorage.setItem('unimetric.llmProvider',select.value);}catch(e){}
    };
    select.onchange=update;update();
  }catch(e){status.textContent='AI 설정 확인 실패';}
}
