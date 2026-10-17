import { el, esc } from './dom.js';

// ---------- shared: result table + chart ----------
export function fmt(v){if(v==null)return'';if(typeof v==='number')return Number.isInteger(v)?v.toLocaleString():v.toFixed(2);return String(v);}
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
  meta.data.forEach((b,i)=>{ctx.fillText(fmt(ds.data[i]), b.x+8, b.y);});
  ctx.restore();
}};
// 가로 막대: 값 내림차순 + 카테고리 색 + 막대 끝 값 라벨.
function drawBar(node,labels,data){
  const order=labels.map((_,i)=>i).sort((a,b)=>data[b]-data[a]);
  const L=order.map(i=>labels[i]), D=order.map(i=>data[i]);
  const C=order.map((_,j)=>PIE_COLORS[j%PIE_COLORS.length]);
  const cv=el('canvas');
  const h=Math.min(560, Math.max(180, L.length*40+30));
  cv.style.maxHeight=h+'px';node.appendChild(cv);
  return new Chart(cv,{type:'bar',
    data:{labels:L,datasets:[{data:D,backgroundColor:C,borderRadius:6,maxBarThickness:30}]},
    options:{indexAxis:'y',layout:{padding:{right:56}},
      plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>' '+fmt(c.parsed.x)}}},
      scales:{x:{ticks:{color:'#8a94a6'},grid:{color:'#1a1e26'},border:{display:false}},
              y:{ticks:{color:'#cfd5df',font:{size:12}},grid:{display:false},border:{display:false}}}},
    plugins:[barValueLabel]});
}
// 도넛 차트 + 우측 범례(색·라벨·값·퍼센트, 값 내림차순). 범례 hover 시 해당 조각 강조.
function drawDonut(node,labels,data){
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
  // 값 내림차순으로 범례 정렬(원래 인덱스 i는 조각 강조용 보존)
  labels.map((l,i)=>({l,v:data[i],c:colors[i],i})).sort((a,b)=>b.v-a.v).forEach(it=>{
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
    data:{labels,datasets:[{data,backgroundColor:colors,borderColor:'#14171d',borderWidth:2,hoverOffset:7}]},
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
  const numCol=cols.find(c=>typeof rows[0][c]==='number');
  const labelCol=cols.find(c=>c!==numCol)||cols[0];
  const canChart=!!numCol && cols.length>=2 && rows.length<=50;

  const bar=el('div','vizbar');
  const content=el('div');content.style.marginTop='12px';
  let inst=null;  // 이 결과 블록 전용 차트 인스턴스
  function draw(kind){
    if(inst){inst.destroy();inst=null;}
    content.innerHTML='';
    [...bar.children].forEach(b=>b.classList.toggle('active',b.dataset.k===kind));
    if(kind==='table'){
      const tw=el('div','tablewrap');tw.appendChild(buildTable(rows));content.appendChild(tw);
      content.appendChild(el('div','tablecap','총 '+rows.length+'행'));return;
    }
    const labels=rows.map(x=>fmt(x[labelCol])), data=rows.map(x=>Number(x[numCol])||0);
    if(kind==='bar'){
      inst=drawBar(content,labels,data);
    }else{
      inst=drawDonut(content,labels,data);
    }
  }
  const views=[['table','표']];
  if(canChart){views.push(['bar','막대'],['pie','원형']);}
  views.forEach(([k,label])=>{const b=el('button','vizbtn',label);b.dataset.k=k;b.onclick=()=>draw(k);bar.appendChild(b);});
  node.append(bar,content);
  draw(canChart?'bar':'table');   // 기본: 차트 가능하면 막대, 아니면 표
}
