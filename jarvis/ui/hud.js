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
