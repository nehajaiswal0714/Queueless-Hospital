const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
async function api(url,options={}){const o={...options,headers:{...(options.headers||{}),'Content-Type':'application/json'}};if(o.body&&typeof o.body!=='string')o.body=JSON.stringify(o.body);const res=await fetch(url,o);const data=await res.json().catch(()=>({success:false,message:'Server returned an invalid response.'}));if(!res.ok||data.success===false)throw new Error(data.message||'Something went wrong.');return data}
function esc(v){return String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}
function money(v){return '₹'+Number(v||0).toLocaleString('en-IN',{minimumFractionDigits:0,maximumFractionDigits:2})}
function time(v){if(!v)return '';return String(v).slice(0,5)}
function empty(text){return `<div class="empty-state">${esc(text)}</div>`}
function toast(message,type='success'){const el=$('#toast');if(!el)return;el.textContent=message;el.className='toast show';setTimeout(()=>el.className='toast',2600)}
function showError(e){toast(e.message||String(e),'error')}
function statCards(selector,items){const el=$(selector);if(!el)return;el.innerHTML=items.map(x=>`<div class="stat-card"><small>${esc(x.label)}</small><strong>${esc(x.value)}</strong>${x.note?`<div class="stat-note">${esc(x.note)}</div>`:''}</div>`).join('')}
function priorityBadge(p){const cls=p==='emergency'?'danger':p==='urgent'?'warning':'success';return `<span class="badge ${cls}">${esc(p)}</span>`}
function statusBadge(s){const low=String(s||'').toLowerCase();const cls=low.includes('paid')||low==='available'||low==='completed'? 'success':low.includes('emergency')||low.includes('off')||low.includes('cancel')?'danger':'warning';return `<span class="badge ${cls}">${esc(s||'—')}</span>`}
