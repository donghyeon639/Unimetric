export const $=(s,r=document)=>r.querySelector(s);
export const el=(t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!=null)e.innerHTML=h;return e;};
export const esc=(s)=>String(s==null?'':s).replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));
export async function api(path,opts){const r=await fetch(path,opts);const b=await r.json().catch(()=>({}));if(!r.ok)throw new Error(b.detail||r.statusText);return b;}
