// ---------- token: arrives once in the link, then lives in storage ----------
const p=new URLSearchParams(location.search);
// home= and orb= are remembered too: the address is cleaned below, so Ctrl+R
// came back on the voice stage with the old orb (6 Oct 2026).
['home','orb'].forEach(k=>{try{if(p.get(k))localStorage.setItem('athena_'+k,p.get(k));
  else if(localStorage.getItem('athena_'+k))p.set(k,localStorage.getItem('athena_'+k))}catch(e){}});
if(p.get('t')){try{localStorage.setItem('jarvis_token',p.get('t'))}catch(e){}
  history.replaceState({},'',location.pathname+location.hash)}
const token=()=>{try{return localStorage.getItem('jarvis_token')||''}catch(e){return ''}};
const $=id=>document.getElementById(id);
async function api(path,opts){
  const r=await fetch(path,Object.assign({headers:{'X-Token':token(),
    'Content-Type':'application/json'}},opts||{}));
  const j=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(j.error||('HTTP '+r.status));
  return j;
}
if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(()=>{})}

// ---------- conversation ----------
const say=$('say');
function line(text,cls){const d=document.createElement('div');d.className=cls;
  d.textContent=text;say.appendChild(d);say.scrollTop=say.scrollHeight;
  document.body.classList.add('chatting');return d}
