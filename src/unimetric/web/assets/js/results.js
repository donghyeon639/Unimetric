import { el, esc } from './dom.js';

// ---------- shared: result table + chart ----------
export function fmt(v){if(v==null)return'';if(typeof v==='number')return Number.isInteger(v)?v.toLocaleString():v.toFixed(2);return String(v);}

// Query results can serialize PostgreSQL NUMERIC as strings. Keep padded IDs as labels.
function chartNumber(value){
  if(typeof value==='number') return Number.isFinite(value) ? value : null;
  if(typeof value==='string' && /^[+-]?(?:0|[1-9]\d*)(?:\.\d+)?(?:e[+-]?\d+)?$/i.test(value)){
    const number=Number(value);
    if(Number.isFinite(number)) return number;
  }
  return null;
}

function numericColumns(rows,columns){
  return columns.filter(column=>
    rows.some(row=>chartNumber(row[column])!==null) &&
    rows.every(row=>row[column]==null || chartNumber(row[column])!==null)
  );
}

export function buildTable(rows){
  if(!rows||!rows.length){return el('div','muted','결과 행이 없습니다.');}
  const cols=Object.keys(rows[0]);
  // 모든 비-null 값이 숫자인 컬럼 = 숫자 컬럼(우측정렬 + 셀 내 비율 바)
  const numCols=cols.filter(c=>rows.every(r=>r[c]==null||typeof r[c]==='number'));
  const max={};numCols.forEach(c=>{max[c]=Math.max(1,...rows.map(r=>Math.abs(Number(r[c])||0)));});
  const t=el('table','dt');
  const head='<thead><tr>'+cols.map(c=>'<th class="'+(numCols.includes(c)?'num':'')+'">'+esc(c)+'</th>').join('')+'</tr></thead>';
  const body='<tbody>'+rows.map(r=>'<tr>'+cols.map(c=>{
    if(numCols.includes(c)){
      const v=Number(r[c])||0, w=Math.round(Math.abs(v)/max[c]*100);
      return '<td class="num"><span class="cellbar" style="width:'+w+'%"></span><span class="cellv">'+esc(fmt(r[c]))+'</span></td>';
    }
    return '<td>'+esc(fmt(r[c]))+'</td>';
  }).join('')+'</tr>').join('')+'</tbody>';
  t.innerHTML=head+body;return t;
}
const PIE_COLORS=['#f5a524','#54a0ff','#3ddc97','#ff6b6b','#f472b6','#a78bfa','#fbbf24','#22d3ee','#fb923c','#34d399','#60a5fa','#a3e635'];
// 막대 끝에 값 라벨을 그리는 인라인 플러그인(가로 막대 기준).
const barValueLabel={id:'barValueLabel',afterDatasetsDraw(chart){
  const ds=chart.data.datasets[0], meta=chart.getDatasetMeta(0), ctx=chart.ctx;
  ctx.save();ctx.font='600 11px ui-monospace, monospace';ctx.fillStyle='#cfd5df';
  ctx.textAlign='left';ctx.textBaseline='middle';
  meta.data.forEach((b,i)=>{
    if(ds.data[i]!==null) ctx.fillText(fmt(ds.data[i]), b.x+8, b.y);
  });
  ctx.restore();
}};
// SQL 결과 순서를 유지한다.
function drawBar(node,labels,data,title){
  const colors=labels.map((_,i)=>PIE_COLORS[i%PIE_COLORS.length]);
  const cv=el('canvas');
  const h=Math.min(560, Math.max(180, labels.length*40+30));
  cv.style.maxHeight=h+'px';node.appendChild(cv);
  return new Chart(cv,{type:'bar',
    data:{labels,datasets:[{label:title,data,backgroundColor:colors,borderRadius:6,maxBarThickness:30}]},
    options:{indexAxis:'y',layout:{padding:{right:56}},
      plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>' '+fmt(c.parsed.x)}}},
      scales:{x:{ticks:{color:'#8a94a6'},grid:{color:'#1a1e26'},border:{display:false}},
              y:{ticks:{color:'#cfd5df',font:{size:12},autoSkip:false},grid:{display:false},border:{display:false}}}},
    plugins:[barValueLabel]});
}
// 도넛 범례도 SQL 결과 순서를 유지한다.
function drawDonut(node,labels,data,title){
  const total=data.reduce((a,b)=>a+(b||0),0);
  const colors=labels.map((_,i)=>PIE_COLORS[i%PIE_COLORS.length]);
  const wrap=el('div','donutwrap');
  const cwrap=el('div','donutcanvas');
  const cv=el('canvas');cwrap.appendChild(cv);
  const center=el('div','donutcenter');
  center.innerHTML='<span class="dc-l">합계</span><b>'+fmt(total)+'</b>';
  cwrap.appendChild(center);
  const leg=el('div','donutlegend');
  let inst=null;
  labels.map((l,i)=>({l,v:data[i],c:colors[i],i})).forEach(it=>{
    const pct=total?(it.v/total*100):0;
    const row=el('div','legrow');
    row.innerHTML='<span class="dot" style="background:'+it.c+'"></span>'+
      '<span class="llabel">'+esc(it.l)+'</span>'+
      '<span class="lval">'+fmt(it.v)+'</span>'+
      '<span class="lpct" style="color:'+it.c+'">'+pct.toFixed(2)+'%</span>';
    row.onmouseenter=()=>{if(inst){inst.setActiveElements([{datasetIndex:0,index:it.i}]);inst.update();}};
    row.onmouseleave=()=>{if(inst){inst.setActiveElements([]);inst.update();}};
    leg.appendChild(row);
  });
  wrap.append(cwrap,leg);node.appendChild(wrap);
  inst=new Chart(cv,{type:'doughnut',
    data:{labels,datasets:[{label:title,data,backgroundColor:colors,borderColor:'#14171d',borderWidth:2,hoverOffset:7}]},
    options:{cutout:'64%',plugins:{legend:{display:false},
      tooltip:{callbacks:{label:(ctx)=>' '+ctx.label+': '+fmt(ctx.parsed)+' ('+(total?(ctx.parsed/total*100).toFixed(2):0)+'%)'}}}}});
  return inst;
}
// 결과를 표/막대/원형으로 토글해 보여준다(지표 조회·채팅·미리보기 공용).
export function renderResult(node,r){
  node.innerHTML='';
  node.appendChild(el('pre','sql',esc(r.sql||'')));
  const rows=r.rows||[];
  if(!rows.length){node.appendChild(el('div','muted','결과 행이 없습니다.'));return;}
  const cols=Object.keys(rows[0]);
  const numberCols=numericColumns(rows,cols);
  const chart=r.chart;
  const hasChart=chart && cols.includes(chart.label_column) &&
    numberCols.includes(chart.value_column) && chart.label_column!==chart.value_column;
  const labelCol=hasChart ? chart.label_column : cols.find(c=>!numberCols.includes(c));
  const measures=numberCols.filter(c=>c!==labelCol);
  const canChart=Boolean(globalThis.Chart && labelCol && measures.length && rows.length<=50);
  let selected='';
  if(hasChart){
    selected=chart.value_column;
  }else if(!Object.hasOwn(r,'chart') && measures.length===1){
    selected=measures[0];
  }
  let view=canChart && selected ? 'bar' : 'table';

  const bar=el('div','vizbar');
  const content=el('div');content.style.marginTop='12px';
  const buttons={};
  let inst=null;  // 이 결과 블록 전용 차트 인스턴스

  function chartTitle(){
    const primary=hasChart && selected===chart.value_column;
    const title=(primary && chart.title) || selected;
    return primary && chart.unit ? title+' ('+chart.unit+')' : title;
  }

  function draw(kind){
    const data=selected ? rows.map(row=>chartNumber(row[selected])) : [];
    const canPie=data.length>0 && data.every(value=>value!==null && value>=0) &&
      data.some(value=>value>0);
    view=!selected || (kind==='pie' && !canPie) ? (selected ? 'bar' : 'table') : kind;
    if(inst){inst.destroy();inst=null;}
    content.innerHTML='';
    for(const [key,button] of Object.entries(buttons)){
      button.classList.toggle('active',key===view);
      button.setAttribute('aria-pressed',String(key===view));
      button.disabled=key!=='table' && (!selected || (key==='pie' && !canPie));
    }
    if(view==='table'){
      const tw=el('div','tablewrap');tw.appendChild(buildTable(rows));content.appendChild(tw);
      content.appendChild(el('div','tablecap','총 '+rows.length+'행'));return;
    }
    const labels=rows.map(row=>fmt(row[labelCol]));
    const title=chartTitle();
    const heading=el('div','viztitle');
    heading.textContent=title;
    content.appendChild(heading);
    inst=view==='bar' ? drawBar(content,labels,data,title) : drawDonut(content,labels,data,title);
  }
  const views=[['table','표']];
  if(canChart){views.push(['bar','막대'],['pie','원형']);}
  views.forEach(([key,label])=>{
    const button=el('button','vizbtn',label);
    button.type='button';
    button.onclick=()=>draw(key);
    buttons[key]=button;
    bar.appendChild(button);
  });
  const controls=el('div','vizcontrols');
  controls.appendChild(bar);
  if(canChart){
    const field=el('label','vizmeasure');
    field.append('표시 지표');
    const select=el('select','in');
    if(!selected){
      const placeholder=new Option('지표 선택','');
      placeholder.disabled=true;
      select.appendChild(placeholder);
    }
    for(const column of measures){
      const primary=hasChart && column===chart.value_column;
      select.appendChild(new Option((primary && chart.title) || column,column));
    }
    select.value=selected;
    select.onchange=()=>{
      selected=select.value;
      draw(view==='table' ? 'bar' : view);
    };
    field.appendChild(select);
    controls.appendChild(field);
  }
  node.append(controls,content);
  draw(view);
}
