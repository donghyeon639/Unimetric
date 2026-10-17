import { el, esc } from './dom.js';
import { requestNewProject } from './state.js';

export function emptyState(big,sub,withBtn){
  const e=el('div','empty','<div class="big">'+esc(big)+'</div><div>'+esc(sub)+'</div>');
  if(withBtn){const b=el('button','btn pri','＋ 새 단체');b.style.marginTop='16px';b.onclick=requestNewProject;e.appendChild(b);}
  return e;
}
export function vhead(c,title,desc,actions){
  const h=el('div','viewhead'); h.innerHTML='<div><h1>'+esc(title)+'</h1>'+(desc?'<p>'+esc(desc)+'</p>':'')+'</div>';
  if(actions){const sp=el('div');sp.style.flex='1';h.appendChild(sp);actions.forEach(a=>h.appendChild(a));}
  c.appendChild(h);
}