// Enough Markdown to read comfortably, and equations handed to MathJax -
// without it a study answer arrived as "\[ s = -\frac{..." on screen.
function esc(t){return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function md(t){
  // Code fences first, so nothing below (bold, bullets, $maths$) touches code.
  const code=[];
  t=String(t).replace(/```([\w+#.-]*)[ \t]*\n?([\s\S]*?)```/g,(_,lang,body)=>{code.push([lang,body.replace(/\n$/,'')]);return '\u0000'+(code.length-1)+'\u0000'});
  t=esc(t);
  t=t.replace(/(^|[^$])\$(?!\$)\s*([^$\n]+?)\s*\$(?!\$)/g,'$1\\($2\\)');
  // Markdown tables (study answers use them): header row, a |---| line, rows.
  t=t.replace(/(?:^\|.*\|[ \t]*(?:\n|$)){2,}/gm,blk=>{const rows=blk.trim().split('\n');
    if(rows.length<2||!/^\|[\s:|-]+\|$/.test(rows[1].trim()))return blk;
    const cells=r=>r.trim().replace(/^\||\|$/g,'').split('|').map(c=>c.trim());
    return '<div class="tbl"><table><thead><tr>'+cells(rows[0]).map(c=>'<th>'+c+'</th>').join('')+'</tr></thead><tbody>'
      +rows.slice(2).map(r=>'<tr>'+cells(r).map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>'});
  t=t.replace(/^#{1,4}\s+(.+)$/gm,'<span class="h">$1</span>');
  t=t.replace(/\*\*([^*\n]+)\*\*/g,'<b>$1</b>');
  t=t.replace(/`([^`\n]+)`/g,'<code>$1</code>');
  t=t.replace(/^\s*[-*]\s+/gm,'• ');
  t=t.replace(/\u0000(\d+)\u0000/g,(_,i)=>{const c=code[+i];
    return '<pre data-lang="'+esc(c[0]||'')+'"><code'+(c[0]?' class="language-'+esc(c[0])+'"':'')+'>'+esc(c[1])+'</code></pre>'});
  return t}
// Code blocks get highlighting and a Copy button; equations go to MathJax.
function decorate(b){
  b.querySelectorAll('pre').forEach(p=>{const c=p.querySelector('code');
    if(window.hljs&&c){try{hljs.highlightElement(c)}catch(e){}}
    if(p.dataset.lang){const l=document.createElement('span');l.className='lang';l.textContent=p.dataset.lang;p.appendChild(l)}
    const k=document.createElement('button');k.type='button';k.className='cp';k.textContent='Copy';
    k.onclick=()=>copyText(c?c.innerText:p.innerText,k);p.appendChild(k)});
  if(window.MathJax&&MathJax.typesetPromise)MathJax.typesetPromise([b]).catch(()=>{})}
function copyText(t,btn){const ok=()=>{const o=btn.textContent;btn.textContent='Copied ✓';setTimeout(()=>{btn.textContent=o},1400)};
  if(navigator.clipboard&&window.isSecureContext)navigator.clipboard.writeText(t).then(ok,()=>copyOld(t,ok));else copyOld(t,ok)}
function copyOld(t,ok){const a=document.createElement('textarea');a.value=t;a.setAttribute('readonly','');
  a.style.cssText='position:fixed;left:-9999px;opacity:0';document.body.appendChild(a);a.select();
  try{document.execCommand('copy');ok()}catch(e){}a.remove()}
function actBtn(label,fn,tip){const b=document.createElement('button');b.type='button';b.textContent=label;
  if(tip){b.title=tip;b.setAttribute('aria-label',tip)}b.onclick=e=>{e.stopPropagation();fn(b)};return b}
// Your message, with Edit and Copy.
function userLine(text){const d=line(text,'me');const a=document.createElement('div');a.className='msgacts';
  a.append(actBtn('✎ Edit',()=>startEdit(text,d),'Edit this message'),actBtn('Copy',b=>copyText(text,b),'Copy this message'));
  d.appendChild(a);return d}
function answer(text,err,o){o=o||{};
  if(!err&&text)$('txAth').textContent=String(text).replace(/\[share:[\w-]+\]/g,'').replace(/[*_`#>]/g,'').trim().slice(0,400);
  // [share:<id>] - a file the PC is sending: shown as a Download button.
  const shared=[];text=(text||'').replace(/\s*\[share:([\w-]+)\]/g,(_,id)=>{shared.push(id);return ''});
  const m=(text||'').match(/\[([\w.\-]+\.html)\]\s*$/m);
  const body=(m?text.replace(m[0],'').trim():(text||''));
  const d=document.createElement('div');d.className='jv'+(err?' err':'');
  const b=document.createElement('div');b.className='body';d.appendChild(b);
  const finish=()=>{b.innerHTML=md(body);decorate(b);
    if(body.length>700&&!d.querySelector('.more')){d.classList.add('long');const x=document.createElement('button');
      x.className='more';x.textContent='SHOW ALL';
      x.onclick=()=>{d.classList.toggle('long');x.textContent=d.classList.contains('long')?'SHOW ALL':'SHOW LESS'};
      b.after(x)}};
  say.appendChild(d);document.body.classList.add('chatting');
  // A fresh answer types itself out, as in ChatGPT; history and errors appear at once.
  if(o.type&&!err&&body&&!matchMedia('(prefers-reduced-motion:reduce)').matches){
    const parts=body.split(/(\s+)/);let i=0;const step=Math.max(1,Math.ceil(parts.length/80));
    const tick=()=>{i+=step;b.textContent=parts.slice(0,i).join('');say.scrollTop=say.scrollHeight;
      if(i<parts.length)setTimeout(tick,16);else{finish();say.scrollTop=say.scrollHeight}};tick()}
  else finish();
  say.scrollTop=say.scrollHeight;
  if(m){const a=document.createElement('a');a.className='open';
    a.href='/answers/'+encodeURIComponent(m[1])+'?t='+encodeURIComponent(token());
    a.target='_blank';a.textContent='OPEN FULL ANSWER';
    // Wide screen, chat view: open it beside the chat (the canvas) instead of a new tab.
    const wide=()=>document.body.classList.contains('fullchat')&&matchMedia('(min-width:1200px)').matches;
    const title=m[1].replace(/^\d{8}-\d{6}-/,'').replace(/\.html$/,'').replace(/-/g,' ');
    a.onclick=e=>{if(wide()){e.preventDefault();openCanvas(a.href,title)}};
    d.appendChild(document.createElement('br'));d.appendChild(a);
    // A fresh answer with a page opens it beside the chat straight away (14 Oct 2026: "it doesn't open the answers").
    if(o.type&&wide())openCanvas(a.href,title)}
  shared.forEach(id=>{const a=document.createElement('a');a.className='dl';a.href=shareLink(id);
    a.setAttribute('download','');a.textContent='⬇ DOWNLOAD';d.appendChild(document.createElement('br'));d.appendChild(a)});
  if(!err&&body){const bar=document.createElement('div');bar.className='msgacts';
    bar.append(actBtn('Copy',bt=>copyText(body,bt),'Copy this answer'));
    if(o.q){say.querySelectorAll('.regen').forEach(x=>x.remove());     // only the latest answer can be regenerated
      const r=actBtn('↻ Regenerate',()=>{if(!busy)resend(o.q,o.q,o.qnode)},'Ask again for a new answer');r.classList.add('regen');bar.append(r)}
    d.appendChild(bar)}
  if(shared.length)loadShares();
  return d}
function openCanvas(href,title){$('cvFrame').src=href;$('cvPop').href=href;$('cvTitle').textContent=title||'Answer';
  document.body.classList.add('cv-open')}
function shareLink(id){return '/get/'+encodeURIComponent(id)+'?t='+encodeURIComponent(token())}

// ---------- files between the PC and this phone ----------
// Waiting downloads (made by "send me X", by voice or typed) stay listed
// until they expire, so a file asked for by voice on the PC is found here.
async function loadShares(){
  try{const j=await api('/shares');const box=$('shares');box.innerHTML='';
    (j.shares||[]).forEach(s=>{const a=document.createElement('a');a.href=shareLink(s.id);
      a.setAttribute('download','');a.textContent='⬇ '+s.name;
      const sm=document.createElement('small');sm.textContent=s.minutes_left+' min';a.appendChild(sm);
      box.appendChild(a)})}catch(e){}
}
function sendToPC(files){
  let i=0;
  const next=()=>{if(i>=files.length)return;const f=files[i++];
    const w=line('sending '+f.name+' to the PC… 0%','tr');
    const x=new XMLHttpRequest();
    x.open('POST','/upload?name='+encodeURIComponent(f.name||'file'));
    x.setRequestHeader('X-Token',token());
    x.upload.onprogress=e=>{if(e.lengthComputable)w.textContent='sending '+f.name+' to the PC… '+Math.floor(100*e.loaded/e.total)+'%'};
    x.onload=()=>{let j={};try{j=JSON.parse(x.responseText)}catch(e){}
      w.remove();
      if(x.status==200)answer('Saved '+j.saved+' ('+j.size+') in '+j.folder+' on the PC.');
      else answer(j.error||('Upload failed (HTTP '+x.status+').'),true);
      next()};
    x.onerror=()=>{w.remove();answer('Cannot reach the PC to send '+f.name+'.',true);next()};
    x.send(f)};
  next();
}
$('toPC').onchange=()=>{const fs=Array.from($('toPC').files||[]);$('toPC').value='';if(fs.length)sendToPC(fs)};
loadShares();setInterval(()=>{if(!document.hidden)loadShares()},8000);
function term(){}   // the old terminal panel; activity now shows in the live feed

let busy=false,ctl=null;
const goHTML=$('go').innerHTML;
// While a typed question is being answered the send button is a Stop button.
function setBusy(b){busy=b;const g=$('go'),stop=b&&!!ctl;g.disabled=b&&!ctl;g.classList.toggle('stop',stop);
  g.innerHTML=stop?'<svg viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="7" width="10" height="10" rx="1.5"/></svg>':goHTML;
  g.title=stop?'Stop':'Send';g.setAttribute('aria-label',stop?'Stop':'Send')}
$('go').addEventListener('click',e=>{if(busy&&ctl){e.preventDefault();
  api('/ask/stop',{method:'POST',body:'{}'}).catch(()=>{});ctl.abort()}});

async function send(text,force,me){
  ctl=new AbortController();setBusy(true);const w=line('thinking','tr think');
  try{const j=await api('/ask',{method:'POST',body:JSON.stringify({text,confirm:!!force}),signal:ctl.signal});
    w.remove();(j.steps||[]).forEach(s=>line('› '+s.tool+'  '+(s.result||'').slice(0,160),'tr'));
    if(j.answer==='(stopped)'){line('Stopped.','tr');return}
    if(j.needs_confirm){const d=answer(j.answer);const b=document.createElement('button');
      b.className='open';b.textContent='RUN ANYWAY';b.onclick=()=>{b.remove();send(text,true,me)};d.appendChild(b)}
    else answer(j.answer,false,{type:true,q:text,qnode:me});
    if(voiceReply){voiceReply=false;speakOnPhone(j.answer)}
  }catch(e){w.remove();if(e&&e.name==='AbortError')line('Stopped.','tr');
    else answer('Cannot reach the PC. Is it awake and on Tailscale?',true)}
  finally{ctl=null;setBusy(false);if(typeof loadSide==='function')setTimeout(loadSide,400)}
}
function ask(text){if(busy||!text)return;const me=userLine(text);$('txYou').textContent=text;send(text,false,me)}

// Edit: put the message back in the box; sending cuts the chat back to before it and asks again.
let editing=null;
function startEdit(text,node){if(busy)return;editing={text,node};$('q').value=text;$('q').focus();
  document.body.classList.add('editing');let n=$('editNote');
  if(!n){n=document.createElement('div');n.id='editNote';$('f').before(n)}
  n.textContent='Editing your message - Enter to send, Esc to cancel'}
function stopEdit(){editing=null;document.body.classList.remove('editing');const n=$('editNote');if(n)n.remove()}
$('q').addEventListener('keydown',e=>{if(e.key==='Escape'&&editing){stopEdit();$('q').value=''}});
async function resend(oldText,newText,node){
  try{await api('/chats/rewind',{method:'POST',body:JSON.stringify({text:oldText})})}
  catch(e){answer('Could not go back to that message.',true);return}
  if(node&&node.isConnected){while(node.nextSibling)node.nextSibling.remove();node.remove()}
  ask(newText)}

// ---------- attachments ----------
let pending=null;
function chip(){ $('chip').innerHTML='';if(!pending)return;
  $('chip').textContent=(pending.doc?'📄 ':'🖼 ')+pending.name;
  const x=document.createElement('span');x.textContent='✕';x.onclick=()=>{pending=null;chip()};
  $('chip').appendChild(x)}
function takeFile(f){if(!f)return;const name=f.name||'pasted-image.png';
  const img=(f.type||'').startsWith('image/')||/\.(jpe?g|png|gif|webp|heic|bmp)$/i.test(name);
  const r=new FileReader();r.onload=()=>{pending={data:r.result,name,doc:!img};chip();$('q').focus()};
  r.readAsDataURL(f)}
function pick(input){input.onchange=()=>{const f=input.files&&input.files[0];input.value='';takeFile(f)}}
pick($('pick'));pick($('cam'));
// Paste a screenshot into the box, or drop a file anywhere on the page.
$('q').addEventListener('paste',e=>{const f=e.clipboardData&&e.clipboardData.files&&e.clipboardData.files[0];
  if(f){e.preventDefault();takeFile(f)}});
const hasFiles=e=>e.dataTransfer&&Array.from(e.dataTransfer.types||[]).includes('Files');
document.addEventListener('dragover',e=>{if(hasFiles(e)){e.preventDefault();document.body.classList.add('dropping')}});
document.addEventListener('dragleave',e=>{if(!e.relatedTarget)document.body.classList.remove('dropping')});
document.addEventListener('drop',e=>{document.body.classList.remove('dropping');if(!hasFiles(e))return;
  e.preventDefault();const f=e.dataTransfer.files[0];if(!f)return;
  if(!document.body.classList.contains('fullchat'))setView('chat');takeFile(f)});

// The camera button on the PC takes the webcam frame on the server (ffmpeg);
// on a phone the file input's `capture` opens the phone camera.
const onPC=['127.0.0.1','localhost','[::1]'].includes(location.hostname)
  ||matchMedia('(hover:hover) and (pointer:fine)').matches;
$('camBtn').onclick=e=>{if(!onPC)return;e.preventDefault();if(busy)return;
  const t=$('q').value.trim();$('q').value='';webcam(t||'What do you see?')};
async function webcam(text){
  setBusy(true);line(text,'me');const w=line('looking through the webcam…','tr');
  try{const j=await api('/look',{method:'POST',body:JSON.stringify({text,webcam:true})});
    w.remove();
    if(j.image){const im=document.createElement('img');im.className='shot';im.src=j.image;
      line('webcam','tr').appendChild(im)}
    answer(j.answer,/^ERROR:/.test(j.answer||''))}
  catch(e){w.remove();answer(String(e.message||e),true)}
  finally{setBusy(false)}
}
async function upload(text,att){
  setBusy(true);const w=line((att.doc?'reading ':'looking at ')+att.name+'…','tr');
  try{const j=att.doc
      ? await api('/read',{method:'POST',body:JSON.stringify({text,name:att.name,data:att.data})})
      : await api('/look',{method:'POST',body:JSON.stringify({text,image:att.data})});
    w.remove();answer(j.answer)}
  catch(e){w.remove();answer(String(e.message||e),true)}
  finally{setBusy(false)}
}
$('f').onsubmit=e=>{e.preventDefault();if(busy)return;
  const t=$('q').value.trim();
  if(editing&&!pending){const ed=editing;stopEdit();$('q').value='';if(t)resend(ed.text,t,ed.node);return}
  if(pending){const a=pending;pending=null;chip();$('q').value='';
    const said=t||(a.doc?'Work through this and answer it.':'What is in this picture?');
    const me=line(said,'me');
    if(!a.doc){const im=document.createElement('img');im.className='shot';im.src=a.data;me.appendChild(im)}
    upload(said,a);return}
  if(!t)return;$('q').value='';ask(t)};
$('topq').onkeydown=e=>{if(e.key==='Enter'){const t=$('topq').value.trim();$('topq').value='';ask(t)}};
// Menu items open the full chat, marked as the current page, so the answer
// is on screen - run inside the dashboard's small chat box it looked like a
// click that did nothing.
document.querySelectorAll('[data-cmd]').forEach(b=>b.onclick=()=>{closeVeils();
  if(b.hasAttribute('data-nav')){setView('chat');document.querySelectorAll('aside nav button').forEach(x=>x.classList.toggle('on',x===b))}
  ask(b.dataset.cmd)});

// ---------- views ----------
let lastView='stage';
function setView(v){lastView=v;setTimeout(()=>{if(typeof markTab==='function')markTab()});if(typeof closePage==='function'&&pageName)closePage();document.body.classList.toggle('fullchat',v==='chat');document.body.classList.toggle('stage',v==='stage');document.body.classList.toggle('dash',v==='dash');
  document.querySelectorAll('nav button[data-view]').forEach(b=>b.classList.toggle('on',b.dataset.view===v));
  if(v==='chat'){document.body.classList.add('chatting');say.scrollTop=say.scrollHeight;loadSide()}}
document.querySelectorAll('nav button[data-view]').forEach(b=>b.onclick=()=>{closeVeils();setView(b.dataset.view)});
$('expandBtn').onclick=()=>setView(document.body.classList.contains('fullchat')?'dash':'chat');
$('focusBtn').onclick=()=>ask('study mode');


// ---------- pages ----------
const esc2=s=>esc(String(s==null?'':s));
let pageName='',pageTimer=null;
setView('stage');            // the stage is the home screen
const TITLES={calendar:['CALENDAR','Next 7 days · UPES timetable + Google Calendar · live'],memory:['MEMORY','What Athena knows about you'],
  workflows:['WORKFLOWS','Routines and what is playing'],automations:['AUTOMATIONS','What Athena does on her own - switch any of it off'],goals:['GOALS','Long-running goals - Athena plans the days and keeps you on them'],missions:['MISSIONS','Big jobs Claude does in the background - websites, programs, documents, research'],system:['SYSTEM REPORT','Live health of this PC'],
  tasks:['TASKS','What to do today, and what is left'],phone:['CALLS & BOOKINGS','Call through your phone, look places up, book online'],skills:['TOOLS & SKILLS','Everything Athena can do'],store:['RAGAZZA','Your store · orders, money and stock'],brain:['BRAIN','What Athena knows, and how she is built - drag, zoom, hover'],
  models:['MODELS','The language models Athena can use, and which are ready']};
// System Monitor and LLM Status left the dashboard for their pages. They are
// moved, not copied: the same live elements keep updating, and they go back
// (hidden) into <main> when the page closes so $('rings') etc. always exist.
function parkPanels(){const m=document.querySelector('main');[$('monCard'),$('llmCard')].forEach(c=>{if(c.parentNode!==m)m.appendChild(c)})}
function openPage(name,btn){if(name!=='missions'&&PAGES._brainObj){try{PAGES._brainObj.destroy()}catch(e){}PAGES._brainObj=null;PAGES._brainEl=null}setTimeout(()=>{if(typeof markTab==='function')markTab()});parkPanels();pageName=name;closeVeils();document.body.classList.remove('fullchat');document.body.classList.add('pageview');
  document.querySelectorAll('aside nav button').forEach(x=>x.classList.toggle('on',x===btn||x.dataset.page===name));
  $('pgTitle').textContent=TITLES[name][0];$('pgNote').textContent=TITLES[name][1];clearInterval(pageTimer);
  if(name==='models'){$('pgBody').innerHTML='';$('pgBody').appendChild($('llmCard'));return}
  $('pgBody').innerHTML='<div class="muted">Loading…</div>';renderPage();
  pageTimer=setInterval(()=>{if(['workflows','automations','goals','missions','system','phone','store','calendar'].includes(pageName))renderPage(true)},name==='workflows'?2000:name==='store'||name==='calendar'?30000:5000)}
function closePage(){try{if(PAGES._brainObj){PAGES._brainObj.destroy();PAGES._brainObj=null;PAGES._brainEl=null}}catch(e){}setTimeout(()=>{if(typeof markTab==='function')markTab()});pageName='';clearInterval(pageTimer);parkPanels();document.body.classList.remove('pageview');
  document.querySelectorAll('aside nav button').forEach(x=>x.classList.toggle('on',x.dataset.view===lastView))}
$('pgBack').onclick=closePage;
async function renderPage(quiet){const name=pageName;if(!name)return;
  try{const d=await api('/view/'+name);if(name!==pageName)return;
    if(name==='system')parkPanels();               // re-rendering replaces the page body
    PAGES[name](d,quiet);
    if(name==='system')$('pgBody').prepend($('monCard'))}
  catch(e){if(!quiet)$('pgBody').innerHTML='<div class="muted">Could not load: '+esc2(e.message)+'</div>'}}
async function act(page,body){try{const j=await api('/view/'+page,{method:'POST',body:JSON.stringify(body)});return j.said||''}catch(e){return 'Failed: '+e.message}}
const fmt=s=>{s=Math.max(0,Math.round(s||0));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')};
// ---------- Needs you · Loops · Usage on the Missions page (20 Oct 2026, ideas from Symvibe) ----------
const agoT=ts=>{const s=Math.max(0,Date.now()/1000-ts);return s<60?'just now':s<3600?Math.round(s/60)+' min ago':s<86400?Math.round(s/3600)+' h ago':Math.round(s/86400)+' d ago'};
const NEED_ICON={blocked:'⏸',breaker:'⚡',ask:'?',question:'✉',failed:'!',loop:'↻',console:'\u25A3'};
function needsPanel(d){
  const n=d.needs||[];if(!n.length)return null;
  const el=document.createElement('section');el.className='needs';el.setAttribute('aria-label','Needs you');
  el.innerHTML='<div class="needs-h"><span class="needs-dot"></span><b>NEEDS YOU</b><span class="needs-n">'+n.length+'</span><small>only you can unblock these</small></div>'+
    n.map(i=>'<div class="need k-'+esc2(i.kind)+'" data-key="'+esc2(i.key)+'">'+
      '<div class="need-av"><span>'+esc2(String(i.who||'?').trim().charAt(0).toUpperCase())+'</span><i>'+(NEED_ICON[i.kind]||'!')+'</i></div>'+
      '<div class="need-b"><div class="need-t"><b>'+esc2(i.who)+'</b> '+esc2(i.title)+' <small>· '+agoT(i.since)+'</small></div>'+
      (i.text?'<div class="need-x">'+esc2(i.text)+'</div>':'')+
      '<div class="need-a">'+(i.can_reply?'<input class="minp" data-nin placeholder="Your answer…"><button class="mbtn go" data-nsend>Reply</button>':'')+
        (i.can_retry?'<button class="mbtn go" data-nretry>Try again</button>':'')+
        (i.kind==='console'?'<button class="mbtn" data-ndeck="'+esc2(i.console)+'">Open in Deck</button>':'')+
        '<button class="mbtn" data-ndis>Dismiss</button></div></div></div>').join('');
  const key=b=>b.closest('.need').dataset.key;
  const done=async body=>{const said=await act('missions',body);const e=$('mSaid');if(e&&said)e.textContent=said;PAGES._mSig='';renderPage(true)};
  el.querySelectorAll('[data-nsend]').forEach(b=>b.onclick=()=>{const i=b.closest('.need').querySelector('[data-nin]');if(!i.value.trim()){i.focus();return}
    b.disabled=true;done({action:'need_reply',key:key(b),text:i.value.trim()})});
  el.querySelectorAll('[data-nin]').forEach(i=>i.onkeydown=e=>{if(e.key==='Enter')i.closest('.need').querySelector('[data-nsend]').click()});
  el.querySelectorAll('[data-nretry]').forEach(b=>b.onclick=()=>{b.disabled=true;done({action:'need_retry',key:key(b)})});
  el.querySelectorAll('[data-ndeck]').forEach(b=>b.onclick=()=>act('missions',{action:'deck_show',console:b.dataset.ndeck}));
  el.querySelectorAll('[data-ndis]').forEach(b=>b.onclick=()=>{b.closest('.need').style.opacity=.35;done({action:'need_dismiss',key:key(b)})});
  return el}
// Athena Deck (deck/): the board of live agent consoles - what's on it, and a way in.
function deckBox(d){
  const k=d.deck||{},cs=k.consoles||[],word={needs:'needs you',working:'working',resting:'resting',starting:'starting',exited:'stopped'};
  return '<div class="pbox deckbox" style="margin-bottom:16px"><div class="sec-h"><b>ATHENA DECK</b><small>'+
    (k.running?cs.length+' console'+(cs.length===1?'':'s')+' on the board':'your board of live Claude, Gemini and PowerShell consoles')+'</small></div>'+
    (k.running&&cs.length?'<div class="deck-cons">'+cs.map(c=>'<button class="deck-con s-'+esc2(c.state)+'" data-dcon="'+esc2(c.id)+'" title="'+esc2(c.detail||word[c.state]||c.state)+'">'+
      '<i></i><b>'+c.n+'</b>'+esc2(c.title)+'<small>'+esc2(word[c.state]||c.state)+'</small></button>').join('')+'</div>':'')+
    '<div class="deck-foot"><span class="muted">Say \u201copen two Claude consoles on the orb site\u201d or \u201cwhat are my consoles doing\u201d.</span>'+
    '<button class="mbtn go" id="deckOpen">'+(k.running?'Show Deck':'Open Deck')+'</button></div></div>'}
function wireDeck(say){
  if($('deckOpen'))$('deckOpen').onclick=async()=>{const b=$('deckOpen');b.disabled=true;b.textContent='Opening\u2026';say(await act('missions',{action:'deck_open'}));PAGES._mSig='';renderPage(true)};
  $('pgBody').querySelectorAll('[data-dcon]').forEach(b=>b.onclick=()=>act('missions',{action:'deck_show',console:b.dataset.dcon}))}
const everyTxt=m=>m>=1440&&m%1440===0?'daily':m%60===0?(m===60?'hourly':'every '+m/60+' h'):'every '+Math.round(m)+' min';
const inTxt=s=>s<90?'in a moment':s<3600?'in '+Math.round(s/60)+' min':'in '+(s/3600).toFixed(1).replace(/\.0$/,'')+' h';
setInterval(()=>document.querySelectorAll('[data-next]').forEach(e=>{e.textContent='next '+inTxt(+e.dataset.next-Date.now()/1000)}),30000);
function loopsBox(d){
  const ls=d.loops||[],now=Date.now()/1000;
  const pill={active:['WATCHING','on'],met:['GOAL MET ✓','met'],gave_up:['GAVE UP','bad'],paused:['PAUSED','bad'],stopped:['STOPPED','off']};
  const dots=lp=>{const h=lp.history||[],out=[];for(let i=0;i<lp.max_runs;i++){let c='',t='Run '+(i+1);
      if(i<lp.runs){const r=h[h.length-lp.runs+i]||{};c=r.met===true?'met':r.met===false?'no':'unk';t+=': '+(r.note||'')}
      else if(i===lp.runs&&lp.mission){c='now';t+=': checking now'}
      out.push('<i class="'+c+'" title="'+esc2(t)+'"></i>')}return out.join('')};
  return '<div class="pbox" style="margin-bottom:16px"><div class="sec-h"><b>LOOPS</b><small>repeat a job until its goal is met — then it stops by itself</small></div>'+
    (ls.length?ls.map(lp=>{const p=pill[lp.status]||[String(lp.status).toUpperCase(),'off'];
      const when=lp.status!=='active'?'':lp.mission?'checking now':'<span data-next="'+lp.next+'">next '+inTxt(lp.next-now)+'</span>';
      return '<div class="loop s-'+esc2(lp.status)+'"><div class="loop-top"><div class="loop-main"><div class="loop-task">'+esc2(lp.task)+'</div>'+
        '<div class="loop-until">until '+esc2(lp.until)+'</div></div><span class="lpill '+p[1]+'">'+p[0]+'</span></div>'+
        '<div class="loop-dots">'+dots(lp)+'<small>'+lp.runs+'/'+lp.max_runs+' runs · '+everyTxt(lp.every)+(when?' · '+when:'')+' · '+(lp.worker==='gemini'?'Gemini':'Claude')+'</small></div>'+
        (lp.note?'<div class="loop-note">'+esc2(lp.note)+'</div>':'')+
        '<div class="loop-act">'+(lp.status==='active'?'<button class="mbtn stop" data-lstop="'+lp.id+'">Stop</button>':'<button class="mbtn" data-lrm="'+lp.id+'">Clear</button>')+'</div></div>'}).join(''):
      '<div class="muted" style="padding:2px 0 4px">None yet. Say “every 30 minutes, check the orb site on phones until nothing scrolls sideways”, or start one here.</div>')+
    '<div class="loop-form"><select id="lpEvery" class="minp" aria-label="How often">'+[[10,'every 10 min'],[15,'every 15 min'],[30,'every 30 min'],[60,'every hour'],[120,'every 2 hours'],[360,'every 6 hours'],[1440,'daily']]
      .map(([v,t])=>'<option value="'+v+'"'+(v===30?' selected':'')+'>'+t+'</option>').join('')+'</select>'+
    '<input id="lpTask" class="minp" placeholder="each run: e.g. check the orb landing page at 360px">'+
    '<input id="lpUntil" class="minp" placeholder="until: e.g. nothing scrolls sideways">'+
    '<select id="lpWho" class="minp" aria-label="Worker"><option value="claude">Claude</option><option value="gemini">Gemini</option></select>'+
    '<button class="mbtn go" id="lpGo" title="Each run is a mission - Claude runs use your limits">Start loop</button></div></div>'}
function wireLoops(say){
  if($('lpGo'))$('lpGo').onclick=async()=>{const b=$('lpGo'),t=$('lpTask').value.trim(),u=$('lpUntil').value.trim();if(!t||!u){say('Say what each run does, and when to stop.');return}
    if(!b.dataset.sure){b.dataset.sure='1';b.textContent='Tap again — runs until it’s met';setTimeout(()=>{if(b.isConnected){delete b.dataset.sure;b.textContent='Start loop'}},5000);return}
    b.disabled=true;say(await act('missions',{action:'loop_add',task:t,until:u,every:$('lpEvery').value,worker:$('lpWho').value}));PAGES._mSig='';document.activeElement.blur();renderPage(true)};
  $('pgBody').querySelectorAll('[data-lstop]').forEach(b=>b.onclick=async()=>{b.disabled=true;say(await act('missions',{action:'loop_stop',id:b.dataset.lstop}));PAGES._mSig='';renderPage(true)});
  $('pgBody').querySelectorAll('[data-lrm]').forEach(b=>b.onclick=async()=>{await act('missions',{action:'loop_remove',id:b.dataset.lrm});PAGES._mSig='';renderPage(true)})}
const fmt$=v=>'$'+(v>=100?v.toFixed(0):v>=10?v.toFixed(1):v.toFixed(2));
const UCOL={athena:'var(--u1)',code:'var(--u3)'};
const niceMax=m=>{const p=Math.pow(10,Math.floor(Math.log10(m)));for(const f of [1,2,2.5,5,10])if(f*p>=m)return f*p;return 10*p};
function spark(vals,color){const W=120,H=26,mx=Math.max(...vals,1e-9),bw=W/vals.length;
  return '<svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" aria-hidden="true">'+vals.map((v,i)=>{const h=v?Math.max(2,v/mx*(H-3)):0;
    return h?'<rect x="'+(i*bw+1).toFixed(1)+'" y="'+(H-1-h).toFixed(1)+'" width="'+Math.max(1,bw-2).toFixed(1)+'" height="'+h.toFixed(1)+'" rx="1.5" fill="'+color+'"/>':''}).join('')+
    '<line x1="0" x2="'+W+'" y1="'+(H-.5)+'" y2="'+(H-.5)+'" stroke="rgba(150,170,205,.22)"/></svg>'}
function usageChart(u,W){                 // drawn at the panel's real width: labels stay 10.5px on any screen
  const S=u.series,days=u.days,n=days.length,tot=days.map((_,i)=>S.reduce((a,s)=>a+s.values[i],0)),max=niceMax(Math.max(...tot,0.5));
  const H=W<500?170:220,L=W<500?36:46,R=4,T=10,B=24,ph=H-T-B,cw=(W-L-R)/n,bw=Math.min(30,cw*.62),y=v=>T+ph-v/max*ph,every=W<500?3:2;
  let g='';[0,.5,1].forEach(f=>{const yy=y(max*f).toFixed(1);g+='<line class="'+(f?'u-grid':'u-base')+'" x1="'+L+'" x2="'+(W-R)+'" y1="'+yy+'" y2="'+yy+'"/>'+
    '<text class="u-axis" x="'+(L-8)+'" y="'+(+yy+3.5)+'" text-anchor="end">'+(f?fmt$(max*f).replace(/\.0+$/,''):'$0')+'</text>'});
  let bars='';
  days.forEach((ds,i)=>{const x=+(L+i*cw+(cw-bw)/2).toFixed(1),nz=S.filter(s=>s.values[i]>0);let base=T+ph,segs='';
    nz.forEach((s,k)=>{const h=s.values[i]/max*ph,gap=k?2:0,hh=Math.max(1,h-gap),yy=+(base-gap-hh).toFixed(1);   // 2px surface gap between segments
      segs+=k===nz.length-1&&hh>5?'<path class="seg" fill="'+UCOL[s.key]+'" d="M'+x+','+(yy+hh).toFixed(1)+'V'+(yy+4)+'Q'+x+','+yy+' '+(x+4)+','+yy+'H'+(x+bw-4).toFixed(1)+'Q'+(x+bw).toFixed(1)+','+yy+' '+(x+bw).toFixed(1)+','+(yy+4)+'V'+(yy+hh).toFixed(1)+'Z"/>'
        :'<rect class="seg" fill="'+UCOL[s.key]+'" x="'+x+'" y="'+yy+'" width="'+bw.toFixed(1)+'" height="'+hh.toFixed(1)+'"/>';base=yy});
    const day=new Date(ds+'T12:00:00'),lab=i===n-1?'today':((n-1-i)%every===0&&n-1-i>=every?String(day.getDate()):'');
    bars+='<g class="u-col" data-i="'+i+'" data-top="'+base.toFixed(1)+'" tabindex="0" aria-label="'+esc2(ds)+': '+fmt$(tot[i])+'">'+segs+
      '<rect class="u-hit" x="'+(L+i*cw).toFixed(1)+'" y="'+T+'" width="'+cw.toFixed(1)+'" height="'+ph+'"/>'+
      (lab?'<text class="u-axis" x="'+(L+i*cw+cw/2).toFixed(1)+'" y="'+(H-7)+'" text-anchor="middle">'+lab+'</text>':'')+'</g>'});
  return '<svg width="'+W+'" height="'+H+'" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Claude spend per day for the last 14 days, by source">'+g+bars+'</svg>'}
function usageBox(d){
  const u=d.usage||{};if(!u.series)return '';
  const S=u.series,days=u.days,tot=days.map((_,i)=>S.reduce((a,s)=>a+s.values[i],0));
  const cap=u.cap||15,pct=Math.min(100,(u.athena_month||0)/cap*100);
  const hero='<div class="u-hero"><div class="u-ringwrap" title="Athena missions against the monthly cap"><div class="u-ring" style="background:conic-gradient(var(--u1) '+pct.toFixed(1)+'%,rgba(150,170,205,.14) 0)"></div><b>'+Math.round(pct)+'%</b></div>'+
    '<div style="min-width:0"><div class="u-lbl">'+esc2(u.month_name||'')+' so far</div><div class="u-big">'+fmt$(u.month_total||0)+'</div><div class="u-sub">missions '+fmt$(u.athena_month||0)+' of the $'+cap+' cap</div></div></div>';
  const tiles=S.map(s=>'<div class="u-tile"><div class="u-lbl"><span class="u-key" style="background:'+UCOL[s.key]+'"></span>'+esc2(s.label)+'</div><div class="u-val">'+fmt$(s.month)+'</div><div class="u-sub">today '+fmt$(s.today)+'</div></div>').join('');
  const rows=(u.rows||[]).map(r=>'<div class="u-row"><div class="n">'+esc2(r.label)+'<small>'+esc2(r.sub)+'</small></div>'+spark(r.values,r.key==='runs'?'rgba(150,170,205,.55)':UCOL[r.key])+
    '<div class="v">'+(r.key==='runs'?r.runs+' run'+(r.runs===1?'':'s'):fmt$(r.month))+'</div></div>').join('');
  const table='<div class="u-tablewrap" hidden id="uTable"><table class="u-table"><thead><tr><th>Day</th>'+S.map(s=>'<th>'+esc2(s.label)+'</th>').join('')+'<th>Total</th></tr></thead><tbody>'+
    days.map((ds,i)=>'<tr><td>'+ds+'</td>'+S.map(s=>'<td>'+fmt$(s.values[i])+'</td>').join('')+'<td>'+fmt$(tot[i])+'</td></tr>').reverse().join('')+'</tbody></table></div>';
  return '<div class="pbox usage" style="margin-bottom:16px"><div class="sec-h"><b>USAGE</b><small>where your Claude limits go · last 14 days</small></div>'+
    '<div class="u-top">'+hero+tiles+'</div>'+
    '<div class="u-leg">'+S.map(s=>'<span><i style="background:'+UCOL[s.key]+'"></i>'+esc2(s.label)+' <b>'+fmt$(s.month)+'</b></span>').join('')+'</div>'+
    '<div class="u-chart" id="uChart"><div class="u-tip" id="uTip" hidden></div></div>'+
    '<div class="u-rows">'+rows+'</div>'+table+
    '<div class="u-foot"><span>API-price estimates — on your subscriptions this is limit use, not a bill. Gemini and Argon run on the Google plan.</span><button class="mbtn" id="uTbl">Table</button></div></div>'}
function wireUsage(d){
  const u=d.usage||{},c=$('uChart'),tip=$('uTip');if(!c||!u.series)return;
  const row=(color,val,label)=>{const r=document.createElement('div');r.className='r';const k=document.createElement('i');if(color)k.style.background=color;
    const b=document.createElement('b');b.textContent=val;const l=document.createElement('span');l.textContent=label;r.append(k,b,l);return r};
  const show=g=>{const i=+g.dataset.i,svg=c.querySelector('svg');c.querySelectorAll('.u-col.hov').forEach(x=>x.classList.remove('hov'));g.classList.add('hov');tip.replaceChildren();
    const t=document.createElement('div');t.className='t';t.textContent=new Date(u.days[i]+'T12:00:00').toLocaleDateString([],{weekday:'short',day:'numeric',month:'short'});tip.append(t);
    let sum=0;u.series.slice().reverse().forEach(s=>{sum+=s.values[i];tip.append(row(UCOL[s.key],fmt$(s.values[i]),s.label))});
    const tr=row('',fmt$(sum),'total');tr.style.marginTop='6px';tip.append(tr);
    const hit=g.querySelector('.u-hit'),cx=+hit.getAttribute('x')+(+hit.getAttribute('width'))/2,top=+g.dataset.top;tip.hidden=false;
    const above=top-tip.offsetHeight-10>=0;                  // just above the bar; below its top when there's no room
    tip.style.left=Math.min(svg.clientWidth-tip.offsetWidth/2-4,Math.max(tip.offsetWidth/2+4,cx))+'px';
    tip.style.top=(above?top-8:top+10)+'px';tip.style.transform=above?'translate(-50%,-100%)':'translate(-50%,0)'};
  const hide=g=>{g.classList.remove('hov');tip.hidden=true};
  const draw=()=>{const w=Math.floor(c.clientWidth);if(!w||w===c._w)return;c._w=w;const old=c.querySelector('svg');if(old)old.remove();
    c.insertAdjacentHTML('afterbegin',usageChart(u,w));
    c.querySelectorAll('.u-col').forEach(g=>{g.addEventListener('pointerenter',()=>show(g));g.addEventListener('pointerleave',()=>hide(g));
      g.addEventListener('focus',()=>show(g));g.addEventListener('blur',()=>hide(g))})};
  draw();if(window.ResizeObserver){if(PAGES._uRO)PAGES._uRO.disconnect();PAGES._uRO=new ResizeObserver(draw);PAGES._uRO.observe(c)}
  if($('uTbl'))$('uTbl').onclick=()=>{const t=$('uTable');t.hidden=!t.hidden;$('uTbl').textContent=t.hidden?'Table':'Hide table'}}
// vis-network, the same graph library graphify draws with; fetched only
// when the Brain page is first opened.
let visLoad=null;const needVis=()=>visLoad||(visLoad=new Promise((ok,no)=>{const s=document.createElement('script');
  s.src='https://cdn.jsdelivr.net/npm/vis-network@9.1.6/standalone/umd/vis-network.min.js';s.onload=ok;s.onerror=()=>{visLoad=null;no(new Error('graph library did not load'))};document.head.appendChild(s)}));
const BRAIN_COL={core:'#c4a8ff',hub:'#8f72ff',area:'#b38cff',tool:'#6f9bb0',music:'#ff6bd6',people:'#34f5a5',
  facts:'#f7b84b',routines:'#ffa34d',tasks:'#ff6b7a'};
let brainTab='mind',brainNetObj=null;
// One look for both graphs: glowing dots on the HUD's dark blue.
function drawGraph(nodes,edges,big){if(brainNetObj){brainNetObj.destroy();brainNetObj=null}
  const net=brainNetObj=new vis.Network($('brainNet'),{nodes:new vis.DataSet(nodes),
    edges:new vis.DataSet(edges.map(e=>({...e,color:{color:big?'rgba(196,168,255,.12)':'rgba(196,168,255,.25)',highlight:'#c4a8ff'},width:1})))},
    // No moving dots: the code graph comes with its positions worked out on
    // the PC; the small one is settled off-screen before it is shown.
    {physics:big?false:{solver:'forceAtlas2Based',forceAtlas2Based:{gravitationalConstant:-45,springLength:70},stabilization:{iterations:250}},
     interaction:{hover:true,tooltipDelay:120,hideEdgesOnDrag:big},layout:{improvedLayout:!big},nodes:{borderWidth:1}});
  if(big)net.fit();
  else net.once('stabilizationIterationsDone',()=>{net.setOptions({physics:false});net.fit();
    setTimeout(()=>net.fit(),350)});   // once more: the first fit can land before the last move (graph cut off at the top)
  // Maximising the window after the fit left the graph zoomed in and cut off.
  let rt;new ResizeObserver(()=>{clearTimeout(rt);rt=setTimeout(()=>net.fit(),200)}).observe($('brainNet'))}
// Click a dot: play that artist, call that person, run that routine, tick
// that task, forget that memory - the same actions as saying it or using
// the other pages.
function brainClicks(d){const byId={};d.nodes.forEach(n=>byId[n.id]=n);
  const pop=document.createElement('div');pop.id='brainPop';
  pop.style.cssText='position:absolute;display:none;z-index:5;min-width:170px;padding:10px;border-radius:10px;background:rgba(6,19,29,.96);border:1px solid var(--c);box-shadow:var(--glow);font:13px var(--head)';
  $('brainNet').style.position='relative';$('brainNet').appendChild(pop);
  const hide=()=>pop.style.display='none';
  brainNetObj.on('click',async p=>{const n=byId[p.nodes[0]];if(!n||!(n.acts||[]).length){hide();return}
    pop.innerHTML='<div style="color:var(--c);font-weight:600;margin-bottom:6px">'+esc2(n.full||n.label)+'</div>'+
      n.acts.map((a,i)=>'<button data-i="'+i+'" style="display:block;width:100%;margin:4px 0;padding:6px 10px;border-radius:8px;cursor:pointer;background:rgba(196,168,255,.1);border:1px solid var(--line);color:var(--txt);text-align:left">'+esc2(a.label)+'</button>').join('')+
      '<div class="muted" id="brainPopSaid" style="margin-top:6px;font-size:12px"></div>';
    pop.style.left=Math.min(p.pointer.DOM.x+12,$('brainNet').clientWidth-200)+'px';pop.style.top=(p.pointer.DOM.y+12)+'px';pop.style.display='block';
    pop.querySelectorAll('button').forEach(b=>b.onclick=async()=>{const a=n.acts[+b.dataset.i];const said=$('brainPopSaid');
      if(a.ask){ask(a.ask);said.textContent='Asked Athena: '+a.ask;setTimeout(hide,1800)}
      else if(a.fill){hide();closePage();$('q').value=a.fill;$('q').focus()}
      else if(a.act){said.textContent='…';said.textContent=(await act(a.act[0],a.act[1]))||'Done.';setTimeout(()=>{hide();renderPage()},1500)}})});
  brainNetObj.on('dragStart',hide)}
const glow=(c,size,label,title,fs,fc)=>({label,title,shape:'dot',size,
  color:{background:c,border:c,highlight:{background:'#fff',border:c}},font:{color:fc||'#cfefff',size:fs,face:'Rajdhani'},
  shadow:{enabled:true,color:c,size:Math.max(8,size),x:0,y:0}});
async function brainShow(d){
  $('brainInfo').textContent='Loading…';
  try{await needVis()}catch(e){$('brainInfo').textContent=e.message;return}
  if(brainTab==='mind'){
    const col=g=>BRAIN_COL[g]||(g.startsWith('mem_')?'#7ee8fa':'#6f9bb0');
    const priv=document.body.classList.contains('tour');   // tour: no names or personal facts on screen
    drawGraph(d.nodes.map(n=>({id:n.id,...glow(col(n.group),n.group==='core'?34:n.group==='hub'?20:n.group==='area'?12:7,priv&&/^(people|facts|mem_)/.test(n.group)&&n.group!=='hub'?'':n.label,priv?'':(n.title||n.label),
      n.group==='core'?22:n.group==='hub'?17:13)})),d.edges,false);
    $('brainInfo').textContent=d.counts.things+' things · '+d.counts.links+' links - click a dot to act on it';
    brainClicks(d);return}
  let c;try{c=await api('/view/codegraph')}catch(e){$('brainInfo').textContent='Could not load: '+e.message;return}
  if(c.error){$('brainInfo').textContent=c.error;return}
  const deg={};c.edges.forEach(e=>{deg[e.from]=(deg[e.from]||0)+1;deg[e.to]=(deg[e.to]||0)+1});
  // vis-network draws nothing for hsl() colours - hex only.
  const hue=k=>{const h=(k*137.5)%360/60,x=Math.round(255*(1-Math.abs(h%2-1))),f=[[255,x,70],[x,255,70],[70,255,x],[70,x,255],[x,70,255],[255,70,x]][Math.floor(h)%6];
    return '#'+f.map(v=>Math.max(70,v).toString(16).padStart(2,'0')).join('')};
  // Only the well-connected parts are named on the picture; hover for the rest.
  drawGraph(c.nodes.map(n=>{const k=deg[n.id]||0;return {id:n.id,x:n.x,y:n.y,...glow(hue(n.c),4+Math.min(18,Math.sqrt(k)*3),k>=10?n.label:'',n.title,k>=25?16:12)}}),c.edges,true);
  $('brainInfo').textContent=c.counts.things+' parts · '+c.counts.links+' links - Athena\'s own code, mapped by graphify · colour = area'}
// Tour mode (?tour=1): walks through the pages by itself, for screenshots
// and screen recordings - the Store and Memory pages (real revenue, personal
// facts) are left out. The title carries the step so a recorder can follow.
(function(){if(!p.get('tour'))return;   // p: the address before the token was stripped from it
  document.body.classList.add('tour');   // hides names, personal facts and past chats for public screenshots
  const steps=[['chat',()=>{openChat('new');setView('chat');setTimeout(()=>ask('What can you do?'),1500)}],
    ['home',()=>{closePage();setView('stage')}],
    ['skills',()=>openPage('skills',null)],
    ['brain',()=>{brainTab='mind';openPage('brain',null)}],
    ['code',()=>{brainTab='code';openPage('brain',null)}],
    ['calls',()=>openPage('phone',null)],
    ['calendar',()=>openPage('calendar',null)],
    ['tasks',()=>openPage('tasks',null)],
    ['system',()=>openPage('system',null)],
    ['end',()=>{closePage();setView('dash')}]];
  // ?tour=demo: real commands, typed into the chat one after another, for a
  // screen recording of Athena doing things. Harmless ones only - nothing
  // that calls, messages, buys or deletes.
  if(p.get('tour')==='demo'){steps.length=0;
    const cmd=(t,ms)=>['cmd',()=>ask(t),ms];
    steps.push(['chat',()=>{openChat('new');setView('chat')},3000],
      cmd('open paint',7000),cmd('close paint',6000),   // not Notepad: it reopens your last files - a .env was on screen (27 Sep 2026)
      
      cmd('open calculator',7000),cmd('close calculator',6000),
      cmd('what can you do',8000),
      cmd('open spotify',10000),cmd('play something I like',16000),cmd('pause the music',6000),cmd('close spotify',7000),
      cmd('system report',9000),
      cmd("what's on my screen",45000),
      ['end',()=>{},3000])}
  let i=0;const next=()=>{const [name,fn]=steps[i];try{fn()}catch(e){}document.title='Athena Tour '+i+' '+name;
    const wait=steps[i][2]||(name==='chat'?32000:8000);i++;if(i<steps.length)setTimeout(next,wait);else setTimeout(()=>document.title='Athena Tour done',4000)};
  setTimeout(next,5000)})();
const PAGES={
 goals(d,quiet){
   const act_=d.goals.filter(g=>g.status==='active');$('nGoals').textContent=act_.length||'-';
   if(quiet&&document.activeElement&&document.activeElement.closest&&document.activeElement.closest('#pgBody'))return;
   const H=t=>'<h4 style="font:600 12px var(--mono);letter-spacing:2px;color:var(--c);margin:18px 0 8px">'+t+'</h4>';
   const kind={study:'📖',practice:'✍️',quiz:'❓',review:'🔁'};
   const row=(g,t,big)=>'<label class="gtask'+(t.done?' done':'')+'" style="display:flex;gap:10px;align-items:flex-start;padding:'+(big?'10px 12px':'6px 8px')+';border-radius:10px;cursor:pointer">'+
     '<input type="checkbox" data-g="'+g.id+'" data-t="'+t.id+'" '+(t.done?'checked':'')+(g.status!=='active'?' disabled':'')+' style="margin-top:3px;accent-color:var(--c);width:16px;height:16px">'+
     '<span style="flex:1;min-width:0"><span style="'+(t.done?'text-decoration:line-through;opacity:.6':'')+'">'+(kind[t.kind]||'•')+' '+esc2(t.task)+'</span>'+
     '<small class="muted" style="display:block;margin-top:2px">'+esc2(t.kind)+' · '+(t.minutes||45)+' min'+(t.score?' · scored '+esc2(t.score):'')+(t.moved?' · moved here':'')+
     (t.kind==='quiz'&&!t.done?' · say “quiz me on '+esc2(t.topic)+'”':'')+'</small></span>'+
     (!t.done&&g.status==='active'?'<button class="act" data-start="'+t.id+'" data-goal="'+g.id+'" title="Do it now in the project chat" style="padding:4px 10px;flex:none">▶ Start</button>':'')+'</label>';
   let html='';
   if(!d.goals.length)html='<div class="hero" style="padding:18px 20px"><div class="big" style="font-size:22px">No goals yet</div>'+
     '<div class="muted" style="margin-top:6px">Say or type: “Athena, prepare me for my Signals end-sem by Friday”. She reads the project’s course files, '+
     'plans every day up to the deadline, puts today’s tasks in your morning brief, nudges you in the evening, and moves anything you miss into the days left.</div></div>';
   d.goals.forEach(g=>{const pct=Math.round(100*g.done/Math.max(1,g.total)),live=g.status==='active';
     const today=g.days.find(x=>x.today);
     html+='<div class="pbox" style="margin-bottom:16px;opacity:'+(live?1:.6)+'">'+
       '<div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap">'+
       '<div style="min-width:0"><div class="muted">'+(g.project?esc2(g.project)+' · ':'')+(live?(g.days_left>0?g.days_left+' day'+(g.days_left>1?'s':'')+' left':g.days_left===0?'deadline today':'past deadline'):g.status.toUpperCase())+' · due '+esc2(g.deadline_label)+'</div>'+
       '<div class="big" style="font-size:22px;margin-top:4px">'+esc2(g.title)+'</div></div>'+
       '<div style="display:flex;gap:8px">'+(live&&g.missed?'<button class="act" data-replan="'+g.id+'">Fit in '+g.missed+' missed</button>':'')+
       '<button data-drop="'+g.id+'" data-st="'+g.status+'" style="border-color:var(--warn);color:var(--warn)">'+(live?'Drop':'Remove')+'</button></div></div>'+
       '<div style="display:flex;align-items:center;gap:12px;margin:12px 0 4px"><div style="flex:1;height:10px;border-radius:6px;background:rgba(150,170,205,.12);overflow:hidden">'+
       '<div style="width:'+pct+'%;height:100%;background:linear-gradient(90deg,var(--c),var(--c2))"></div></div><b style="font:600 14px var(--mono)">'+pct+'%</b>'+
       '<small class="muted">'+g.done+'/'+g.total+' tasks</small></div>'+
       (live?H('TODAY')+(today?today.tasks.map(t=>row(g,t,true)).join(''):'<div class="muted">Nothing planned today.</div>'):'')+
       H('THE PLAN')+'<div class="pgrid">'+g.days.map(day=>'<div class="rt" style="'+(day.today?'border-color:var(--c)':'')+(day.past&&day.tasks.every(t=>t.done)?';opacity:.55':'')+'">'+
         '<small class="muted">'+esc2(day.label).toUpperCase()+'</small>'+day.tasks.map(t=>row(g,t,false)).join('')+'</div>').join('')+'</div>'+
       '<small class="muted" data-said></small></div>'});
   $('pgBody').innerHTML=html;
   $('pgBody').querySelectorAll('input[data-t]').forEach(c=>c.onchange=async()=>{c.disabled=true;
     await act('goals',{goal:c.dataset.g,task:c.dataset.t,action:'toggle',on:c.checked});renderPage(true)});
   // Start: a new chat in the goal's project, asking for the lesson / problems / notes / quiz;
   // the task ticks itself off when the answer arrives.
   $('pgBody').querySelectorAll('[data-start]').forEach(b=>b.onclick=async e=>{e.preventDefault();e.stopPropagation();b.disabled=true;
     try{const j=await api('/view/goals',{method:'POST',body:JSON.stringify({goal:b.dataset.goal,task:b.dataset.start,action:'start'})});
       if(!j.prompt){b.disabled=false;return}
       await openChat(j.project?'new?project='+encodeURIComponent(j.project):'new');ask(j.prompt)}
     catch(err){b.disabled=false}});
   $('pgBody').querySelectorAll('[data-replan]').forEach(b=>b.onclick=async()=>{b.disabled=true;await act('goals',{goal:b.dataset.replan,action:'replan'});renderPage(true)});
   $('pgBody').querySelectorAll('[data-drop]').forEach(b=>b.onclick=async()=>{
     if(!b.dataset.sure){b.dataset.sure='1';b.textContent='Tap again';setTimeout(()=>{if(b.isConnected){delete b.dataset.sure;b.textContent=b.dataset.st==='active'?'Drop':'Remove'}},4000);return}
     b.disabled=true;await act('goals',{goal:b.dataset.drop,action:b.dataset.st==='active'?'drop':'remove'});renderPage(true)})},
 // The live brain over the Missions page (neuron-brain.js, built by a Claude mission on 17 Oct 2026):
 // a region fires while its worker has a job; the cards are the real missions.
 _brain(d){
   if(typeof NeuronBrain!=='function')return null;
   if(!PAGES._brainEl){const el=document.createElement('div');el.id='brainBoard';
     
     PAGES._brainEl=el;PAGES._brainObj=new NeuronBrain(el,{regions:[
       {id:'claude',label:'Builder',color:'#a78bfa'},{id:'gemini',label:'Research',color:'#22d3ee'},
       {id:'argon',label:'Deep work',color:'#f59e0b'},{id:'maps',label:'Places',color:'#4ade80'},
       {id:'voice',label:'Voice',color:'#f472b6'},{id:'memory',label:'Memory',color:'#60a5fa'},
       {id:'study',label:'Study',color:'#fb923c'},{id:'schedule',label:'Schedules',color:'#2dd4bf'}]})}
   const live=s=>s==='running'||s==='starting',busy={};
   d.missions.forEach(m=>{const ws=m.worker==='chain'?(m.steps||[]).filter(s=>live(s.status)).map(s=>s.worker):live(m.status)?[m.worker]:[];
     ws.forEach(w=>{(busy[w]=busy[w]||[]).push(m)})});
   const regions={};
   ['claude','gemini','argon','maps'].forEach(w=>{const n=(busy[w]||[]).length;
     regions[w]={active:n>0,firing:n?Math.min(1,.35+.25*n):0,sub:n?(n>1?n+' jobs':(busy[w][0].progress.slice(-1)[0]||{}).text||'working').slice(0,34):'idle'}});
   const sch=(d.scheduled||[]).length;
   regions.schedule={active:false,firing:0,sub:sch?sch+' scheduled':'none yet'};
   ['voice','memory','study'].forEach(w=>regions[w]={active:false,firing:0,sub:'idle'});
   const order=[...d.missions].sort((a,b)=>live(b.status)-live(a.status)).slice(0,6);
   const cards=order.map(m=>({title:m.title,worker:m.worker==='chain'?'Planned · '+(m.steps||[]).length+' steps':({claude:'Claude',gemini:'Gemini',argon:'Gemini Argon',maps:'Google Maps'}[m.worker]||m.worker),
     status:live(m.status)?'running':m.status==='done'?'done':'failed',note:live(m.status)?((m.progress.slice(-1)[0]||{}).text||'getting started'):m.status==='stopped'?'stopped':(m.minutes+' min'+(m.cost?' · $'+Number(m.cost).toFixed(2):''))}));
   try{PAGES._brainObj.update({regions,cards})}catch(e){}
   return PAGES._brainEl},
 missions(d,quiet){
   const run=d.missions.filter(m=>m.status==='running'||m.status==='starting'),need=(d.needs||[]).length,nb=$('nMissions');
   nb.textContent=need?'!'+need:(run.length||'-');nb.classList.toggle('alert',!!need);
   const sig=JSON.stringify(d);if(quiet&&(sig===PAGES._mSig||document.activeElement&&document.activeElement.closest&&document.activeElement.closest('#pgBody')||[...document.querySelectorAll('#pgBody video')].some(v=>!v.paused))){PAGES._brain(d);return}
   PAGES._mSig=sig;const open=PAGES._mOpen||{};
   const link=m=>'/mission/'+m.id+'/'+encodeURIComponent(token())+'/'+(m.preview||'').split('/').map(encodeURIComponent).join('/');
   const col={running:'var(--c)',starting:'var(--c)',done:'var(--ok,#4fd18b)',failed:'var(--warn)',stopped:'var(--warn)'};
   let html='<div class="pbox" style="margin-bottom:16px"><div class="muted" style="margin-bottom:8px">Give Athena a job - she plans the steps and picks Gemini to research and Claude to build - or pick one yourself. It works in its own folder, can’t send, post or buy anything, and Athena tells you when it’s done.'+
     ' Claude missions this month: $'+(d.spent||0).toFixed(2)+' of $'+(d.cap||15).toFixed(0)+' (max $'+(d.per_mission||1.5).toFixed(2)+' each); Gemini is free.'+'</div>'+
     '<textarea id="mTask" class="minp" rows="2" placeholder="e.g. Build a one-page website for my desk orb project, dark theme, with a parts list" style="width:100%;box-sizing:border-box;resize:vertical"></textarea>'+
     '<div style="display:flex;gap:10px;align-items:center;margin-top:8px;flex-wrap:wrap"><select id="mWho" class="minp" style="padding:6px 8px">'+
       '<option value="auto">Auto - plan the steps</option>'+
       '<option value="claude"'+(PAGES._mWho==='claude'?' selected':'')+'>Claude</option><option value="gemini"'+(PAGES._mWho==='gemini'?' selected':'')+'>Gemini Flash (background)</option>'+
       '<option value="argon"'+(PAGES._mWho==='argon'?' selected':'')+'>Gemini Argon (in the Gemini app)</option></select>'+
       '<button class="mbtn go" id="mGo">Start mission</button><small class="muted" id="mSaid"></small></div></div>';
   const sch=d.scheduled||[];
   html+='<div class="pbox" style="margin-bottom:16px"><div style="display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap">'+
     '<b style="font:600 12px var(--mono);letter-spacing:2px;color:var(--c)">SCHEDULED</b><small class="muted">runs on its own - while you sleep too; results land here and on your phone</small></div>'+
     (sch.length?sch.map(s=>'<div style="display:flex;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line2)">'+
       '<span style="flex:none;font:600 12px var(--mono);color:var(--c)">'+esc2(s.when)+'</span><span style="flex:1;min-width:0">'+esc2(s.what)+'</span>'+
       (s.last?'<small class="muted" style="flex:none">last '+new Date(s.last*1000).toLocaleDateString()+'</small>':'')+
       '<button class="mbtn" data-unsch="'+s.id+'" title="Stop running it">\u2715</button></div>').join(''):
       '<div class="muted" style="padding:6px 0">None yet. Say \u201cevery Monday at 7am, start a mission to scan new AI tools and write me a rollup\u201d, or add one here.</div>')+
     '<div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap"><input id="schWhen" class="minp" placeholder="every Monday at 7am" style="flex:0 1 190px;min-width:0">'+
     '<input id="schWhat" class="minp" placeholder="what to do, e.g. read my LMS files and update my study plan" style="flex:1 1 260px;min-width:0">'+
     '<button class="mbtn go" id="schAdd">Schedule</button></div></div>';
   html+=loopsBox(d);
   html+=deckBox(d);
   html+=usageBox(d);
   if(!d.missions.length)html+='<div class="muted">No missions yet. You can also say: “Athena, have Claude build me a one-page site for my orb”.</div>';
   d.missions.forEach(m=>{const live=m.status==='running'||m.status==='starting';
     html+='<div class="pbox" style="margin-bottom:14px">'+
       '<div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap"><div style="min-width:0">'+
       '<div class="muted"><b style="color:'+(col[m.status]||'inherit')+'">'+esc2(m.status.toUpperCase())+'</b> · '+m.minutes+' min'+' · '+(m.worker==='chain'?(m.steps.length?m.steps.length+' step'+(m.steps.length>1?'s':''):'planning'):({gemini:'Gemini',argon:'Gemini Argon'}[m.worker]||'Claude'))+(m.cost?' · $'+Number(m.cost).toFixed(2):'')+(m.turns?' · '+m.turns+' steps':'')+'</div>'+
       '<div class="big" style="font-size:20px;margin-top:4px;overflow-wrap:anywhere">'+esc2(m.title)+'</div></div>'+
       '<div style="display:flex;gap:8px;flex-wrap:wrap">'+(live?'<button class="mbtn stop" data-mstop="'+m.id+'">Stop</button>':'')+
       (m.preview?(/\.mp4$/i.test(m.preview)?'':'<button class="mbtn" data-mprev="'+m.id+'">'+(open[m.id]?'Hide':/\.md$/i.test(m.preview)?'Read':'Preview')+'</button>')+'<a class="mbtn" href="'+link(m)+'" target="_blank" rel="noopener">Open ↗</a>':'')+
       (!live&&m.can_publish?'<button class="mbtn" data-mpub="'+m.id+'" title="Puts the site on the public internet">'+(m.published?'Update live site':'Publish')+'</button>':'')+
       (!live&&m.has_draft?'<button class="mbtn" data-mdraft="'+m.id+'" title="Opens it in your mail app - you send it">Open draft</button>':'')+
       '<button class="mbtn mfold" data-mfold="'+m.id+'">Folder</button></div></div>'+
       (m.published?'<div style="margin-top:8px;font-size:13px">\u{1F310} Live: <a href="'+esc2(m.published)+'" target="_blank" rel="noopener">'+esc2(m.published)+'</a></div>':'')+
       (m.steps&&m.steps.length?'<ol style="margin:10px 0 0;padding-left:20px;font-size:13px;line-height:1.55">'+m.steps.map(s=>
         '<li><b style="color:'+(col[s.status]||'var(--dim)')+'">'+({claude:'Claude',gemini:'Gemini',argon:'Argon'}[s.worker]||s.worker)+'</b> '+
         '<span class="muted">'+esc2(s.status||'waiting')+'</span> - '+esc2(String(s.task||'').slice(0,140))+'</li>').join('')+'</ol>':'')+
       (live||!m.result?'<div style="margin-top:10px;font:12px/1.6 var(--mono)">'+(m.progress.length?m.progress.map(p=>'<div class="muted">› '+esc2(p.text)+'</div>').join(''):'<div class="muted">› getting started…</div>')+'</div>':
         '<div style="margin-top:10px;white-space:pre-wrap">'+esc2(m.result)+'</div>')+
       (m.followups&&m.followups.length?'<small class="muted" style="display:block;margin-top:6px">Changes asked: '+m.followups.map(esc2).join(' · ')+'</small>':'')+
       (open[m.id]&&/\.md$/i.test(m.preview||'')?'<div class="mdview" data-mmd="'+link(m)+'" style="margin-top:12px;max-height:60vh;overflow:auto;padding:12px 14px;border:1px solid var(--line);border-radius:10px;white-space:pre-wrap;line-height:1.5">Loading…</div>':'')+
       (/\.mp4$/i.test(m.preview||'')?'<video src="'+link(m)+'" controls playsinline preload="metadata" style="width:100%;max-height:70vh;margin-top:12px;border-radius:10px;background:#000"></video>':'')+
       (open[m.id]&&m.preview&&!/\.(md|mp4)$/i.test(m.preview)?'<iframe sandbox="allow-scripts allow-forms allow-popups allow-modals" src="'+link(m)+'" style="width:100%;height:460px;border:1px solid rgba(150,170,205,.25);border-radius:10px;margin-top:12px;background:#fff"></iframe>':'')+
       (!live&&m.status!=='stopped'?'<div style="display:flex;gap:8px;margin-top:10px"><input class="minp" data-mtext="'+m.id+'" placeholder="Ask for a change… e.g. make the header darker" style="flex:1;min-width:0"><button class="mbtn go" data-mfollow="'+m.id+'">Send</button></div>':'')+
       '</div>'});
   $('pgBody').innerHTML=html;
   const brain=PAGES._brain(d);if(brain)$('pgBody').prepend(brain);
   const needs=needsPanel(d);if(needs)$('pgBody').prepend(needs);        // above everything: only you can unblock these
   const say=t=>{const e=$('mSaid');if(e)e.textContent=t};
   wireLoops(say);wireUsage(d);wireDeck(say);
   $('pgBody').querySelectorAll('[data-mmd]').forEach(async d=>{try{d.innerHTML=md(await (await fetch(d.dataset.mmd)).text());decorate(d)}catch(e){d.textContent='Could not load it.'}});
   $('mGo').onclick=async()=>{const t=$('mTask').value.trim();if(!t){say('Type what Claude should do.');return}
     $('mGo').disabled=true;PAGES._mWho=$('mWho').value;say(await act('missions',{action:'start',task:t,worker:$('mWho').value}));$('mGo').disabled=false;PAGES._mSig='';document.activeElement.blur();renderPage(true)};
   $('schAdd').onclick=async()=>{const w=$('schWhen').value.trim(),t=$('schWhat').value.trim();if(!w||!t){say('Say when, and what to do.');return}
     say(await act('missions',{action:'schedule',when:w,task:t}));PAGES._mSig='';document.activeElement.blur();renderPage(true)};
   $('pgBody').querySelectorAll('[data-unsch]').forEach(b=>b.onclick=async()=>{
     if(!b.dataset.sure){b.dataset.sure='1';b.textContent='Remove?';setTimeout(()=>{if(b.isConnected){delete b.dataset.sure;b.textContent='\u2715'}},4000);return}
     await act('missions',{action:'unschedule',id:b.dataset.unsch});PAGES._mSig='';renderPage(true)});
   $('pgBody').querySelectorAll('[data-mstop]').forEach(b=>b.onclick=async()=>{b.disabled=true;await act('missions',{action:'stop',id:b.dataset.mstop});renderPage(true)});
   $('pgBody').querySelectorAll('[data-mfold]').forEach(b=>b.onclick=()=>act('missions',{action:'folder',id:b.dataset.mfold}));
   // Publish is public: the first tap only explains, the second does it.
   $('pgBody').querySelectorAll('[data-mpub]').forEach(b=>b.onclick=async()=>{
     if(!b.dataset.sure){b.dataset.sure='1';b.textContent='Tap again - it goes public';
       setTimeout(()=>{if(b.isConnected){delete b.dataset.sure;b.textContent='Publish'}},5000);return}
     b.disabled=true;b.textContent='Publishing\u2026';const said=await act('missions',{action:'publish',id:b.dataset.mpub});
     PAGES._mSig='';renderPage(true);const e=$('mSaid');if(e)e.textContent=said});
   $('pgBody').querySelectorAll('[data-mdraft]').forEach(b=>b.onclick=async()=>{const said=await act('missions',{action:'draft',id:b.dataset.mdraft});
     const e=$('mSaid');if(e)e.textContent=said});
   $('pgBody').querySelectorAll('[data-mprev]').forEach(b=>b.onclick=()=>{open[b.dataset.mprev]=!open[b.dataset.mprev];PAGES._mOpen=open;PAGES._mSig='';renderPage(true)});
   $('pgBody').querySelectorAll('[data-mfollow]').forEach(b=>b.onclick=async()=>{const i=$('pgBody').querySelector('[data-mtext="'+b.dataset.mfollow+'"]');
     if(!i.value.trim())return;b.disabled=true;await act('missions',{action:'follow',id:b.dataset.mfollow,text:i.value.trim()});PAGES._mSig='';i.blur();renderPage(true)})},
 automations(d,quiet){
   $('nAuto').textContent=d.rules.length||'-';
   if(quiet&&document.activeElement&&document.activeElement.closest&&document.activeElement.closest('#pgBody'))return;  // not under your finger
   const pill=(on)=>'<span class="st '+(on?'ok':'warn')+'">'+(on?'ON':'OFF')+'</span>';
   let html='<div class="hero" style="grid-template-columns:1fr auto;align-items:center;padding:16px 20px"><div><div class="muted">ALL AUTOMATION</div>'+
     '<div class="big" style="color:'+(d.paused?'var(--warn)':'var(--ok)')+'">'+(d.paused?'Paused':'Running')+'</div>'+
     '<div class="muted" style="margin-top:4px">'+d.rules.length+' automation'+(d.rules.length===1?'':'s')+' · add one by voice: “Athena, every day at 11pm turn on focus mode” or “keep an eye on my LMS”</div></div>'+
     '<button class="act" id="autoAll" style="padding:12px 20px">'+(d.paused?'Resume all':'Pause all')+'</button></div>';
   html+='<h4 style="font:600 12px var(--mono);letter-spacing:2px;color:var(--c);margin:16px 0 10px">YOUR AUTOMATIONS</h4>';
   html+=d.rules.length?'<div class="pgrid">'+d.rules.map(r=>'<div class="rt" style="opacity:'+(r.on?1:.55)+'">'+
       '<div style="display:flex;justify-content:space-between;gap:8px;align-items:center"><small class="muted">'+(r.kind==='watch'?'WATCH':'RULE')+' · '+esc2(r.when).toUpperCase()+'</small>'+pill(r.on)+'</div>'+
       '<b style="display:block;margin:8px 0 6px">'+esc2(r.what)+'</b><small class="muted">Last run: '+esc2(r.last)+'</small>'+
       '<div style="display:flex;gap:8px;margin-top:10px"><button data-tog="'+r.id+'" data-on="'+(r.on?0:1)+'">'+(r.on?'Turn off':'Turn on')+'</button>'+
       '<button data-del="'+r.id+'" style="border-color:var(--warn);color:var(--warn)">Remove</button></div><small class="muted" data-said></small></div>').join('')+'</div>'
     :'<div class="muted">None yet. Say “Athena, keep an eye on my LMS”, or “every time I get home, play my playlist”.</div>';
   html+='<h4 style="font:600 12px var(--mono);letter-spacing:2px;color:var(--c);margin:20px 0 6px">WHAT ATHENA DID ON HER OWN</h4>'+(d.activity.length?
     '<table class="tbl"><thead><tr><th>WHEN</th><th>WHAT</th></tr></thead><tbody>'+d.activity.map(a=>'<tr><td style="white-space:nowrap">'+esc2(a.t)+'</td><td>'+esc2(a.what)+'</td></tr>').join('')+'</tbody></table>'
     :'<div class="muted">Nothing yet.</div>');
   $('pgBody').innerHTML=html;
   $('autoAll').onclick=async()=>{await act('automation',{pause:!d.paused});renderPage(true)};
   $('pgBody').querySelectorAll('[data-tog]').forEach(b=>b.onclick=async()=>{b.disabled=true;await act('rules',{id:b.dataset.tog,action:'toggle',on:b.dataset.on==='1'});renderPage(true)});
   // Remove asks once, on the button itself: the page cannot show a dialog.
   $('pgBody').querySelectorAll('[data-del]').forEach(b=>b.onclick=async()=>{
     if(!b.dataset.sure){b.dataset.sure='1';b.textContent='Tap again to remove';setTimeout(()=>{if(b.isConnected){delete b.dataset.sure;b.textContent='Remove'}},4000);return}
     b.disabled=true;await act('rules',{id:b.dataset.del,action:'remove'});renderPage(true)})},
 store(d){if(d.error){$('pgBody').innerHTML='<div class="muted">'+esc2(d.error)+'</div>';return}
   const rs=n=>'₹'+Number(n||0).toLocaleString('en-IN'),tile=(t,big,sub)=>'<div class="pbox"><h4>'+t+'</h4><div class="big" style="font-size:28px">'+big+'</div><div class="muted">'+sub+'</div></div>';
   $('nShip').textContent=d.toShip.length||'-';
   let html='<div class="pgrid">'+tile('TODAY',rs(d.today.revenue),d.today.orders+' orders')+tile('LAST 7 DAYS',rs(d.week.revenue),d.week.orders+' orders')+
     tile('LAST 30 DAYS',rs(d.month.revenue),d.month.orders+' orders · '+rs(d.collected30d)+' received')+
     tile('TO COLLECT (COD)',rs(d.codToCollect.amount),d.codToCollect.orders+' orders out · '+d.inTransit+' in transit')+'</div>';
   if(d.needsReview.length||d.refundsDue)html+='<div class="pbox" style="margin-top:12px;border-color:var(--warn)"><h4 style="color:var(--warn)">NEEDS YOU</h4><ul>'+
     d.needsReview.map(r=>'<li><span><b>'+esc2(r.order)+'</b> '+esc2(r.reason)+'</span></li>').join('')+
     (d.refundsDue?'<li><span>'+d.refundsDue+' cancelled order(s) paid online: refund in Razorpay, then mark refunded in the admin panel.</span></li>':'')+'</ul></div>';
   html+='<h4 style="font:600 12px var(--mono);letter-spacing:2px;color:var(--c);margin:18px 0 6px">TO SHIP · OLDEST FIRST</h4>'+(d.toShip.length?
     '<table class="tbl"><thead><tr><th>ORDER</th><th>CUSTOMER</th><th class="hide-sm">PLACED</th><th>TOTAL</th></tr></thead><tbody>'+d.toShip.map(o=>
     '<tr><td><b>'+esc2(o.order)+'</b></td><td>'+esc2(o.shipName)+' <small class="muted">'+esc2(o.shipCity)+'</small></td><td class="hide-sm">'+esc2(new Date(o.createdAt).toLocaleString('en-IN',{dateStyle:'medium',timeStyle:'short'}))+
     '</td><td>'+rs(o.total)+' <small class="muted">'+(o.paymentMethod==='COD'?'COD':'paid')+'</small></td></tr>').join('')+'</tbody></table>':'<div class="muted">Nothing waiting to ship.</div>');
   html+='<h4 style="font:600 12px var(--mono);letter-spacing:2px;color:var(--c);margin:18px 0 6px">LOW STOCK (3 OR FEWER)</h4>'+(d.lowStock.length?
     '<table class="tbl"><tbody>'+d.lowStock.map(v=>'<tr><td>'+esc2(v.product)+' <small class="muted">'+esc2(v.size)+'</small></td><td><span class="st '+(v.stock?'warn':'bad')+'">'+(v.stock?v.stock+' LEFT':'SOLD OUT')+'</span></td></tr>').join('')+'</tbody></table>':'<div class="muted">All stocked.</div>');
   html+='<div class="muted" style="margin-top:16px">'+d.newCustomers7d+' new customer(s) this week · Ask: <i>"ship RGZ1004 with Delhivery DLV123"</i>, <i>"set Noor Anarkali stock to 10"</i></div>';
   const y=$('pgBody').scrollTop;$('pgBody').innerHTML=html;$('pgBody').scrollTop=y},
 async brain(d,quiet){if(quiet)return;
   const tab=(k,t)=>'<button data-bt="'+k+'" style="font:600 12px var(--disp);letter-spacing:.12em;padding:6px 16px;border-radius:999px;cursor:pointer;'+
     'background:'+(brainTab===k?'rgba(196,168,255,.14)':'transparent')+';border:1px solid '+(brainTab===k?'var(--c)':'var(--line)')+';color:'+(brainTab===k?'var(--c)':'var(--dim)')+'">'+t+'</button>';
   $('pgBody').innerHTML='<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">'+tab('mind','KNOWLEDGE')+(d.code_map?tab('code','CODE'):'')+
     '<span class="muted" id="brainInfo"></span></div>'+
     '<div id="brainNet" style="height:calc(100vh - 310px);min-height:380px;border:1px solid var(--line);border-radius:10px;background:radial-gradient(circle at 50% 50%,#16131f,#030b12)"></div>';
   document.querySelectorAll('[data-bt]').forEach(b=>b.onclick=()=>{brainTab=b.dataset.bt;PAGES.brain(d)});
   brainShow(d)},
 calendar(d){if(d.error){$('pgBody').innerHTML='<div class="muted">'+esc2(d.error)+'</div>';return}
   if(!d.rows.length){$('pgBody').innerHTML='<div class="muted">Nothing in the next 7 days.</div>';return}
   let html='<table class="tbl"><thead><tr><th>DAY</th><th>TIME</th><th>EVENT</th><th class="hide-sm">WHERE</th><th class="hide-sm">SOURCE</th><th>STATUS</th></tr></thead><tbody>';let last='';
   d.rows.forEach(r=>{if(r.date!==last){html+='<tr class="grp"><td colspan="6">'+esc2(r.day.toUpperCase())+' · '+esc2(r.date)+'</td></tr>';last=r.date}
     html+='<tr class="'+r.state+'"><td>'+esc2(r.day)+'</td><td>'+esc2(r.time)+'</td><td><b>'+esc2(r.what)+'</b></td><td class="hide-sm">'+esc2(r.where||'—')+'</td><td class="hide-sm">'+esc2(r.source)+'</td><td><span class="st '+r.state+'">'+(r.state==='now'?'NOW':r.state==='done'?'DONE':'UPCOMING')+'</span></td></tr>'});
   $('pgBody').innerHTML=html+'</tbody></table>'+(d.google?'':'<p class="muted">Google Calendar is not connected - showing the timetable only.</p>')},
 memory(d){let html='<div class="pgrid">';
   d.sections.forEach(s=>{html+='<div class="pbox"><h4>'+esc2(s.title.toUpperCase())+'</h4>'+(s.items.length?'<ul>'+s.items.map(i=>
     '<li><span>'+esc2(i.text)+'</span><small>'+esc2(i.added)+(i.until?' → '+esc2(i.until):'')+'</small><button data-forget="'+esc2(i.text)+'">Forget</button></li>').join('')+'</ul>':'<div class="muted">Nothing yet.</div>')+'</div>'});
   html+='<div class="pbox"><h4>SAVED FACTS</h4>'+(d.facts.length?'<ul>'+d.facts.map(f=>'<li><span><b>'+esc2(f.key)+'</b>: '+esc2(f.value)+'</span></li>').join('')+'</ul>':'<div class="muted">None.</div>')+'</div>';
   html+='<div class="pbox"><h4>HISTORY</h4><ul><li><span>Conversations saved</span><b>'+d.chats+'</b></li><li><span>Searchable snippets</span><b>'+d.snippets+'</b></li></ul><p class="muted">Say "remember that …" to add, or edit '+esc2(d.file)+'.</p></div></div>';
   $('pgBody').innerHTML=html;
   $('pgBody').querySelectorAll('[data-forget]').forEach(b=>b.onclick=async()=>{if(b.dataset.sure!=='1'){b.dataset.sure='1';b.textContent='Sure?';return}
     await act('forget',{text:b.dataset.forget});renderPage()})},
 workflows(d,quiet){const m=d.music;
   const hero='<div class="hero"><div class="art" id="art">'+(m&&m.art?'':'<div class="vinyl'+(m&&m.playing?' spin2':'')+'"></div>')+'</div><div style="min-width:0">'+
     '<div class="muted">'+(m?(m.playing?'NOW PLAYING':'PAUSED')+(m.app?' · '+esc2(/spotify/i.test(m.app)?'Spotify':m.app.split('!')[0].split('\\').pop()):''):(d.spotify_running?'SPOTIFY IS OPEN':'NOTHING PLAYING'))+'</div>'+
     '<h2>'+esc2(m?(m.title||'Unknown track'):'Nothing playing')+'</h2><p>'+esc2(m?[m.artist,m.album].filter(Boolean).join(' · '):'Start something, or run a routine below')+'</p>'+
     (m&&m.length?'<div class="prog"><i style="width:'+Math.min(100,100*m.position/m.length)+'%"></i></div><div class="times"><span>'+fmt(m.position)+'</span><span>'+fmt(m.length)+'</span></div>':'')+
     '<div class="ctl"><button data-m="previous">⏮</button><button class="main" data-m="'+(m&&m.playing?'pause':'play')+'">'+(m&&m.playing?'⏸':'▶')+'</button><button data-m="next">⏭</button>'+
     (d.spotify_running?'':'<button class="act" data-m="open">Open Spotify</button>')+'</div></div></div>';
   if(quiet&&$('mhero')){$('mhero').innerHTML=hero}else{
     $('pgBody').innerHTML='<div id="mhero">'+hero+'</div><h4 style="font:600 12px var(--mono);letter-spacing:2px;color:var(--c);margin:4px 0 10px">ROUTINES</h4><div class="pgrid">'+
       d.routines.map(r=>'<div class="rt"><b>'+esc2(r.name)+'</b><ol>'+r.steps.map(s=>'<li>'+esc2(s)+'</li>').join('')+'</ol><button data-run="'+esc2(r.name)+'">▶ Run</button><small class="muted" data-said></small></div>').join('')+'</div>';
     $('pgBody').querySelectorAll('[data-run]').forEach(b=>b.onclick=async()=>{b.disabled=true;b.textContent='Running…';
       const s=await act('routine',{name:b.dataset.run});b.nextElementSibling.textContent=s.split('\n')[0];b.textContent='▶ Run';b.disabled=false;renderPage(true)})}
   if(m&&m.art)$('art').style.backgroundImage='url('+m.art+')';
   $('pgBody').querySelectorAll('[data-m]').forEach(b=>b.onclick=async()=>{await act('music',{action:b.dataset.m});setTimeout(()=>renderPage(true),700)})},
 system(d,quiet){let html='<div class="hero" style="grid-template-columns:1fr;padding:14px 18px"><div><div class="muted">OVERALL HEALTH</div><div class="big" style="color:'+(d.health==='Healthy'?'var(--ok)':'var(--warn)')+'">'+esc2(d.health)+'</div></div></div>';
   html+='<table class="tbl"><thead><tr><th>COMPONENT</th><th>READING</th><th class="hide-sm">LOAD</th><th>STATUS</th></tr></thead><tbody>'+d.rows.map(r=>
     '<tr><td><b>'+esc2(r.name)+'</b>'+(r.note?' <small class="muted">'+esc2(r.note)+'</small>':'')+'</td><td>'+esc2(r.value)+'</td><td class="hide-sm">'+(r.pct==null?'':'<div class="meter'+(r.state==='warn'?' warn':'')+'"><i style="width:'+Math.min(100,r.pct)+'%"></i></div>')+'</td><td><span class="st '+r.state+'">'+(r.state==='ok'?'OK':'CHECK')+'</span></td></tr>').join('')+'</tbody></table>';
   html+='<h4 style="font:600 12px var(--mono);letter-spacing:2px;color:var(--c);margin:18px 0 6px">BUSIEST APPS</h4><table class="tbl"><thead><tr><th>APP</th><th>MEMORY</th></tr></thead><tbody>'+
     d.apps.map(a=>'<tr><td>'+esc2(a.name)+'</td><td>'+(a.mb>=1024?(a.mb/1024).toFixed(1)+' GB':a.mb+' MB')+'</td></tr>').join('')+'</tbody></table>';
   const y=$('pgBody').scrollTop;$('pgBody').innerHTML=html;$('pgBody').scrollTop=y},
 tasks(d){const li=(t,btns)=>'<li><span>'+esc2(t.text)+(t.due?' <small>'+esc2(t.due)+'</small>':'')+'</span>'+btns+'</li>';
   const openBtns=t=>'<button data-done="'+t.id+'">✓ Done</button><button data-del="'+t.id+'">✕</button>';
   let html='<form class="addrow" id="addTask" autocomplete="off"><input id="taskText" placeholder="Add a task… e.g. finish DSD lab record"><select id="taskDue"><option value="today">Today</option><option value="tomorrow">Tomorrow</option><option value="">No date</option></select><button>Add</button></form><div class="pgrid">';
   html+='<div class="pbox"><h4>TO DO TODAY</h4>'+(d.today.length?'<ul>'+d.today.map(t=>li(t,openBtns(t))).join('')+'</ul>':'<div class="muted">Nothing due today.</div>')+'</div>';
   html+='<div class="pbox"><h4>REMAINING TODAY</h4><ul>'+(d.classes.map(c=>'<li><span>🎓 '+esc2(c.what)+(c.where?' <small>'+esc2(c.where)+'</small>':'')+'</span><small>'+esc2(c.at)+'</small></li>').join('')+
     d.reminders_today.map(r=>'<li><span>⏰ '+esc2(r.text)+'</span><small>'+esc2(r.at)+'</small></li>').join('')||'<li><span class="muted">No more classes or reminders today.</span></li>')+'</ul></div>';
   html+='<div class="pbox"><h4>REMAINING TASKS</h4>'+(d.remaining.length?'<ul>'+d.remaining.map(t=>li(t,openBtns(t))).join('')+'</ul>':'<div class="muted">No other open tasks.</div>')+'</div>';
   html+='<div class="pbox"><h4>UPCOMING REMINDERS</h4>'+(d.reminders_later.length?'<ul>'+d.reminders_later.map(r=>'<li><span>⏰ '+esc2(r.text)+'</span><small>'+esc2(r.date)+' '+esc2(r.at)+'</small></li>').join('')+'</ul>':'<div class="muted">None.</div>')+'</div>';
   if(d.done_today.length)html+='<div class="pbox"><h4>DONE TODAY</h4><ul>'+d.done_today.map(t=>'<li><span style="text-decoration:line-through;color:var(--dim)">'+esc2(t.text)+'</span></li>').join('')+'</ul></div>';
   $('pgBody').innerHTML=html+'</div>';
   $('addTask').onsubmit=async e=>{e.preventDefault();const t=$('taskText').value.trim();if(!t)return;await act('tasks',{action:'add',text:t,due:$('taskDue').value});renderPage()};
   $('pgBody').querySelectorAll('[data-done]').forEach(b=>b.onclick=async()=>{await act('tasks',{action:'done',id:b.dataset.done});renderPage()});
   $('pgBody').querySelectorAll('[data-del]').forEach(b=>b.onclick=async()=>{await act('tasks',{action:'delete',id:b.dataset.del});renderPage()})},
 phone(d,quiet){
   const held=d.held?'<div class="held"><b>⏸ '+esc2(d.held.text)+'</b><button class="btn-call" id="hYes">'+(d.held.kind==='call'?'📞 Call it':'✓ Submit')+'</button><button class="act" id="hNo">Cancel</button></div>':'';
   if(quiet){const hb=$('heldBox');if(hb){hb.innerHTML=held;wireHeld()}return}
   let html='<div id="heldBox">'+held+'</div><div class="pgrid">';
   html+='<div class="pbox"><h4>CALL SOMEONE</h4><div class="clist">'+(d.contacts.length?d.contacts.map(c=>'<button data-callc="'+esc2(c)+'">📞 '+esc2(c)+'</button>').join(''):'<span class="muted">No contacts with numbers yet - say "add contact …".</span>')+'</div>'+
     '<div class="fgrid" style="margin-top:12px"><input class="full" id="dialNum" inputmode="tel" placeholder="Number or place, e.g. 0135 2760973 or Domino\'s"></div>'+
     '<div class="dial">'+['1','2','3','4','5','6','7','8','9','+','0','⌫'].map(k=>'<button data-k="'+k+'">'+k+'</button>').join('')+'</div>'+
     '<div class="callrow"><button class="btn-call" id="doCall">📞 Call</button><button class="btn-end" id="doEnd">⏹ Hang up</button></div><div class="said" id="callSaid"></div></div>';
   html+='<div class="pbox"><h4>LOOK UP A PLACE</h4><div class="fgrid"><input class="full" id="plName" placeholder="Place, e.g. Barbeque Nation Rajpur Road"><input class="full" id="plQ" placeholder="What to know, e.g. is it open now, phone number"></div>'+
     '<button class="gobtn" id="doInfo">🔎 Look it up</button><div class="said" id="infoSaid"></div><button class="act" id="infoCall" style="display:none;margin-top:8px">📞 Call this place</button></div>';
   const tmr=new Date(Date.now()+864e5).toISOString().slice(0,10);
   html+='<div class="pbox"><h4>BOOK A TABLE / APPOINTMENT</h4><div class="fgrid"><input class="full" id="bkPlace" placeholder="Restaurant, clinic, salon…">'+
     '<input id="bkDate" type="date" value="'+tmr+'"><input id="bkTime" type="time" value="20:00"><select id="bkPeople">'+[1,2,3,4,5,6,7,8,10,12].map(n=>'<option'+(n===2?' selected':'')+'>'+n+'</option>').join('')+'</select><input id="bkNotes" placeholder="Notes (optional)"></div>'+
     '<button class="gobtn" id="doBook">🍽 Find and fill the booking</button><div class="said" id="bookSaid"></div><p class="muted" style="margin:8px 0 0">Athena fills everything in its own browser window and stops before the final Book / Pay button - you confirm. Log in to Zomato / Swiggy there once if asked.</p></div></div>';
   $('pgBody').innerHTML=html;wireHeld();
   const num=$('dialNum');
   $('pgBody').querySelectorAll('[data-k]').forEach(b=>b.onclick=()=>{num.value=b.dataset.k==='⌫'?num.value.slice(0,-1):num.value+b.dataset.k});
   const call=async who=>{if(!who)return;$('callSaid').textContent='Calling '+who+'…';$('callSaid').textContent=await act('phone',{action:'call',who});renderPage(true)};
   $('pgBody').querySelectorAll('[data-callc]').forEach(b=>b.onclick=()=>call(b.dataset.callc+''));
   $('doCall').onclick=()=>call(num.value.trim());
   $('doEnd').onclick=async()=>{$('callSaid').textContent=await act('phone',{action:'hangup'})};
   $('doInfo').onclick=async()=>{const p=$('plName').value.trim();if(!p)return;$('doInfo').disabled=true;$('infoSaid').textContent='Looking up '+p+'…';
     $('infoSaid').textContent=await act('phone',{action:'info',place:p,question:$('plQ').value.trim()});$('doInfo').disabled=false;
     $('infoCall').style.display='';$('infoCall').onclick=()=>call(p)};
   $('doBook').onclick=async()=>{const p=$('bkPlace').value.trim();if(!p)return;const b=$('doBook');b.disabled=true;b.textContent='Working on it… (can take a minute)';
     const dt=new Date($('bkDate').value+'T'+$('bkTime').value);
     const when=dt.toLocaleDateString(undefined,{weekday:'long',day:'numeric',month:'long'})+' at '+dt.toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'});
     $('bookSaid').textContent='Searching for '+p+'…';
     $('bookSaid').textContent=await act('phone',{action:'book',place:p,when,people:$('bkPeople').value,notes:$('bkNotes').value.trim()});
     b.disabled=false;b.textContent='🍽 Find and fill the booking';renderPage(true)};
 },
 skills(d){const areas={};d.rows.forEach(r=>(areas[r.area]=areas[r.area]||[]).push(r));
   let html='<input class="filter" id="skillQ" placeholder="Search '+d.count+' skills…"><div class="pgrid" style="margin-bottom:14px">'+
     Object.entries(areas).map(([a,rs])=>'<div class="pbox" style="padding:10px 14px"><div class="muted">'+esc2(a)+'</div><div class="big">'+rs.length+'</div></div>').join('')+'</div>';
   html+='<table class="tbl" id="skillT"><thead><tr><th>SKILL</th><th class="hide-sm">AREA</th><th>WHAT IT DOES</th></tr></thead><tbody>'+
     d.rows.map(r=>'<tr><td><b style="text-transform:capitalize">'+esc2(r.name)+'</b></td><td class="hide-sm">'+esc2(r.area)+'</td><td>'+esc2(r.what)+'</td></tr>').join('')+'</tbody></table>';
   html+='<p class="muted">Also: '+d.routines.length+' routines ('+esc2(d.routines.join(', '))+')'+(d.planner?', and a multi-step planner on '+esc2(d.planner.split(':').pop()):'')+'.</p>';
   $('pgBody').innerHTML=html;
   $('skillQ').oninput=()=>{const q=$('skillQ').value.toLowerCase();$('skillT').querySelectorAll('tbody tr').forEach(tr=>tr.style.display=tr.textContent.toLowerCase().includes(q)?'':'none')}},
};
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>openPage(b.dataset.page,b));

function wireHeld(){const y=$('hYes'),n=$('hNo');
  if(y)y.onclick=async()=>{y.disabled=true;const s=await act('phone',{action:'confirm'});const c=$('callSaid')||$('bookSaid');if(c)c.textContent=s;renderPage(true)};
  if(n)n.onclick=async()=>{await act('phone',{action:'cancel'});renderPage(true)}}
// ---------- the mic: the same as saying "Athena" ----------
async function wake(){
  try{await api('/wake',{method:'POST',body:'{}'});['mic','bigMic','talk'].forEach(i=>$(i).classList.add('on'));
    $('talkSub').textContent='I am listening…';
    setTimeout(()=>{['mic','bigMic','talk'].forEach(i=>$(i).classList.remove('on'))},8000)}
  catch(e){answer('Voice is not running on the PC.',true)}}
$('startVoice').onclick=async()=>{$('startVoice').textContent='Starting…';
  try{const j=await api('/voice/start',{method:'POST',body:'{}'});$('vText').textContent=j.said}catch(e){}
  setTimeout(()=>{$('startVoice').textContent='⟳ Start voice'},20000)};
// ---------- phone voice: talk into the phone, hear Athena on the phone ----------
// Recorded here, heard and spoken by the PC's voice process (jarvis/phonevoice.py):
// the same Whisper and Kokoro voice as the desk, and audio never leaves the PC.
const PHONE=!/^(127\.0\.0\.1|localhost|\[::1\])$/.test(location.hostname);
let rec=null,sayAudio=null,sayGen=0,voiceReply=false;
const talkUI=(on,sub)=>{['mic','bigMic','talk'].forEach(i=>$(i)&&$(i).classList.toggle('on',on));
  $('talkSub').textContent=sub||'tap, or say "Athena"'};
function unlockAudio(){        // phones only play sound started from a tap: unlock one player now, reuse it later
  if(!sayAudio)sayAudio=new Audio();
  sayAudio.src='data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YQAAAAA=';
  sayAudio.play().catch(()=>{})}
function stopSaying(){sayGen++;if(sayAudio)try{sayAudio.pause()}catch(e){}}
async function speakOnPhone(text){
  const gen=++sayGen;if(!sayAudio)return;
  const plain=String(text||'').replace(/```[\s\S]*?```/g,' ').replace(/\[([^\]]+)\]\([^)]*\)/g,'$1')
    .replace(/https?:\/\/\S+/g,'').replace(/\\[()[\]]|\$\$?/g,'').replace(/[*_#`>|]/g,'');
  const parts=[];let cur='';
  for(const s of plain.match(/[^.!?\n]+[.!?]*/g)||[]){const t=s.trim();if(!t)continue;
    cur=cur?cur+' '+t:t;if(cur.length>140){parts.push(cur);cur=''}}
  if(cur)parts.push(cur);
  const get=t=>fetch('/phone/say',{method:'POST',headers:{'X-Token':token()},body:JSON.stringify({text:t})})
    .then(r=>r.ok?r.blob():null).catch(()=>null);
  let next=parts.length?get(parts[0]):null;
  for(let i=0;i<Math.min(parts.length,10);i++){
    const blob=await next;next=i+1<parts.length?get(parts[i+1]):null;   // fetch the next while this one plays
    if(gen!==sayGen)return;if(!blob)continue;
    const url=URL.createObjectURL(blob);sayAudio.src=url;
    await new Promise(res=>{sayAudio.onended=sayAudio.onerror=res;sayAudio.play().catch(res)});
    URL.revokeObjectURL(url);if(gen!==sayGen)return}}
async function phoneTalk(){
  stopSaying();
  if(rec){if(rec.state==='recording')rec.stop();return}          // second tap: that's all
  if(!navigator.mediaDevices||!window.MediaRecorder){
    answer('This browser can\'t use the microphone here - open Athena from the https:// link.',true);return}
  unlockAudio();
  let stream;
  try{stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}})}
  catch(e){answer('The microphone is blocked - allow it for Athena in the browser\'s site settings.',true);return}
  const chunks=[];rec=new MediaRecorder(stream);
  rec.ondataavailable=e=>{if(e.data&&e.data.size)chunks.push(e.data)};
  const ac=new (window.AudioContext||window.webkitAudioContext)(),an=ac.createAnalyser();
  an.fftSize=1024;ac.createMediaStreamSource(stream).connect(an);
  const buf=new Float32Array(an.fftSize),t0=performance.now();let heard=false,quiet=t0,floor=0.01;
  const tick=setInterval(()=>{if(!rec||rec.state!=='recording'){clearInterval(tick);return}
    an.getFloatTimeDomainData(buf);let e=0;for(const v of buf)e+=v*v;const rms=Math.sqrt(e/buf.length),now=performance.now();
    if(now-t0<300)floor=Math.max(floor,rms);                       // the room's own level, first 0.3 s
    if(rms>Math.max(0.02,floor*2.5)){heard=true;quiet=now}
    if((heard&&now-quiet>1300)||now-t0>15000||(!heard&&now-t0>7000))rec.stop()},100);
  rec.onstop=async()=>{clearInterval(tick);stream.getTracks().forEach(t=>t.stop());try{ac.close()}catch(e){}
    rec=null;
    if(!heard){talkUI(false);return}
    talkUI(false,'Hearing you…');
    const blob=new Blob(chunks,{type:(chunks[0]&&chunks[0].type)||'audio/webm'});
    try{const r=await fetch('/phone/hear',{method:'POST',headers:{'X-Token':token(),'Content-Type':blob.type},body:blob});
      const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||('HTTP '+r.status));
      const said=(j.text||'').trim();
      if(!said){answer('I didn\'t catch that - tap and try again.',true);return}
      voiceReply=true;ask(said)}
    catch(e){answer('Couldn\'t hear that: '+e.message,true)}
    finally{talkUI(false)}};
  rec.start();talkUI(true,'Listening… tap to stop')}
const talk=()=>PHONE?phoneTalk():wake();
$('mic').onclick=talk;$('bigMic').onclick=talk;$('talk').onclick=talk;

// ---------- the phone app's own notifications (jarvis/webpush.py), instead of ntfy ----------
const b64u=k=>{k=k.replace(/-/g,'+').replace(/_/g,'/');const r=atob(k+'='.repeat((4-k.length%4)%4));
  return Uint8Array.from(r,c=>c.charCodeAt(0))};
async function pushSub(ask){
  if(!PHONE||!('serviceWorker' in navigator)||!('PushManager' in window)||!window.Notification)return false;
  if(Notification.permission==='denied')return false;
  if(Notification.permission!=='granted'){if(!ask||await Notification.requestPermission()!=='granted')return false}
  const reg=await navigator.serviceWorker.ready;
  let sub=await reg.pushManager.getSubscription();
  if(!sub){const {key}=await api('/push/key',{method:'POST',body:'{}'});
    sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:b64u(key)})}
  await api('/push/subscribe',{method:'POST',body:JSON.stringify({sub:sub.toJSON(),label:navigator.userAgent.slice(0,80)})});
  return true}
(async()=>{try{
  if(await pushSub(false))return;                    // already on: just keep the PC's copy of it fresh
  if(!PHONE||!window.Notification||!('PushManager' in window)||Notification.permission==='denied')return;
  try{if(localStorage.getItem('pushBarClosed'))return}catch(e){}
  const bar=document.createElement('div');bar.id='pushBar';
  bar.innerHTML='\u{1F514} Get Athena\u2019s alerts here, in the app<button type="button">Turn on</button><span aria-label="Not now">\u2715</span>';
  document.body.appendChild(bar);
  bar.querySelector('span').onclick=()=>{bar.remove();try{localStorage.setItem('pushBarClosed','1')}catch(e){}};
  bar.querySelector('button').onclick=async()=>{
    try{if(await pushSub(true)){bar.textContent='Notifications are on - sending a test.';
        api('/push/test',{method:'POST',body:'{}'}).catch(()=>{});setTimeout(()=>bar.remove(),3500)}
      else bar.textContent='Not allowed - you can turn them on in the browser\u2019s site settings for Athena.'}
    catch(e){bar.textContent='Couldn\u2019t turn them on: '+e.message}}}
catch(e){}})();

// ---------- live: what Athena is doing this second ----------
const mkbars=(el,n)=>{for(let i=0;i<n;i++)el.appendChild(document.createElement('i'));return[...el.children]};
const bars=mkbars($('wave'),36), vbars=mkbars($('vwave'),28);
const stars=$('stars');for(let i=0;i<46;i++){const a=Math.random()*6.283,r=Math.sqrt(Math.random())*122;
  const c=document.createElementNS('http://www.w3.org/2000/svg','circle');
  c.setAttribute('cx',200+Math.cos(a)*r);c.setAttribute('cy',200+Math.sin(a)*r*.98);
  c.setAttribute('r',(Math.random()*1.4+.4).toFixed(2));c.setAttribute('opacity',(Math.random()*.7+.2).toFixed(2));stars.appendChild(c)}
let lastHeard='',lastReply='',live={};
const LABEL={idle:['AI CORE','v3.0 · STANDING BY'],listening:['LISTENING','SPEAK NOW'],
  thinking:['PROCESSING','WORKING ON IT…'],speaking:['RESPONDING',''],offline:['VOICE OFFLINE','TYPE BELOW'],
  muted:['MIC MUTED','']};
function kind(m){const s=m.toLowerCase();
  if(/error|failed|stalled|could not|lost|busy/.test(s))return['warn','WARN','!'];
  if(/interrupted|stopped/.test(s))return['warn','STOP','■'];
  if(/^tool: (permission|blocked):/.test(s))return['warn',s.includes('blocked')?'BLOCKED':'NEEDS OK','🔒'];
  if(s.startsWith('heard'))return['voice','VOICE','🎙'];
  if(s.startsWith('replied'))return['info','REPLY','✦'];
  if(s.startsWith('tool'))return['act','ACTION','⚙'];
  if(s.startsWith('memory'))return['tip','MEMORY','◆'];
  if(/phoned|push|alert/.test(s))return['live','ALERT','☎'];
  if(/woke|unlocked|lid/.test(s))return['tip','SYSTEM','⏻'];
  return['info','INFO','•']}
function feed(log){const f=$('feed');f.innerHTML='';
  const items=(log||[]).filter(([,m])=>!/^(listening|standing by|processing request|responding)$/.test(m)).slice(-7).reverse();
  if(!items.length){f.innerHTML='<div class="empty">Waiting for activity…</div>';return}
  items.forEach(([t,m])=>{const [k,tag,ic]=kind(m);const d=document.createElement('div');d.className='fi';
    const text=m.replace(/^(heard|replied|tool|memory):\s*/i,'');
    d.innerHTML='<i class="k-'+k+'">'+ic+'</i><div><b></b><small>'+t+'</small></div><span class="tag k-'+k+'">'+tag+'</span>';
    d.querySelector('b').textContent=text.charAt(0).toUpperCase()+text.slice(1);f.appendChild(d)})}
const AGENTS=[
  ['Voice Agent','voice',/./,'var(--c)','<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/>'],
  ['Planner Agent','planner',/planner|tool: /,'var(--vio)','<path d="M4 6h10M4 12h16M4 18h7"/><circle cx="18" cy="6" r="2"/>'],
  ['Browser Agent','browser',/tool: browser_/,'var(--org)','<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'],
  ['App Control Agent','apps',/tool: (app_|open_app|close_app|music|set_volume)/,'var(--c2)','<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8"/>'],
  ['Memory Agent','memory',/memory|tool: (search_past|what_you|remember|forget)/,'var(--ok)','<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/>'],
  ['Alerts Agent','alerts',/phoned|push|alert|reminder/,'var(--warn)','<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10 21h4"/>']];
function secsAgo(t){if(!t)return 1e9;const [h,m,s]=t.split(':').map(Number);const n=new Date();
  let d=(n.getHours()*3600+n.getMinutes()*60+n.getSeconds())-(h*3600+m*60+s);return d<0?d+86400:d}
function agents(){const box=$('agents');box.innerHTML='';let act=0;const log=live.log||[];
  AGENTS.forEach(([name,key,re,col,ic])=>{let on,label;
    if(key==='voice'){on=live.running&&live.state!=='idle';label=live.running?(live.state==='idle'?'Listening':live.state):'Offline'}
    else{on=log.some(([t,m])=>re.test(m.toLowerCase())&&secsAgo(t)<45);label=on?'Active':'Standby'}
    if(on)act++;const d=document.createElement('div');d.className='ag'+(on?' act':'');
    d.innerHTML='<i style="color:'+col+'"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">'+ic+'</svg></i><div style="min-width:0"><b>'+name+'</b><small>'+label+'</small></div>';
    box.appendChild(d)});
  $('agCount').textContent=act+' ACTIVE · '+AGENTS.length+' READY';$('oAgents').textContent=act?act+' running':AGENTS.length+' ready'}
// The particle face: amber listening, violet thinking, pink speaking, cyan idle.
// Without WebGL (or face.js) the SVG orb above stays, as before.
const FACE_COL={idle:'#c4a8ff',listening:'#f7a443',thinking:'#8f72ff',speaking:'#ff5fb8'};
// "Core": a fiery particle sphere (asked for 6 Oct 2026, after an "Ultron" demo).
// Same two calls as AthenaFace; picked with ?orb=core (the PC window passes hud.orb).
class CoreOrb{
  // Athena's colours (FACE_COL), a dense core of dots inside a lighter shell,
  // and tilted Dyson-swarm rings with dots travelling along them (6 Oct 2026).
  constructor(box){
    // Drawn on a full-window layer behind the page, centred on the orb's box,
    // so a big core (both hands apart) fills the screen instead of being cut
    // off at its box (6 Oct 2026). The box's own canvas stays as the anchor.
    this.box=box;box.style.visibility='hidden';
    const cv=document.createElement('canvas');cv.id='coreFull';
    cv.style.cssText='position:fixed;inset:0;width:100vw;height:100vh;pointer-events:none;z-index:0';
    document.body.prepend(cv);document.body.classList.add('corefull');this.cv=cv;this.x=cv.getContext('2d');this.lv=0;this.t=0;this.st='idle';this.spin=0;
    const sph=(n,r0)=>{const g=Math.PI*(3-Math.sqrt(5));return [...Array(n)].map((_,i)=>{const y=1-2*(i+.5)/n,r=Math.sqrt(1-y*y),s=r0?r0*(.35+.65*Math.cbrt(Math.random())):1;
      return{x:Math.cos(g*i)*r*s,y:y*s,z:Math.sin(g*i)*r*s,j:Math.random()}})};
    this.shell=sph(700,0);this.core=sph(1600,.55);
    this.rings=[[1.42,.30,.2],[1.62,.22,-.9],[1.85,.16,1.7],[1.28,.42,2.6]].map(([a,b,rot],i)=>({a,b,rot,dots:[...Array(26+i*8)].map(()=>Math.random()*6.283),sp:(i%2?-1:1)*(.25+i*.07)}));
    const loop=()=>{this.draw();requestAnimationFrame(loop)};requestAnimationFrame(loop)}
  setState(s){this.st=s}
  setLevel(v){this.lv=Math.max(this.lv*.85,Math.min(1,v*6))}
  setScale(v){this.want=Math.max(.4,Math.min(2.4,v||1))}
  rgb(){const h=(FACE_COL[this.st]||FACE_COL.idle).slice(1);return[0,2,4].map(i=>parseInt(h.substr(i,2),16))}
  draw(){const c=this.cv,x=this.x,d=devicePixelRatio,w=c.width=c.clientWidth*d,h=c.height=c.clientHeight*d;
    const bx=this.box.getBoundingClientRect();if(!w||!h||!bx.width)return;this.sc=(this.sc||1)+((this.want||1)-(this.sc||1))*.15;const cx=(bx.left+bx.width/2)*d,cy=(bx.top+bx.height/2)*d,R=Math.min(bx.width,bx.height)*d*(.24+this.lv*.04)*this.sc,[r,g,b]=this.rgb();
    this.t+=.016;this.spin+=.003+this.lv*.025+(this.st==='thinking'?.01:0);
    x.clearRect(0,0,w,h);x.globalCompositeOperation='lighter';
    const gk=1/Math.sqrt(this.sc),gl=x.createRadialGradient(cx,cy,0,cx,cy,R*(1.15+.55*gk));
    gl.addColorStop(0,`rgba(255,255,255,${(.8+this.lv*.2)*gk})`);gl.addColorStop(.12,`rgba(${r},${g},${b},${(.75+this.lv*.25)*gk})`);gl.addColorStop(.45,`rgba(${r},${g},${b},${.22*gk})`);gl.addColorStop(1,'rgba(0,0,0,0)');
    x.fillStyle=gl;x.beginPath();x.arc(cx,cy,R*(1.15+.55*gk),0,7);x.fill();
    const cs=Math.cos(this.spin),sn=Math.sin(this.spin),tl=.35,ct=Math.cos(tl),st=Math.sin(tl);
    const dots=(set,size,alpha,wob)=>{for(const q of set){let X=q.x*cs-q.z*sn,Z=q.x*sn+q.z*cs,Y=q.y*ct-Z*st;Z=q.y*st+Z*ct;
      const k=1+wob*Math.sin(this.t*3+q.j*9)*(.3+this.lv*2),s=(Z+2)/3;
      x.fillStyle=`rgba(${r},${g},${b},${alpha*(.35+.65*s)})`;const z=size*d*s*Math.sqrt(this.sc);x.fillRect(cx+X*R*k-z/2,cy+Y*R*k-z/2,z,z)}};
    dots(this.shell,1.9,.9,.05);dots(this.core,1.8,1,.03);
    for(const ring of this.rings){const rot=ring.rot+this.spin*.4,cr=Math.cos(rot),sr=Math.sin(rot),A=R*ring.a,B=R*ring.b;
      x.strokeStyle=`rgba(${r},${g},${b},${.5+this.lv*.4})`;x.lineWidth=1.1*d*Math.sqrt(this.sc);x.beginPath();x.ellipse(cx,cy,A,B,rot,0,7);x.stroke();
      for(let i=0;i<ring.dots.length;i++){const a=ring.dots[i]+=.004*ring.sp*(1+this.lv*3),ex=Math.cos(a)*A,ey=Math.sin(a)*B,front=Math.sin(a)>0;
        x.fillStyle=front?'rgba(255,255,255,.95)':`rgba(${r},${g},${b},.55)`;const z=(front?3.2:1.8)*d*Math.sqrt(this.sc);
        x.fillRect(cx+ex*cr-ey*sr-z/2,cy+ex*sr+ey*cr-z/2,z,z)}}
    x.globalCompositeOperation='source-over'}}
let face=null,caps=null;
try{face=(p.get('orb')||(()=>{try{return localStorage.getItem('athena_orb')}catch(e){return null}})())==='core'
      ?new CoreOrb($('faceOrb')):new AthenaFace($('faceOrb'));
  caps=new AthenaCaptions($('faceCaps'),{font:'500 17px "Segoe UI", system-ui, sans-serif'});
  $('orbwrap').classList.add('hasface')}catch(e){face=null;caps=null}
function faceSay(text,col,ms){if(!caps||!text)return;
  // long answers (a brief, a list) read better in the transcript; the face shows the opening
  const words=String(text).replace(/\s+/g,' ').trim().split(' ');
  caps.font=document.body.classList.contains('stage')?'500 24px Figtree, "Segoe UI", system-ui, sans-serif':'500 17px Figtree, "Segoe UI", system-ui, sans-serif';
  caps.setAccent(col);caps.say(words.slice(0,22).join(' ')+(words.length>22?' …':''),ms)}
async function pollLive(){
  try{const j=await api('/live');live=j;
    const st=j.state||'idle', lv=j.level||0;
    if(face&&face.setScale)face.setScale(j.core_scale||1);   // both hands apart/together (gestures.py)
    {const G={point:'☝ POINT',click:'🤏 CLICK',palm:'🖐 PALM',peace:'✌ LISTEN',resize:'🙌 RESIZE'},el=$('gestChip');
     // the gesture being made, or - while gesture mode runs - that it is on
     if(el){el.textContent=G[j.gesture]||(j.gestures_on?'✋ GESTURES ON':'');el.hidden=!(G[j.gesture]||j.gestures_on);
       el.style.opacity=G[j.gesture]?'1':'.6'}}
    let [a,b]=LABEL[j.running?st:'offline']||LABEL.idle;
    // the tool running right now, and the mode (modes.py) when it is not general
    const lastLog=(j.log||[]).slice(-1)[0];
    if(st==='thinking'&&lastLog&&/^tool: /.test(lastLog[1]))b=lastLog[1].replace(/^tool: /,'').replace(/_/g,' ').toUpperCase()+'…';
    if(j.mode&&j.mode!=='general'&&st==='idle')b=j.mode.toUpperCase()+' MODE · STANDING BY';
    $('c1').textContent=a;$('c2').textContent=b;
    const fast=st==='listening'||st==='thinking'||st==='speaking';
    $('spinA').classList.toggle('fast',fast);$('spinB').classList.toggle('fast',st==='thinking');
    $('pulse').setAttribute('r',128+Math.min(14,lv*18));
    const talk=st==='speaking'?0.55:lv;
    const shape=(el,i,n)=>{const w=Math.sin(Date.now()/140+i*0.7)*.5+.5;
      el.style.height=(3+Math.max(0,talk)*26*(0.35+w*.65)+(fast?Math.random()*5:0))+'px'};
    bars.forEach(shape);vbars.forEach(shape);
    const bad=!j.running||st==='muted';
    $('stat').textContent=!j.running?'VOICE OFFLINE':st==='muted'?'MIC MUTED':'OPTIMAL';
    $('stat').style.color=bad?'var(--warn)':'';$('sdot').className='dot'+(bad?' warn':'');
    $('vState').textContent=j.running?st.toUpperCase():'OFFLINE';
    $('vText').textContent=!j.running?'Voice is not running':st==='listening'?'Listening…':st==='thinking'?'Thinking…':st==='speaking'?'Speaking…':'Say "Athena" or tap';
    $('oVoice').textContent=j.running?'Online · '+st:(st==='stopped'?'Stopped':'Offline');$('oVoice').className=j.running?'':'w';
    $('startVoice').style.display=j.running?'none':'';
    $('hxOn').textContent=j.running?'● ONLINE':'● OFFLINE';$('hxOn').className=j.running?'ok':'bad';
    $('hxMic').textContent=!j.running?'VOICE OFF':st==='muted'?'MIC MUTED':'MIC ON';
    if(!$('talk').classList.contains('on'))$('talkSub').textContent=st==='listening'?'I am listening…':'tap, or say "Athena"';
    // Spoken conversation appears in the transcript too - the window is live,
    // not a chat box that only knows what was typed.
    if(face){const fs=j.running&&['listening','thinking','speaking'].includes(st)?st:'idle';
      face.setState(fs);face.setLevel(fs==='listening'?Math.min(1,lv*1.4):0);
      document.documentElement.style.setProperty('--facec',FACE_COL[fs]);
      const H={idle:'Say <em>Athena.</em>',listening:"I'm <em>listening.</em>",thinking:'Let me <em>think.</em>',speaking:'<em>Speaking.</em>'};
      $('dial').dataset.s=fs;$('dialLab').textContent={idle:'STANDING BY',listening:'LISTENING',thinking:'THINKING',speaking:'SPEAKING'}[fs];
      if($('stTitle').dataset.s!==fs){$('stTitle').innerHTML=H[fs];$('stTitle').dataset.s=fs;
        document.querySelectorAll('.stpills span').forEach(x=>x.classList.toggle('on',x.dataset.s===fs))}}
    if(j.heard&&j.heard!==lastHeard){line(j.heard,'me voice');lastHeard=j.heard;$('txYou').textContent=j.heard;faceSay(j.heard,FACE_COL.listening,170)}
    if(j.reply&&j.reply!==lastReply){if(/\[share:/.test(j.reply))loadShares();answer(j.reply);lastReply=j.reply;$('txAth').textContent=j.reply;faceSay(j.reply,FACE_COL.speaking,300)}
    feed(j.log);agents();jobChips(j.jobs||[]);
  }catch(e){$('stat').textContent='LINK LOST';$('sdot').className='dot warn'}
}
// Background jobs (web.py _jobs): a chip each - who, what, and the time ticking.
// A Deck console's opens it on the Deck; a mission's opens the Missions page.
function jobChips(js){const box=$('jobChips');if(!box)return;
  const sig=js.map(x=>(x.console||x.mission)+':'+x.state).join('|');
  if(box.dataset.sig!==sig){box.dataset.sig=sig;
    box.innerHTML=js.map(x=>'<button class="jobchip'+(x.state==='needs'?' needs':'')+'" data-jc="'+esc2(x.console||'')+'" data-since="'+(+x.since||0)+'" title="'+
      (x.console?'Show it on the Deck':'Open Missions')+'"><i></i><b>'+esc2(x.who)+'</b><span>'+esc2(x.title)+(x.state==='needs'?' · needs you':'')+'</span><em></em></button>').join('');
    box.querySelectorAll('.jobchip').forEach(b=>b.onclick=()=>b.dataset.jc?act('missions',{action:'deck_show',console:b.dataset.jc}):openPage('missions',null))}
  box.querySelectorAll('.jobchip').forEach(b=>{const s=Math.max(0,Math.floor(Date.now()/1000-(+b.dataset.since)));
    b.querySelector('em').textContent=s<60?s+'s':Math.floor(s/60)+':'+String(s%60).padStart(2,'0')})}
async function primeLive(){try{const j=await api('/live');lastHeard=j.heard||'';lastReply=j.reply||''}catch(e){}}

// ---------- system monitor ----------
let studyModel='',llmN=0;
const showLLM=()=>{$('oLLM').textContent=llmN+' connected'+(studyModel?' · class: '+studyModel:'')};
const RINGS=[['cpu','CPU'],['ram','RAM'],['gpu','GPU'],['disk','DISK']];
$('rings').innerHTML=RINGS.map(([k,n])=>'<div class="ring"><svg viewBox="0 0 100 100"><circle class="bg" cx="50" cy="50" r="40"/><circle class="fg" id="r_'+k+'" cx="50" cy="50" r="40" stroke-dasharray="251.3" stroke-dashoffset="251.3"/></svg><div><small>'+n+'</small><b id="v_'+k+'">--</b></div></div>').join('');
function ring(k,v){const el=$('r_'+k);if(v==null){$('v_'+k).textContent='--';el.style.strokeDashoffset=251.3;return}
  el.style.strokeDashoffset=251.3*(1-Math.min(100,v)/100);$('v_'+k).textContent=Math.round(v)+'%'}
async function pollStatus(){
  try{const s=await api('/status');
    ring('cpu',s.cpu);ring('ram',s.mem_pct);ring('disk',s.disk_pct);ring('gpu',s.gpu?s.gpu.util:null);
    $('mon').textContent=(s.gpu?'VRAM '+s.gpu.vram_used+'/'+s.gpu.vram_total+' GB · '+s.gpu.temp+'°C':'')+(s.battery==null?'':' · BAT '+s.battery+'%');
    const on=(s.model_on||'').toUpperCase();
    $('oCore').textContent=on?('Active · '+on):'Active';$('oCore').className=on==='CPU'?'w':'';
    $('oSys').textContent=s.cpu>90?'Under load':'Optimal';$('oSys').className=s.cpu>90?'w':'';
    studyModel=(s.study_model||'').split(/[:/]/).slice(-1)[0]||'';showLLM();
    $('user').textContent=s.user||'Operator';
    $('hxGpu').textContent=s.gpu?'GPU '+s.gpu.vram_used+' / '+s.gpu.vram_total+' GB':'GPU --';
    $('hxHi').textContent=greet()+(s.user?', '+String(s.user).split(' ')[0].toUpperCase():'');
    $('net').textContent='↓'+Math.round(s.down_kbs)+' ↑'+Math.round(s.up_kbs)+' KB/s';
  }catch(e){}
}

// ---------- overview: schedule, memory, providers, weather ----------
function constellation(n){const svg=$('constel');svg.innerHTML='';const pts=[];let seed=7;
  const rnd=()=>{seed=(seed*9301+49297)%233280;return seed/233280};
  for(let i=0;i<Math.max(6,Math.min(18,n));i++)pts.push([20+rnd()*260,15+rnd()*110]);
  let d='';pts.forEach((p,i)=>{if(i)d+=' L'+p[0].toFixed(0)+' '+p[1].toFixed(0);else d='M'+p[0].toFixed(0)+' '+p[1].toFixed(0)});
  svg.innerHTML='<path d="'+d+'" fill="none" stroke="rgba(196,168,255,.35)" stroke-width="1"/>'+
    pts.map(p=>'<circle cx="'+p[0].toFixed(0)+'" cy="'+p[1].toFixed(0)+'" r="2.6" fill="#e4dcff" style="filter:drop-shadow(0 0 4px #c4a8ff)"/>').join('')}
const PROV=[['groq','Groq','G','var(--org)'],['gemini','Gemini','✦','var(--c)'],['openrouter','OpenRouter','⇄','var(--vio)'],
  ['cerebras','Cerebras','C','var(--warn)'],['ollama','Ollama (local)','◉','var(--ok)'],['claude','Claude API','✺','var(--dim)']];
// ---------- phone: bottom tabs and the Today card ----------
function markTab(){const cur=pageName||lastView;document.querySelectorAll('footer .tab').forEach(x=>x.classList.toggle('on',x.dataset.tab===cur))}
document.querySelectorAll('footer .tab').forEach(b=>b.onclick=()=>{const t=b.dataset.tab;
  if(t==='more'){$('mnavBtn').click();return}
  if(t==='missions')openPage('missions',null);else setView(t);markTab()});
function todayCard(o){
  const el=$('today');if(!el)return;const rows=[];const add=(ic,html,go)=>rows.push('<div data-go="'+(go||'')+'"><span>'+ic+'</span><span>'+html+'</span></div>');
  const nd=o.needs||[],nb=$('nMissions');if(nb){nb.classList.toggle('alert',!!nd.length);if(nd.length)nb.textContent='!'+nd.length}
  if(nd.length)add('\u26A0\uFE0F','<b>'+esc2(nd[0].who)+'</b> '+esc2(nd[0].title)+(nd.length>1?' <small>\u00b7 +'+(nd.length-1)+' more</small>':''),'missions');
  const sc=o.schedule||[],next=sc.find(e=>e.state!=='done');
  if(next)add('\u{1F4DA}','<b>'+esc2(next.what)+'</b> '+(next.state==='now'?'now':'at '+esc2(next.at))+(next.where?' <small>\u00b7 '+esc2(next.where)+'</small>':''),'calendar');
  else if(o.tomorrow)add('\u{1F4DA}','Done for today <small>\u00b7 tomorrow '+esc2(o.tomorrow.at)+' '+esc2(o.tomorrow.what)+'</small>','calendar');
  const g=o.goal_today||[];if(g.length)add('\u{1F3AF}','<b>'+g.length+' goal task'+(g.length>1?'s':'')+'</b> today <small>\u00b7 '+esc2(g[0].task)+'</small>','goals');
  (o.missions||[]).slice(0,2).forEach(m=>add('\u{1F680}','<b>'+esc2(m.title)+'</b> <small>\u00b7 working</small>','missions'));
  const r=(o.reminders||[])[0];if(r)add('\u23F0',esc2(r.what)+' <small>\u00b7 '+esc2(r.at)+'</small>','tasks');
  const dl=(o.deadlines||[]).filter(d=>d.days>=0&&d.days<=3);dl.slice(0,2).forEach(d=>add('\u{1F4DD}',esc2(String(d.text).slice(0,60))+' <small>\u00b7 '+(d.days?'in '+d.days+' day'+(d.days>1?'s':''):'today')+'</small>','tasks'));
  const late=(o.deadlines||[]).filter(d=>d.days<0).length;if(late)add('\u26A0\uFE0F',late+' overdue','tasks');
  const low=(o.attendance||[]).filter(a=>a.total&&a.percent<a.threshold);if(low.length)add('\u{1F4C9}',low.slice(0,2).map(a=>esc2(a.course)+' '+Math.round(a.percent)+'%').join(' \u00b7 '),'calendar');
  el.innerHTML=rows.join('');el.hidden=!rows.length;
  el.querySelectorAll('[data-go]').forEach(d=>d.onclick=()=>{if(d.dataset.go){openPage(d.dataset.go,null);markTab()}})}
// ---------- arriving from Share or a home-screen shortcut ----------
(async()=>{try{
  const q=new URLSearchParams(location.search);const shared=q.get('shared'),act=q.get('do');
  if(!shared&&!act)return;history.replaceState({},'',location.pathname+location.hash);
  await new Promise(r=>setTimeout(r,1200));                     // let the chat load first
  if(act==='talk'){talk();return}
  if(act==='mission'){openPage('missions',null);setTimeout(()=>{const t=$('mTask');if(t)t.focus()},900);return}
  if(act==='today'){setView('stage');return}
  if(act==='quiz'){setView('stage');const qq=$('q');qq.value='quiz me on ';qq.focus();return}
  if(shared&&'caches' in window){
    const c=await caches.open('athena-share');const r=await c.match('/share-meta');if(!r)return;
    const meta=await r.json();
    const words=[meta.title,meta.text,meta.url].filter(Boolean).filter((v,i,a)=>a.indexOf(v)===i).join(' ');
    let first=true;                                          // the chat takes one attachment: the first file
    for(const k of meta.files){const fr=await c.match(k);await c.delete(k);if(!fr||!first)continue;first=false;
      takeFile(new File([await fr.blob()],decodeURIComponent(fr.headers.get('X-Name')||'shared'),{type:fr.headers.get('Content-Type')||''}))}
    await c.delete('/share-meta');
    setView('stage');const qq=$('q');qq.value=words?words+' - ':'';qq.focus();
    $('txAth').textContent='Shared with Athena - say what to do with it ("summarise this", "add it to my Signals project").'}
}catch(e){}})();
async function pollOverview(){
  try{const o=await api('/overview');if(!o||!o.t)return;try{holo(o)}catch(e){}try{todayCard(o)}catch(e){}
    const tl=$('tl');tl.innerHTML='';const sc=o.schedule||[];
    const left=sc.filter(e=>e.state!=='done');
    if(!sc.length)tl.innerHTML='<div class="empty">Nothing scheduled today.</div>';
    sc.forEach(e=>{const r=document.createElement('div');r.className='tr2 '+e.state;
      const pct=e.state==='done'?100:e.state==='now'?60:0;
      const when=e.state==='done'?'Done':e.state==='now'?'Now':(e.mins>=60?'In '+Math.floor(e.mins/60)+'h '+(e.mins%60)+'m':'In '+e.mins+'m');
      r.innerHTML='<time>'+e.at+'</time><span class="pt"></span><div class="nm"><b></b><u><i style="width:'+pct+'%"></i></u></div><em>'+when+'</em>';
      r.querySelector('b').textContent=e.what+(e.where?' · '+e.where:'');tl.appendChild(r)});
    if(sc.length&&!left.length){const d=document.createElement('div');d.className='empty';
      d.textContent='Done for today.'+(o.tomorrow?' Tomorrow: '+o.tomorrow.at+' '+o.tomorrow.what:'');tl.appendChild(d)}
    const m=o.memory||{};$('mLearned').textContent=m.learned??'--';$('mChats').textContent=m.chats??'--';
    $('mSnip').textContent=m.snippets??'--';$('nMem').textContent=m.learned??'-';$('nChats').textContent=m.chats??'-';
    $('oMem').textContent=(m.learned??0)+' learned · '+(m.snippets??0)+' indexed';constellation((m.learned||0)+(m.chats||0));
    const pv=o.providers||{};let n=0;$('llm').innerHTML='';
    PROV.forEach(([k,name,ic,col])=>{const v=pv[k];const on=k==='ollama'?v>0:!!v;if(on)n++;
      const d=document.createElement('div');d.className='lp'+(on?' on':'');
      d.innerHTML='<i style="color:'+col+'">'+ic+'</i><div style="min-width:0"><b>'+name+'</b><small>'+
        (k==='ollama'?(v?v+' models':'Not running'):k==='claude'?'Not linked (paid)':on?'Connected':'No key')+'</small></div>';
      $('llm').appendChild(d)});
    $('llmCount').textContent=n+' CONNECTED';llmN=n;showLLM();
    $('nTools').textContent=o.tools||'-';$('nRoutines').textContent=(o.routines||[]).length||'-';
    $('city').textContent=o.city||'--';
    const w=o.weather||'',tc=(w.match(/-?\d+\s*C/)||[''])[0].replace(/\s*C/,'°C');
    $('weather').textContent=w?(tc?tc+' · ':'')+w.split(',')[0].split(' ').slice(0,2).join(' '):'--';
  }catch(e){}
}


// ---------- Command Centre (design E): next up, today, messages, deadlines, music ----------
let nxAt=null;
function greet(){const h=new Date().getHours();return h<5?'GOOD NIGHT':h<12?'GOOD MORNING':h<17?'GOOD AFTERNOON':'GOOD EVENING'}
function holo(o){
  const now=new Date(),past=at=>{const [h,m]=at.split(':').map(Number);return h*60+m<now.getHours()*60+now.getMinutes()};
  const items=[...(o.schedule||[]).filter(e=>e.at!=='all day').map(e=>({what:e.what,where:e.where||'',at:e.at,state:e.state})),
    ...(o.reminders||[]).map(r=>({what:r.what,where:'Reminder',at:r.at,state:past(r.at)?'done':'next'}))].sort((a,b)=>a.at<b.at?-1:a.at>b.at?1:0);
  const att=o.attendance||[];
  const attOf=n=>{n=(n||'').toLowerCase();return att.find(a=>{const c=a.course.toLowerCase();return c&&(n.includes(c)||c.includes(n))})};
  const attCol=a=>a.percent<a.threshold?'#ff6b8a':a.percent<a.threshold+5?'#ffb35c':'#62f5c0';
  const next=items.find(e=>e.state==='next'),busyNow=items.find(e=>e.state==='now');
  $('nxWhat').textContent=next?next.what:busyNow?busyNow.what:(o.tomorrow?'Tomorrow: '+o.tomorrow.what:'Nothing scheduled');
  $('nxWhere').textContent=next?((next.where?next.where.toUpperCase()+' · ':'')+next.at):busyNow?'HAPPENING NOW':(o.tomorrow?o.tomorrow.at:'');
  nxAt=null;if(next){const [h,m]=next.at.split(':').map(Number);nxAt=new Date();nxAt.setHours(h,m,0,0)}
  $('nxC').hidden=!next;tickNext();
  const na=attOf((next||busyNow||{}).what);const nx=$('nxAtt');
  if(na){nx.style.color=attCol(na);nx.textContent='ATTENDANCE '+na.percent.toFixed(0)+'% · '+(na.need?"DON'T SKIP — "+na.need+' SHORT':'CAN SKIP '+na.can_skip)}else nx.textContent='';
  const t=$('track');t.innerHTML='';
  if(!items.length)t.innerHTML='<div class="hempty">Nothing on today.</div>';
  items.forEach(e=>{const d=document.createElement('div');d.className=e.state;d.innerHTML='<i></i><time></time><b></b>';
    d.querySelector('time').textContent=e.at+(e.state==='now'?' · NOW':'');d.querySelector('b').textContent=e.what;
    const a=attOf(e.what);if(a){const p=document.createElement('em');p.className='pct';p.style.color=attCol(a);p.textContent=a.percent.toFixed(0)+'%';d.querySelector('b').appendChild(p)}
    t.appendChild(d)});
  const as=$('atts');as.innerHTML='';
  if(!att.length)as.innerHTML='<div class="hempty">Say "open the UPES login" to connect.</div>';
  att.forEach(a=>{const d=document.createElement('div');d.className='att';const c=attCol(a);
    d.innerHTML='<span></span><u><i></i></u><b></b>';d.querySelector('span').textContent=a.course;
    const i=d.querySelector('i');i.style.width=Math.min(100,a.percent)+'%';i.style.background=c;
    const b=d.querySelector('b');b.textContent=a.percent.toFixed(0)+'%';b.style.color=c;
    d.title=a.need?('needs '+a.need+' more to reach 75%'):('can skip '+a.can_skip);as.appendChild(d)});
  const age=o.upes_read?Math.round((Date.now()/1000-o.upes_read)/60):0;
  $('attNote').textContent=o.upes_signed_out?'A DAY OLD · SAY "OPEN UPES"':
    att.filter(a=>a.need).length?att.filter(a=>a.need).length+' BELOW 75%':(age>45?'READ '+(age<120?age+' MIN':Math.round(age/60)+' H')+' AGO':'LIVE · UPES');
  $('attNote').style.color=o.upes_signed_out?'#ff8aa5':'';
  $('tdLeft').textContent=items.length?items.filter(e=>e.state!=='done').length+' LEFT':'';
  // who wrote - never what; the backend sends no message text
  const COL={WhatsApp:'#62f5c0',SMS:'#b9a2ff',Gmail:'#ff7ad1'};
  const ms=[...(o.senders||[]),...(o.mail_from||[]).map(w=>({who:w,app:'Gmail',n:1}))].slice(0,4);
  const box=$('msgs');box.innerHTML='';
  if(!ms.length)box.innerHTML='<div class="hempty">No messages today.</div>';
  ms.forEach(m=>{const d=document.createElement('div');d.className='msg';d.style.color=COL[m.app]||'#b9a2ff';
    d.innerHTML='<i><span></span></i><div style="min-width:0"><b style="color:#ece8ff"></b><small></small></div><em></em>';
    d.querySelector('span').textContent=((m.who||'').match(/[\p{L}\d]/u)||['·'])[0].toUpperCase();
    d.querySelector('b').textContent=m.who;d.querySelector('small').textContent=m.app.toUpperCase();
    d.querySelector('em').textContent=m.n+(m.app==='Gmail'?' UNREAD':m.n>1?' MSGS':' MSG');box.appendChild(d)});
  const ac=$('acts');ac.innerHTML='';const al=o.activity||[];
  if(!al.length)ac.innerHTML='<div class="hempty">Nothing yet.</div>';
  al.slice(0,5).forEach(a=>{const d=document.createElement('div');d.className='act';d.innerHTML='<time></time><span></span>';
    d.querySelector('time').textContent=new Date(a.t*1000).toTimeString().slice(0,5);d.querySelector('span').textContent=a.what;
    d.title=a.what;ac.appendChild(d)});
  const ab=$('autoBtn');ab.textContent=o.auto_paused?'AUTO PAUSED':'AUTO ON';ab.classList.toggle('off',!!o.auto_paused);ab.dataset.p=o.auto_paused?'1':'';
  const du=$('dues');du.innerHTML='';const dl=o.deadlines||[];
  if(!dl.length)du.innerHTML='<div class="hempty">Nothing due. Say "check my deadlines".</div>';
  dl.forEach(x=>{const col=x.days<=1?'#ff7ad1':x.days<=4?'#ffb35c':'#b9a2ff';
    const lab=x.days<0?'OVERDUE':x.days===0?'TODAY':x.days===1?'1 DAY':x.days+' DAYS';
    const fill=Math.max(1,Math.min(10,10-x.days));
    const d=document.createElement('div');d.className='due';
    d.innerHTML='<div><span></span><em style="color:'+col+'">'+lab+'</em></div><div class="segs">'+
      Array.from({length:10},(_,i)=>'<i'+(i<fill?' style="background:'+col+'"':'')+'></i>').join('')+'</div>';
    d.querySelector('span').textContent=x.text;du.appendChild(d)})}
function tickNext(){if(!nxAt)return;const m=Math.max(0,Math.round((nxAt-new Date())/60000));
  $('nxIn').textContent=Math.floor(m/60)+':'+String(m%60).padStart(2,'0')}
const PLAY='<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4v16l13-8z"/></svg>',
  PAUSE='<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>';
async function pollMusic(){if(document.hidden||!document.body.classList.contains('dash')||document.body.classList.contains('pageview'))return;
  try{const m=(await api('/view/workflows')).music,on=!!(m&&m.title);
    $('npT').textContent=on?m.title:'Nothing playing';$('npA').textContent=on?(m.artist||''):'Say "play some music"';
    $('npApp').textContent=on&&(m.app||'').toLowerCase().includes('spotify')?'SPOTIFY':'MEDIA';
    $('npArt').style.backgroundImage=on&&m.art?'url("'+m.art+'")':'';
    $('npBar').style.width=on&&m.length?Math.min(100,m.position/m.length*100)+'%':'0';
    const pp=$('npPP'),playing=on&&m.playing;pp.dataset.m=playing?'pause':'play';pp.innerHTML=playing?PAUSE:PLAY;
    pp.setAttribute('aria-label',playing?'Pause':'Play')}catch(e){}}
document.querySelectorAll('.npc [data-m]').forEach(b=>b.onclick=async()=>{await act('music',{action:b.dataset.m});setTimeout(pollMusic,700)});
setInterval(pollMusic,4000);
// Read-outs of WhatsApp/SMS on the PC speakers: one switch, takes effect at once.
function showMute(m){const b=$('nMute');b.textContent=m?'READ-OUT OFF':'READ-OUT ON';b.classList.toggle('off',!!m);b.dataset.m=m?'1':''}
async function loadMute(){try{showMute((await api('/view/notify')).muted)}catch(e){}}
$('nMute').onclick=async()=>{const want=!$('nMute').dataset.m;try{showMute((await api('/view/notify',{method:'POST',body:JSON.stringify({muted:want})})).muted)}catch(e){}};
loadMute();
// Alarm (alarm.py): a full-screen STOP while it rings, on the PC and the phone.
async function pollAlarm(){if(document.hidden)return;try{const r=(await api('/view/alarm')).ringing;const b=$('alarmBox');
  b.hidden=!r;if(r)$('alarmText').textContent=r.text}catch(e){}}
setInterval(pollAlarm,2000);pollAlarm();
$('alarmStop').onclick=async()=>{try{await api('/view/alarm',{method:'POST',body:JSON.stringify({action:'stop'})})}catch(e){}$('alarmBox').hidden=true};
$('alarmSnooze').onclick=async()=>{try{await api('/view/alarm',{method:'POST',body:JSON.stringify({action:'snooze',minutes:10})})}catch(e){}$('alarmBox').hidden=true};
$('autoBtn').onclick=async()=>{const pause=!$('autoBtn').dataset.p;try{const j=await api('/view/automation',{method:'POST',body:JSON.stringify({pause})});
  const ab=$('autoBtn');ab.textContent=j.paused?'AUTO PAUSED':'AUTO ON';ab.classList.toggle('off',!!j.paused);ab.dataset.p=j.paused?'1':''}catch(e){}};

// ---------- clock ----------
function tick(){const d=new Date();$('clock').textContent=d.toTimeString().slice(0,8);$('stClock').textContent=d.toTimeString().slice(0,5);
  $('date').textContent=d.toLocaleDateString(undefined,{weekday:'long',day:'numeric',month:'long',year:'numeric'});
  const ts=d.toTimeString();$('hxClock').innerHTML=ts.slice(0,5)+'<span>:'+ts.slice(6,8)+'</span>';
  $('hxDate').textContent=d.toLocaleDateString('en-GB',{weekday:'short',day:'2-digit',month:'2-digit',year:'numeric'}).toUpperCase().replace(/,/g,' ·').replace(/\//g,'.');
  if(d.getSeconds()===0)tickNext()}
tick();setInterval(tick,1000);

// ---------- conversations ----------
function closeVeils(){document.querySelectorAll('.veil').forEach(v=>v.classList.remove('open'))}
async function loadChats(){const l=$('chatlist');
  try{const j=await api('/chats');l.innerHTML='';
    (j.chats||[]).forEach(c=>{const r=document.createElement('div');r.className='chat'+(c.live?' live':'');
      const t=document.createElement('span');t.className='t';t.textContent=c.title;
      const w=document.createElement('span');w.className='w';w.textContent=c.when;
      const d=document.createElement('span');d.className='d';d.textContent='✕';
      t.onclick=()=>openChat(c.id);
      // One tap deletes (into the recycle folder) and offers Undo for 5 s. The
      // old two-tap "Delete?" was easy to miss and read as a broken button (4 Oct 2026).
      d.onclick=async e=>{e.stopPropagation();
        try{await api('/chats/'+encodeURIComponent(c.id),{method:'DELETE'})}catch(err){d.textContent='Failed';return}
        r.classList.add('gone');t.textContent='Deleted · ';w.textContent='';
        const u=document.createElement('span');u.className='undo';u.textContent='Undo';
        u.onclick=async ev=>{ev.stopPropagation();
          try{await api('/chats/'+encodeURIComponent(c.id)+'/restore',{method:'POST',body:'{}'})}catch(err){}
          loadChats()};
        t.appendChild(u);d.textContent='';setTimeout(()=>{if(r.isConnected&&r.classList.contains('gone'))r.remove()},5000)};
      r.append(t,w,d);l.appendChild(r)});
    if(!(j.chats||[]).length)l.textContent='Nothing saved yet.'}
  catch(e){l.textContent='Cannot reach the PC.'}}
async function openChat(id,show=true){
  try{const j=await api('/chats/'+id);say.innerHTML='';document.body.classList.remove('chatting');
    let lq='',ln=null;
    (j.messages||[]).forEach(m=>{if(m.role==='user'){lq=m.text;ln=userLine(m.text)}else answer(m.text,false,{q:lq,qnode:ln})});
    closeVeils();
    // A new chat opens the chat screen, ready to type: on the Command Centre it
    // used to clear an area you could not see, and looked like nothing happened.
    if(show){setView('chat');if(id==='new'){const q=$('q')||$('topq');if(q)setTimeout(()=>q.focus(),50)}}
    loadSide()}
  catch(e){answer('Could not open that conversation.',true)}}

// ---------- chat sidebar (ChatGPT-style, 13 Oct 2026) ----------
let csAll=[],csProj=[],csTimer=null;
const csOpen=new Set((()=>{try{return JSON.parse(localStorage.getItem('athena_projopen')||'[]')}catch(e){return []}})());
const saveOpen=()=>{try{localStorage.setItem('athena_projopen',JSON.stringify([...csOpen]))}catch(e){}};
const csBusy=()=>!!document.querySelector('.cs-notes,.cs-move,.cs-edit,.cs-row.gone');
async function loadSide(){if(csBusy())return;const q=($('csFind').value||'').trim();
  try{const j=await api('/chats'+(q?'?q='+encodeURIComponent(q):''));csAll=j.chats||[];csProj=j.projects||[];drawSide(q)}catch(e){}}
function csSec(l,label,add,tip){const h=document.createElement('div');h.className='cs-sec';const t=document.createElement('h4');t.textContent=label;h.appendChild(t);
  if(add)h.appendChild(actBtn('+',add,tip));l.appendChild(h);return h}
function csEmpty(l,txt){const p=document.createElement('p');p.className='cs-empty';p.textContent=txt;l.appendChild(p)}
function drawSide(q){const l=$('csList');if(!l)return;l.innerHTML='';
  if(q){csSec(l,'Results');if(!csAll.length)csEmpty(l,'No chats match.');csAll.forEach(c=>l.appendChild(sideRow(c,true)));return}
  csSec(l,'Projects',()=>projForm(null,l.querySelector('.cs-sec')),'New project');
  csProj.forEach(p=>{const its=csAll.filter(c=>c.project===p.id);l.appendChild(projRow(p,its.length));
    if(csOpen.has(p.id)){const box=document.createElement('div');box.className='cs-in';
      (p.files||[]).forEach(x=>{const r=document.createElement('div');r.className='cs-file';r.tabIndex=0;r.textContent='📄 '+x.name;r.title=x.name+' - click to manage files';const go=e=>{e.stopPropagation();projForm(p,box.previousSibling)};r.onclick=go;r.onkeydown=e=>{if(e.key==='Enter')go(e)};box.appendChild(r)});its.forEach(c=>box.appendChild(sideRow(c)));if(!its.length)csEmpty(box,'No chats yet - press + on the project to start one.');l.appendChild(box)}});
  if(!csProj.length)csEmpty(l,'Group chats by subject. Notes you add to a project are kept in mind in every chat in it.');
  const known=new Set(csProj.map(p=>p.id)),loose=csAll.filter(c=>!c.project||!known.has(c.project));
  const d=new Date(),start=new Date(d.getFullYear(),d.getMonth(),d.getDate()).getTime()/1000,day=86400;
  const grp=c=>c.pinned?'Pinned':c.updated>=start?'Today':c.updated>=start-day?'Yesterday':
    c.updated>=start-7*day?'Previous 7 days':c.updated>=start-30*day?'Previous 30 days':'Older';
  ['Pinned','Today','Yesterday','Previous 7 days','Previous 30 days','Older'].forEach(g=>{
    const its=loose.filter(c=>grp(c)===g);if(!its.length)return;csSec(l,g);its.forEach(c=>l.appendChild(sideRow(c)))});
  if(!csAll.length)csEmpty(l,'No chats yet.')}
function projRow(p,n){const r=document.createElement('div');r.className='cs-proj';r.tabIndex=0;
  const open=csOpen.has(p.id);r.setAttribute('aria-expanded',open?'true':'false');
  const car=document.createElement('span');car.className='car';car.textContent=open?'▾':'▸';
  const t=document.createElement('span');t.className='t';t.textContent=p.name;t.title=p.name+((p.files||[]).length?' · '+p.files.length+' file'+(p.files.length>1?'s':''):'')+(p.notes?' - '+p.notes.slice(0,140):'');
  const k=document.createElement('span');k.className='n';k.textContent=n||'';
  let gl=null;if(p.goal){gl=document.createElement('span');gl.className='pgl';gl.textContent='🎯 '+p.goal.pct+'%';
    gl.title='Goal: '+p.goal.title+' · '+p.goal.pct+'% done'+(p.goal.today_left?' · '+p.goal.today_left+' left today':'');
    gl.onclick=e=>{e.stopPropagation();openPage('goals',null)}}
  const a=document.createElement('span');a.className='acts';let sure=false;
  a.append(actBtn('+',()=>{csOpen.add(p.id);saveOpen();openChat('new?project='+encodeURIComponent(p.id))},'New chat in '+p.name),
    actBtn('✎',()=>projForm(p,r),'Name and notes'),
    actBtn('✕',b=>{if(!sure){sure=true;b.textContent='Delete?';b.title='Click again: the project goes, its chats stay';
        setTimeout(()=>{sure=false;b.textContent='✕'},3000);return}
      api('/projects/delete',{method:'POST',body:JSON.stringify({id:p.id})}).catch(()=>{}).then(loadSide)},'Delete project (its chats are kept)'));
  const toggle=()=>{csOpen.has(p.id)?csOpen.delete(p.id):csOpen.add(p.id);saveOpen();drawSide('')};
  r.onclick=toggle;r.onkeydown=e=>{if(e.key==='Enter'&&e.target===r)toggle()};
  r.append(car,t,k,a);if(gl)k.before(gl);return r}
function projForm(p,after){const old=document.querySelector('.cs-notes');if(old)old.remove();
  const f=document.createElement('div');f.className='cs-notes';
  const nm=document.createElement('input');nm.placeholder='Project name, e.g. Signals & Systems';nm.value=p?p.name:'';nm.setAttribute('aria-label','Project name');
  const no=document.createElement('textarea');no.placeholder='Notes Athena keeps in mind in this project - exam dates, what to focus on, how you like answers…';
  no.value=p?(p.notes||''):'';no.setAttribute('aria-label','Project notes');
  const hint=document.createElement('small');hint.textContent='Every chat in this project sees these notes.';
  f.append(nm,no,hint);
  // Files: syllabus, notes, slides. Athena reads each once and uses the parts that fit each question.
  if(p){const fh=document.createElement('small');fh.innerHTML='<b>Files</b> - PDF, Word, PowerPoint or text, up to 30 MB. Athena uses the parts that fit each question.';
    const list=document.createElement('div');list.className='pf-list';
    const draw=files=>{list.innerHTML='';(files||[]).forEach(x=>{const r=document.createElement('div');r.className='pf';
        const n=document.createElement('span');n.className='n';n.textContent='📄 '+x.name;n.title=x.name;
        const z=document.createElement('span');z.className='z';z.textContent=x.size>1048576?(x.size/1048576).toFixed(1)+' MB':Math.max(1,Math.round(x.size/1024))+' KB';
        r.append(n,z,actBtn('✕',async()=>{await api('/projects/'+encodeURIComponent(p.id)+'/files/delete',{method:'POST',body:JSON.stringify({name:x.name})}).catch(()=>{});
          p.files=(p.files||[]).filter(y=>y.name!==x.name);draw(p.files)},'Remove '+x.name));list.appendChild(r)});
      if(!(files||[]).length){const e=document.createElement('div');e.className='pf-empty';e.textContent='No files yet.';list.appendChild(e)}};
    draw(p.files);
    const inp=document.createElement('input');inp.type='file';inp.multiple=true;inp.hidden=true;inp.accept='.pdf,.docx,.pptx,.txt,.md,.csv';
    const add=actBtn('+ Add files',()=>inp.click(),'Add files to this project');
    const upOne=file=>new Promise(res=>{const row=document.createElement('div');row.className='pf up';row.textContent='Reading '+file.name+'… 0%';list.appendChild(row);
      const x=new XMLHttpRequest();x.open('POST','/projects/'+encodeURIComponent(p.id)+'/files?name='+encodeURIComponent(file.name));
      x.setRequestHeader('X-Token',token());
      x.upload.onprogress=e=>{if(e.lengthComputable)row.textContent='Reading '+file.name+'… '+Math.floor(100*e.loaded/e.total)+'%'};
      x.onload=()=>{let j={};try{j=JSON.parse(x.responseText)}catch(e){}
        if(x.status==200&&j.file){row.remove();p.files=[...(p.files||[]).filter(y=>y.name!==j.file.name),j.file];draw(p.files)}
        else{row.classList.add('bad');row.textContent=j.error||('Could not add '+file.name)}res()};
      x.onerror=()=>{row.classList.add('bad');row.textContent='Cannot reach the PC.';res()};x.send(file)});
    inp.onchange=async()=>{const fs=Array.from(inp.files||[]);inp.value='';for(const file of fs)await upOne(file)};
    // Drop files straight onto the project box.
    f.addEventListener('dragover',e=>{if(hasFiles(e)){e.preventDefault();e.stopPropagation();f.classList.add('over')}});
    f.addEventListener('dragleave',()=>f.classList.remove('over'));
    f.addEventListener('drop',async e=>{if(!hasFiles(e))return;e.preventDefault();e.stopPropagation();f.classList.remove('over');
      document.body.classList.remove('dropping');for(const file of Array.from(e.dataTransfer.files))await upOne(file)});
    f.append(fh,list,add,inp)}
  else{const fh=document.createElement('small');fh.textContent='You can add files (syllabus, notes, slides) once it is created.';f.append(fh)}
  const row=document.createElement('div');row.className='row';
  const save=actBtn(p?'Done':'Create',async()=>{const name=nm.value.trim();if(!name){nm.focus();return}
    let made=null;
    try{const j=await api('/projects',{method:'POST',body:JSON.stringify({id:p?p.id:'',name,notes:no.value})});made=j.project}catch(e){}
    f.remove();
    if(!p&&made){csOpen.add(made.id);saveOpen();await loadSide();
      // straight on to adding files
      const r=[...document.querySelectorAll('.cs-proj')].find(x=>x.querySelector('.t').textContent===made.name);
      if(r)projForm({...made,files:[]},r)}
    else loadSide()});save.classList.add('pri');
  row.append(actBtn('Cancel',()=>{f.remove();loadSide()}),save);f.append(row);
  f.onclick=e=>e.stopPropagation();after.after(f);nm.focus()}
function sideRow(c,snip){const r=document.createElement('div');r.className='cs-row'+(c.live?' on':'');r.tabIndex=0;
  const tw=document.createElement('span');tw.className='tw';
  const t=document.createElement('span');t.className='t';t.textContent=c.title;t.title=c.title;tw.appendChild(t);
  if(snip&&c.snippet){const s2=document.createElement('span');s2.className='snip';s2.textContent=c.snippet;tw.appendChild(s2)}
  const a=document.createElement('span');a.className='acts';const id=encodeURIComponent(c.id);
  a.append(
    actBtn(c.pinned?'★':'☆',async()=>{await api('/chats/'+id+'/pin',{method:'POST',body:JSON.stringify({on:!c.pinned})}).catch(()=>{});loadSide()},c.pinned?'Unpin':'Pin'),
    actBtn('✎',()=>{const i=document.createElement('input');i.className='cs-edit';i.value=c.title;i.setAttribute('aria-label','Chat name');
      tw.replaceWith(i);a.style.display='none';i.focus();i.select();let done=false;
      const finish=async keep=>{if(done)return;done=true;const v=i.value.trim();
        if(keep&&v&&v!==c.title)await api('/chats/'+id+'/rename',{method:'POST',body:JSON.stringify({title:v})}).catch(()=>{});
        i.remove();loadSide()};
      i.onkeydown=e=>{if(e.key==='Enter')finish(true);if(e.key==='Escape')finish(false)};i.onblur=()=>finish(true);i.onclick=e=>e.stopPropagation()},'Rename'),
    actBtn('↪',()=>{const sel=document.createElement('select');sel.className='cs-move';sel.setAttribute('aria-label','Move to project');
      [['','- No project -'],...csProj.map(p=>[p.id,p.name])].forEach(([v,n])=>{const o=document.createElement('option');o.value=v;o.textContent=n;o.selected=v===(c.project||'');sel.appendChild(o)});
      if(!csProj.length){const o=document.createElement('option');o.disabled=true;o.textContent='(make a project first: + next to Projects)';sel.appendChild(o)}
      a.style.display='none';tw.after(sel);sel.focus();
      const done=()=>{sel.remove();loadSide()};
      sel.onchange=async()=>{await api('/chats/'+id+'/project',{method:'POST',body:JSON.stringify({project:sel.value})}).catch(()=>{});
        if(sel.value){csOpen.add(sel.value);saveOpen()}done()};
      sel.onblur=done;sel.onclick=e=>e.stopPropagation();sel.onkeydown=e=>{if(e.key==='Escape')done()}},'Move to project'),
    actBtn('⬇',()=>exportChat(c),'Export as Markdown'),
    actBtn('✕',async()=>{try{await api('/chats/'+id,{method:'DELETE'})}catch(e){return}
      if(c.live)say.innerHTML='';
      r.classList.add('gone');t.textContent='Deleted · ';a.remove();
      const u=document.createElement('button');u.type='button';u.className='undo';u.textContent='Undo';
      u.onclick=async e=>{e.stopPropagation();await api('/chats/'+id+'/restore',{method:'POST',body:'{}'}).catch(()=>{});r.classList.remove('gone');loadSide()};
      t.appendChild(u);setTimeout(()=>{if(r.isConnected&&r.classList.contains('gone')){r.classList.remove('gone');loadSide()}},5000)},'Delete'));
  r.onclick=()=>openChat(c.id);r.onkeydown=e=>{if(e.key==='Enter'&&e.target===r)openChat(c.id)};
  r.append(tw,a);return r}
async function exportChat(c){try{const r=await fetch('/chats/'+encodeURIComponent(c.id)+'/export',{headers:{'X-Token':token()}});
    if(!r.ok)throw new Error('HTTP '+r.status);const b=await r.blob();const a=document.createElement('a');
    a.href=URL.createObjectURL(b);a.download=((c.title||'chat').replace(/[^\w\- ]+/g,'').trim()||'chat')+'.md';
    document.body.appendChild(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove()},1500)}
  catch(e){answer('Could not export that chat.',true)}}
$('csNew').onclick=()=>openChat('new');
$('csFind').oninput=()=>{clearTimeout(csTimer);csTimer=setTimeout(loadSide,250)};
$('cvClose').onclick=()=>{document.body.classList.remove('cv-open');$('cvFrame').src='about:blank'};
// Smart titles land a few seconds after the first answer: keep the list fresh while it is on screen.
setInterval(()=>{if(document.body.classList.contains('fullchat')&&!document.hidden)loadSide()},6000);
const openHist=()=>{$('histVeil').classList.add('open');loadChats()};
$('histBtn').onclick=openHist;$('histBtn2').onclick=openHist;
$('histVeil').onclick=e=>{if(e.target.id==='histVeil')closeVeils()};
$('newChat').onclick=()=>openChat('new');$('newChat2').onclick=()=>openChat('new');
// On a phone the sidebar lives in the same drawer as the chat list.
$('mnavBtn').onclick=()=>{const dr=$('drawer');if(!dr.querySelector('.navcopy')){
    const n=document.querySelector('aside nav').cloneNode(true);n.classList.add('navcopy');
    n.querySelectorAll('button').forEach(b=>{b.removeAttribute('id');
      if(b.dataset.cmd)b.onclick=()=>{closeVeils();ask(b.dataset.cmd)};
      else if(b.dataset.page)b.onclick=()=>openPage(b.dataset.page,null);
      else if(b.dataset.view)b.onclick=()=>{closeVeils();setView(b.dataset.view)}});
    const h=document.createElement('h3');h.textContent='COMMAND CENTER';dr.prepend(n);dr.prepend(h)}
  openHist()};

// ---------- settings: which model does class work ----------
async function loadModels(){try{const j=await api('/models');const s=$('model');s.innerHTML='';
  (j.models||[]).forEach(m=>{const o=document.createElement('option');o.value=m.model;
    o.textContent=m.label+(m.ready?'':'  - no key');o.disabled=!m.ready;o.selected=m.live;s.appendChild(o)})}catch(e){}}
const openGear=()=>{$('gearVeil').classList.add('open');$('modelSaid').textContent='';loadModels()};
$('gearBtn').onclick=openGear;$('navSettings').onclick=openGear;$('manageBtn').onclick=openGear;
$('gearClose').onclick=closeVeils;
$('model').onchange=async()=>{$('modelSaid').textContent='testing…';
  try{const j=await api('/model',{method:'POST',body:JSON.stringify({model:$('model').value})});
    $('modelSaid').textContent=j.said;pollStatus()}catch(e){$('modelSaid').textContent='Could not switch.'}};

// ---------- start ----------
(async()=>{
  if(!token()){answer('Open this page from the link Athena prints on the PC - it carries your access token.',true);return}
  setView(p.get('home')==='dash'?'dash':'stage');   // the PC window opens on the Command Centre
  await primeLive();
  openChat('current',false);    // load it, but stay on the home screen
  pollStatus();setInterval(pollStatus,2500);
  pollLive();setInterval(pollLive,300);
  pollOverview();setInterval(pollOverview,20000);
  const hp=location.hash.slice(1);if(TITLES[hp])openPage(hp,null);
})();
