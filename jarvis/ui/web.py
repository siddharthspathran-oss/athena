"""Phone interface: a small web server so Athena can be driven from a browser.

Stdlib only, no framework - this has to start reliably at login, not fight a
dependency tree.

Security matters more here than anywhere else in this project. Athena can run
PowerShell, delete files and read documents, so an open port is an open door:

  * every request needs a token, generated once and kept in data/web_token.txt
  * destructive tools are refused unless the phone explicitly confirms
  * the token never appears in a URL that gets logged - the page keeps it in
    localStorage and sends it as a header

Reachable over Tailscale, so it works on mobile data without exposing anything
to the public internet.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import chats, live, registry, render, vision
from ..tools.knowledge import _extract as extract_text
from ..tools.study import answer_page
from ..agent import Agent
from ..config import DATA, cfg

TOKEN_FILE = DATA / "web_token.txt"

_agent: Agent | None = None
_lock = threading.Lock()
_last_steps: list[dict] = []
_allow_destructive = False


def get_token() -> str:
    """The shared secret, created once and reused."""
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(24)
    TOKEN_FILE.write_text(tok, encoding="utf-8")
    return tok


ORB_KEY_FILE = DATA / "orb_key.txt"


def orb_key() -> str:
    """The desk orb's own key - read state, press listen; nothing more."""
    if not ORB_KEY_FILE.exists() or not ORB_KEY_FILE.read_text(encoding="utf-8").strip():
        ORB_KEY_FILE.write_text(secrets.token_urlsafe(18), encoding="utf-8")
    return ORB_KEY_FILE.read_text(encoding="utf-8").strip()


def _tailscale_https() -> str:
    """The https:// address Tailscale Serve is publishing, if it is on.

    Worth singling out: a browser will only offer to *install* a site, run a
    service worker, or hand it the camera over HTTPS. Over plain http the
    phone can still reach Athena, but only as a bookmark.
    """
    import os
    import subprocess

    exe = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"
    if not exe.exists():
        return ""
    try:
        out = subprocess.run([str(exe), "serve", "status"], capture_output=True,
                             text=True, timeout=8,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return ""
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.startswith("https://"):
            return line.split()[0].rstrip("/")
    return ""


def local_ips() -> list[str]:
    """Addresses this machine can be reached on, Tailscale included."""
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except Exception:
        pass
    return found


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<!-- maximum-scale stops Android zooming the whole app when a field is
     focused, which is what made the layout look out of proportion in the
     installed window. interactive-widget keeps the keyboard from squashing
     the page instead of overlaying it. -->
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,viewport-fit=cover,interactive-widget=resizes-content">
<title>Athena</title>
<!-- Installable from the phone's browser: Chrome offers "Install app",
     iOS Safari needs Share > Add to Home Screen. Either way it then opens
     without browser chrome, with its own icon. -->
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#05080c">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Athena">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<link rel="icon" href="/icons/icon-192.png">
<style>
  :root { --bg:#05080c; --card:rgba(18,30,38,.72); --fg:#dff2f4; --dim:#5f7f8c;
          --accent:#2fe6c0; --accent2:#19b6ff; --edge:rgba(47,230,192,.16);
          --glow:rgba(47,230,192,.45); }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html { -webkit-text-size-adjust:100%; text-size-adjust:100%; }
  body { margin:0; background:var(--bg); color:var(--fg); font-size:16px;
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         display:flex; flex-direction:column; overflow:hidden;
         /* dvh follows the address bar; svh does not jump as it hides. The
            installed app has no bar, so both agree there. */
         height:100svh; height:100dvh;
         padding-top:env(safe-area-inset-top);
         padding-bottom:env(safe-area-inset-bottom); }
  /* The glow behind everything, and a faint grid over it. Fixed and
     pointer-events:none so it never gets in the way of the chat. */
  body::before { content:''; position:fixed; inset:0; pointer-events:none;
    background:
      radial-gradient(60% 40% at 50% -8%, rgba(47,230,192,.16), transparent 70%),
      radial-gradient(45% 35% at 110% 105%, rgba(25,182,255,.12), transparent 70%);
    z-index:0; }
  body::after { content:''; position:fixed; inset:0; pointer-events:none; opacity:.35;
    background-image:linear-gradient(rgba(47,230,192,.05) 1px, transparent 1px),
                     linear-gradient(90deg, rgba(47,230,192,.05) 1px, transparent 1px);
    background-size:44px 44px; z-index:0;
    -webkit-mask-image:radial-gradient(70% 55% at 50% 0%, #000, transparent 75%);
            mask-image:radial-gradient(70% 55% at 50% 0%, #000, transparent 75%); }
  header, #log, #chip, form { position:relative; z-index:1; }

  header { padding:12px 16px;
           border-bottom:1px solid var(--edge); display:flex;
           align-items:center; gap:12px;
           background:linear-gradient(180deg, rgba(47,230,192,.06), transparent); }
  /* The reactor ring: two arcs turning in opposite directions. */
  .ring { width:30px; height:30px; flex:none; position:relative; }
  .ring i { position:absolute; inset:0; border-radius:50%;
            border:1.5px solid transparent; }
  .ring i:nth-child(1){ border-top-color:var(--accent);
    border-right-color:var(--accent); animation:spin 3.2s linear infinite;
    box-shadow:0 0 12px var(--glow); }
  .ring i:nth-child(2){ inset:6px; border-bottom-color:var(--accent2);
    border-left-color:var(--accent2); animation:spin 2.1s linear infinite reverse; }
  .ring b { position:absolute; inset:11px; border-radius:50%;
            background:var(--accent); box-shadow:0 0 14px var(--glow);
            animation:pulse 2.4s ease-in-out infinite; }
  .ring.busy i:nth-child(1){ animation-duration:.7s; }
  .ring.busy i:nth-child(2){ animation-duration:.5s; }
  .ring.bad i { border-top-color:#ff6b6b; border-right-color:#ff6b6b;
                border-bottom-color:#ff6b6b; border-left-color:#ff6b6b; }
  .ring.bad b { background:#ff6b6b; box-shadow:0 0 14px rgba(255,107,107,.5); }
  @keyframes spin { to { transform:rotate(360deg); } }
  @keyframes pulse { 0%,100%{opacity:.55;transform:scale(.82)} 50%{opacity:1;transform:scale(1)} }

  h1 { font-size:14px; margin:0; font-weight:700; letter-spacing:3.4px;
       text-transform:uppercase; color:var(--fg);
       text-shadow:0 0 16px var(--glow); }
  #model { margin-left:auto; max-width:47%; background:rgba(14,24,31,.9);
           color:var(--dim); border:1px solid var(--edge); border-radius:9px;
           font-size:11px; padding:5px 6px; font-family:inherit; }
  #model:focus { outline:none; border-color:var(--accent); }
  header small { color:var(--dim); font-size:10.5px; margin-left:8px;
                 letter-spacing:1.4px; text-transform:uppercase;
                 font-family:ui-monospace,Menlo,Consolas,monospace; }

  #log { flex:1; overflow-y:auto; padding:18px 16px; display:flex;
         flex-direction:column; gap:13px; scrollbar-width:thin;
         scrollbar-color:var(--edge) transparent; }
  #log::-webkit-scrollbar { width:5px; }
  #log::-webkit-scrollbar-thumb { background:var(--edge); border-radius:3px; }

  .msg { max-width:88%; padding:12px 15px; border-radius:15px; line-height:1.5;
         white-space:pre-wrap; word-wrap:break-word; position:relative;
         animation:rise .26s cubic-bezier(.2,.8,.3,1) both;
         backdrop-filter:blur(9px); -webkit-backdrop-filter:blur(9px); }
  @keyframes rise { from{opacity:0;transform:translateY(9px)} to{opacity:1;transform:none} }
  .me { align-self:flex-end; color:#02201c; border-bottom-right-radius:4px;
        background:linear-gradient(135deg, var(--accent), #14c9d6);
        box-shadow:0 5px 22px rgba(47,230,192,.24); font-weight:500; }
  .bot { align-self:flex-start; background:var(--card);
         border:1px solid var(--edge); border-left:2px solid var(--accent);
         border-bottom-left-radius:4px;
         box-shadow:0 5px 24px rgba(0,0,0,.45), inset 0 0 26px rgba(47,230,192,.045); }
  .trace { align-self:flex-start; color:var(--dim); font-size:12px;
           font-family:ui-monospace,Menlo,Consolas,monospace; padding:1px 5px;
           letter-spacing:.4px; animation:rise .26s both; }
  .err { color:#ff8a8a; border-left-color:#ff6b6b !important; }

  form { display:flex; gap:8px; align-items:center;
         padding:11px 12px;
         border-top:1px solid var(--edge);
         background:linear-gradient(0deg, rgba(5,8,12,.96), rgba(5,8,12,.72)); }
  input, button { font:inherit; border-radius:13px; border:1px solid var(--edge); }
  /* min-width:0 is load-bearing. A flex item will not shrink below the
     intrinsic width of a text input (~20 characters), so on a 375px phone
     the text box pushed the Send button clean off the right edge. */
  input { flex:1; min-width:0; padding:13px 15px; background:rgba(14,24,31,.85);
          color:var(--fg); outline:none;
          transition:border-color .2s, box-shadow .2s; }
  input::placeholder { color:var(--dim); }
  input:focus { border-color:var(--accent);
                box-shadow:0 0 0 3px rgba(47,230,192,.12), 0 0 22px rgba(47,230,192,.16); }
  button { padding:13px 17px; border:none; font-weight:700; letter-spacing:.4px;
           flex:none; white-space:nowrap;
           color:#02201c; background:linear-gradient(135deg, var(--accent), #14c9d6);
           box-shadow:0 4px 18px rgba(47,230,192,.28); transition:transform .12s, opacity .2s; }
  button:active { transform:scale(.96); }
  button:disabled { opacity:.42; box-shadow:none; }

  .confirm { align-self:flex-start; background:rgba(42,34,14,.8);
             border:1px solid rgba(214,168,58,.35); border-left:2px solid #d6a83a;
             padding:13px 15px; border-radius:15px; max-width:88%;
             backdrop-filter:blur(9px); }
  .confirm button { margin-top:9px; margin-right:8px; padding:9px 15px; font-size:15px; }
  .no { background:rgba(18,30,38,.9); color:var(--fg);
        border:1px solid var(--edge); box-shadow:none; }
  a.page { display:inline-block; margin-top:10px; padding:9px 15px;
           background:linear-gradient(135deg, var(--accent), #14c9d6); color:#02201c;
           font-weight:700; border-radius:11px; text-decoration:none;
           box-shadow:0 4px 16px rgba(47,230,192,.26); }

  .icon { display:flex; align-items:center; justify-content:center;
          width:42px; min-width:42px; height:42px; background:rgba(14,24,31,.85);
          border:1px solid var(--edge); border-radius:13px; font-size:18px;
          color:var(--dim); cursor:pointer; transition:all .18s; }
  .icon:active { color:var(--accent); border-color:var(--accent);
                 box-shadow:0 0 18px rgba(47,230,192,.22); }
  #chip { padding:0 14px; }
  #chip .att { display:inline-flex; align-items:center; gap:9px;
               background:rgba(18,30,38,.85); border:1px solid var(--edge);
               border-radius:11px; padding:7px 11px; font-size:13px;
               box-shadow:0 0 18px rgba(47,230,192,.09); }
  #chip img { width:34px; height:34px; object-fit:cover; border-radius:7px; }
  #chip .x { color:var(--dim); padding:0 3px; cursor:pointer; }
  .shot { max-width:200px; border-radius:12px; display:block; margin-top:8px;
          border:1px solid var(--edge); }
  /* Saved conversations, as a drawer rather than a permanent column - a
     phone has no width to spare for one. */
  #menu { position:fixed; inset:0; z-index:5; visibility:hidden; }
  #menu.open { visibility:visible; }
  #scrim { position:absolute; inset:0; background:rgba(0,0,0,.6);
           opacity:0; transition:opacity .22s; }
  #menu.open #scrim { opacity:1; }
  #drawer { position:absolute; top:0; bottom:0; left:0; width:80%;
            max-width:310px; background:#080d13;
            border-right:1px solid var(--edge); display:flex;
            flex-direction:column; transform:translateX(-102%);
            transition:transform .24s cubic-bezier(.2,.8,.3,1);
            padding-top:env(safe-area-inset-top);
            padding-bottom:env(safe-area-inset-bottom); }
  #menu.open #drawer { transform:none; }
  #drawer h2 { font-size:10px; letter-spacing:1.6px; text-transform:uppercase;
               color:var(--dim); margin:16px 18px 8px; font-weight:700; }
  #newchat { margin:16px 14px 4px; padding:12px 14px; border-radius:12px;
             background:var(--card); border:1px solid var(--edge);
             color:var(--accent); font-weight:700; font-size:14.5px;
             text-align:left; }
  #chatlist { flex:1; overflow-y:auto; padding:0 10px 14px; }
  .chat { display:flex; align-items:center; gap:8px; padding:11px 10px;
          border-radius:10px; margin-bottom:2px; }
  .chat.live { background:var(--card); }
  .chat span.t { flex:1; font-size:14px; color:var(--fg);
                 overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .chat.live span.t { color:var(--accent); }
  .chat span.w { font-size:11px; color:var(--dim); }
  .chat span.d { color:var(--dim); font-size:13px; padding:0 2px 0 6px; }
  .burger { display:flex; align-items:center; justify-content:center;
            width:30px; height:30px; flex:none; font-size:19px;
            color:var(--dim); }
  .file { display:inline-block; margin-top:7px; padding:6px 11px;
          background:rgba(0,0,0,.22); border-radius:9px; font-size:13.5px;
          font-family:ui-monospace,Menlo,Consolas,monospace; }
  @media (prefers-reduced-motion:reduce){
    .ring i,.ring b{animation:none} .msg,.trace{animation:none} }
</style></head><body>
<div id="menu"><div id="scrim"></div><nav id="drawer">
  <button id="newchat">+  New chat</button>
  <h2>Recent</h2><div id="chatlist"></div>
</nav></div>
<header><span class="burger" id="burger">&#9776;</span>
<span class="ring" id="dot"><i></i><i></i><b></b></span>
<select id="model" title="Which model answers class work"></select>
<h1>Athena</h1><small id="host"></small></header>
<div id="log"></div>
<div id="chip"></div>
<form id="f">
  <label class="icon" for="cam" title="Take a photo">â—‰</label>
  <label class="icon" for="pick" title="Choose a picture">ï¼‹</label>
  <!-- capture= asks the phone for its camera rather than the gallery -->
  <input id="cam" type="file" accept="image/*" capture="environment" hidden>
  <!-- Documents as well as pictures. accept="image/*" meant a phone would
       only offer the photo gallery, so a worksheet could not be sent at all
       from a phone - the one device you actually have the PDF on. -->
  <input id="pick" type="file" hidden
         accept="image/*,.pdf,.docx,.txt,.md,.csv,application/pdf">
  <input id="q" placeholder="Tell Athena what to do"
    autocomplete="off" autocapitalize="sentences"><button id="go">Send</button>
</form>
<script>
const log=document.getElementById('log'), f=document.getElementById('f'),
      q=document.getElementById('q'), go=document.getElementById('go'),
      dot=document.getElementById('dot');
document.getElementById('host').textContent=location.host;

// Token arrives once in the URL, then lives in storage so it stays out of
// browser history and server logs.
const p=new URLSearchParams(location.search);
if(p.get('t')){ localStorage.setItem('jarvis_token',p.get('t'));
  history.replaceState({},'',location.pathname); }
const token=()=>localStorage.getItem('jarvis_token')||'';

// Chrome will not offer to install without a service worker. This one
// deliberately caches nothing: Athena is useless without the PC anyway, and a
// stale cached shell would be worse than a clear "cannot reach the PC".
if('serviceWorker' in navigator){
  navigator.serviceWorker.register('/sw.js').catch(()=>{});
}

function add(text,cls){ const d=document.createElement('div');
  d.className=cls; d.textContent=text; log.appendChild(d);
  log.scrollTop=log.scrollHeight; return d; }

// A study answer comes back as a sentence plus [file.html]. The page itself
// is on the PC, so turn the reference into a link the phone can open - same
// rendering, equations and tables and all.
function addAnswer(text){
  const shared=[];
  text=(text||'').replace(/\s*\[share:([\w-]+)\]/g,function(_,id){shared.push(id);return '';});
  if(shared.length){
    const d=add(text,'msg bot');
    shared.forEach(function(id){
      const a=document.createElement('a');
      a.href='/get/'+encodeURIComponent(id)+'?t='+encodeURIComponent(token());
      a.setAttribute('download',''); a.className='page'; a.textContent='Download';
      d.appendChild(document.createElement('br')); d.appendChild(a);
    });
    return d;
  }
  const m=text.match(/\[([\w.\-]+\.html)\]$/m);
  if(!m){ return add(text,'msg bot'); }
  const d=add(text.replace(m[0],'').trim(),'msg bot');
  const a=document.createElement('a');
  a.href='/answers/'+encodeURIComponent(m[1])+'?t='+encodeURIComponent(token());
  a.target='_blank'; a.rel='noopener'; a.className='page';
  a.textContent='Open the full answer';
  d.appendChild(document.createElement('br'));
  d.appendChild(a);
  log.scrollTop=log.scrollHeight;
  return d;
}

// ---- pictures from the phone ----
let pending=null;                       // {dataUrl, name}
const chip=document.getElementById('chip');

function showChip(){
  chip.innerHTML='';
  if(!pending) return;
  const w=document.createElement('div'); w.className='att';
  const im=document.createElement('img'); im.src=pending.dataUrl;
  const t=document.createElement('span'); t.textContent=pending.name;
  const x=document.createElement('span'); x.className='x'; x.textContent='✕';
  x.onclick=()=>{ pending=null; showChip(); };
  w.append(im,t,x); chip.appendChild(w);
}

function pickImage(input){
  input.onchange=()=>{
    const file=input.files&&input.files[0];
    input.value='';
    if(!file) return;
    const fr=new FileReader();
    // A picture goes to the eyes, anything else to the reader. The phone
    // does not always set file.type for a document, so the extension is
    // the fallback.
    const name=file.name||'photo.jpg';
    const isImage=(file.type||'').startsWith('image/') ||
                  /\.(jpe?g|png|gif|webp|heic|bmp)$/i.test(name);
    fr.onload=()=>{ pending={dataUrl:fr.result,name:name,doc:!isImage};
                    showChip(); q.focus(); };
    fr.readAsDataURL(file);
  };
}
pickImage(document.getElementById('cam'));
pickImage(document.getElementById('pick'));

async function look(text,shot){
  go.disabled=true; dot.className='ring busy';
  const waiting=add('looking at the pictureâ€¦','trace');
  try{
    const r=await fetch('/look',{method:'POST',
      headers:{'Content-Type':'application/json','X-Token':token()},
      body:JSON.stringify({text:text,image:shot.dataUrl})});
    const j=await r.json();
    waiting.remove();
    if(!r.ok){ dot.className='ring bad';
      add(j.error||('HTTP '+r.status),'msg bot err'); return; }
    dot.className='ring';
    (j.steps||[]).forEach(s=>add('â†’ '+s.tool+'  '+s.result,'trace'));
    add(j.answer,'msg bot');
  }catch(e){ waiting.remove(); dot.className='ring bad';
    add('Cannot reach the PC. Is it awake and on Tailscale?','msg bot err'); }
  finally{ go.disabled=false; }
}

// ---------- which model answers class work ----------
const modelSel=document.getElementById('model');
async function loadModels(){
  try{
    const j=await api('/models');
    modelSel.innerHTML='';
    (j.models||[]).forEach(m=>{
      const o=document.createElement('option');
      o.value=m.model;
      o.textContent=m.label+(m.ready?'':'  - no key');
      o.disabled=!m.ready;
      if(m.live) o.selected=true;
      modelSel.appendChild(o);
    });
  }catch(e){ /* the header select is not worth an error bubble */ }
}
modelSel.onchange=async()=>{
  try{ const j=await api('/model',{method:'POST',
        body:JSON.stringify({model:modelSel.value})});
       add(j.said,'trace'); }
  catch(e){ add('Could not switch model.','msg bot err'); }
};

// ---------- saved conversations ----------
const menu=document.getElementById('menu');
document.getElementById('burger').onclick=()=>{ menu.classList.add('open'); loadChats(); };
document.getElementById('scrim').onclick=()=>menu.classList.remove('open');
document.getElementById('newchat').onclick=()=>switchChat('new');

async function api(path,opts){
  const r=await fetch(path,Object.assign({headers:{'X-Token':token(),
    'Content-Type':'application/json'}},opts||{}));
  if(!r.ok) throw new Error('HTTP '+r.status);
  return r.json();
}

async function loadChats(){
  const list=document.getElementById('chatlist');
  try{
    const j=await api('/chats');
    list.innerHTML='';
    (j.chats||[]).forEach(c=>{
      const row=document.createElement('div');
      row.className='chat'+(c.live?' live':'');
      const t=document.createElement('span'); t.className='t'; t.textContent=c.title;
      const w=document.createElement('span'); w.className='w'; w.textContent=c.when;
      const d=document.createElement('span'); d.className='d'; d.textContent='✕';
      t.onclick=w.onclick=()=>switchChat(c.id);
      d.onclick=async e=>{ e.stopPropagation();
        if(!d.dataset.sure){ d.dataset.sure=1; d.textContent='Delete?';
          setTimeout(()=>{ delete d.dataset.sure; d.textContent='✕'; },3000); return; }
        await api('/chats/'+c.id,{method:'DELETE'}); loadChats(); };
      row.append(t,w,d); list.appendChild(row);
    });
    if(!j.chats.length) list.innerHTML=
      '<div style="color:var(--dim);font-size:13px;padding:10px">Nothing saved yet.</div>';
  }catch(e){ list.innerHTML='<div class="msg bot err">Cannot reach the PC.</div>'; }
}

async function switchChat(id){
  try{
    const j=await api('/chats/'+id);
    log.innerHTML='';
    (j.messages||[]).forEach(m=>{
      if(m.role==='user') add(m.text,'msg me'); else addAnswer(m.text);
    });
    menu.classList.remove('open');
    q.focus();
  }catch(e){ add('Could not open that chat.','msg bot err'); }
}

// Show the conversation the PC left open, rather than an empty page.
switchChat('current').catch(()=>{});
loadModels();

async function readDoc(text,doc){
  go.disabled=true; dot.className='ring busy';
  const waiting=add('reading '+doc.name+'â€¦','trace');
  try{
    const r=await fetch('/read',{method:'POST',
      headers:{'Content-Type':'application/json','X-Token':token()},
      body:JSON.stringify({text:text,name:doc.name,data:doc.dataUrl})});
    const j=await r.json();
    waiting.remove();
    if(!r.ok){ dot.className='ring bad';
      add(j.error||('HTTP '+r.status),'msg bot err'); return; }
    dot.className='ring';
    addAnswer(j.answer);
  }catch(e){ waiting.remove(); dot.className='ring bad';
    add('Cannot reach the PC. Is it awake and on Tailscale?','msg bot err'); }
  finally{ go.disabled=false; }
}

async function send(text,force){
  go.disabled=true; dot.className='ring busy';
  const thinking=add('thinkingâ€¦','trace');
  try{
    const r=await fetch('/ask',{method:'POST',
      headers:{'Content-Type':'application/json','X-Token':token()},
      body:JSON.stringify({text:text,confirm:!!force})});
    const j=await r.json();
    thinking.remove();
    if(!r.ok){ dot.className='ring bad';
      add(j.error||('HTTP '+r.status),'msg bot err'); return; }
    dot.className='ring';
    (j.steps||[]).forEach(s=>add('â†’ '+s.tool+'  '+s.result,'trace'));
    if(j.needs_confirm){
      const box=add('','confirm');
      box.textContent=j.answer;
      const yes=document.createElement('button'); yes.textContent='Run anyway';
      const no=document.createElement('button'); no.textContent='Cancel'; no.className='no';
      yes.onclick=()=>{ box.remove(); send(text,true); };
      no.onclick=()=>{ box.remove(); };
      box.appendChild(document.createElement('br'));
      box.appendChild(yes); box.appendChild(no);
    } else { addAnswer(j.answer); }
  }catch(e){ thinking.remove(); dot.className='ring bad';
    add('Cannot reach the PC. Is it awake and on Tailscale?','msg bot err'); }
  finally{ go.disabled=false; q.focus(); }
}

f.onsubmit=e=>{ e.preventDefault();
  const t=q.value.trim();
  if(pending){
    const shot=pending;
    const said=t||(shot.doc?'Work through this and answer it.'
                           :'What is in this picture?');
    const mine=add(said,'msg me');
    if(shot.doc){
      const f=document.createElement('span'); f.className='file';
      f.textContent='ðŸ“„ '+shot.name; mine.appendChild(document.createElement('br'));
      mine.appendChild(f);
    } else {
      const im=document.createElement('img'); im.className='shot'; im.src=shot.dataUrl;
      mine.appendChild(im);
    }
    pending=null; showChip(); q.value='';
    if(shot.doc){ readDoc(said,shot); } else { look(said,shot); }
    return;
  }
  if(!t) return;
  add(t,'msg me'); q.value=''; send(t,false); };
</script></body></html>"""


# What the phone installs. "standalone" is what removes the browser bar and
# makes it open like any other app from the home screen.
MANIFEST = """{
  "name": "Athena",
  "short_name": "Athena",
  "description": "Your assistant, on the PC at home.",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "orientation": "portrait",
  "background_color": "#05080c",
  "theme_color": "#05080c",
  "icons": [
    {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
    {"src": "/icons/maskable-192.png", "sizes": "192x192", "type": "image/png",
     "purpose": "maskable"},
    {"src": "/icons/maskable-512.png", "sizes": "512x512", "type": "image/png",
     "purpose": "maskable"}
  ],
  "share_target": {
    "action": "/share-in", "method": "POST", "enctype": "multipart/form-data",
    "params": {"title": "title", "text": "text", "url": "url",
               "files": [{"name": "files", "accept": ["image/*", "application/pdf", ".pdf", ".docx", ".pptx",
                                                       ".txt", ".md", ".csv", "text/*"]}]}
  },
  "shortcuts": [
    {"name": "Talk to Athena", "short_name": "Talk", "url": "/?do=talk",
     "icons": [{"src": "/icons/icon-192.png", "sizes": "192x192"}]},
    {"name": "New mission", "short_name": "Mission", "url": "/?do=mission",
     "icons": [{"src": "/icons/icon-192.png", "sizes": "192x192"}]},
    {"name": "Today", "short_name": "Today", "url": "/?do=today",
     "icons": [{"src": "/icons/icon-192.png", "sizes": "192x192"}]},
    {"name": "Quiz me", "short_name": "Quiz", "url": "/?do=quiz",
     "icons": [{"src": "/icons/icon-192.png", "sizes": "192x192"}]}
  ]
}"""

# Chrome will not offer to install a site without one of these. It caches
# nothing on purpose - every useful thing here needs the PC, so serving a
# stale shell would only disguise the PC being unreachable.
SERVICE_WORKER = """self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(clients.claim()));
// Share to Athena: the share arrives as a POST with no token, so it is parked
// in a cache and the page - which has the token - picks it up and sends it on.
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'POST' || u.pathname !== '/share-in') return;
  e.respondWith((async () => {
    const f = await e.request.formData();
    const c = await caches.open('athena-share');
    const meta = {title: f.get('title') || '', text: f.get('text') || '', url: f.get('url') || '', files: []};
    let i = 0;
    for (const file of f.getAll('files')) {
      if (!file || !file.name) continue;
      const key = '/share-file/' + (i++);
      await c.put(key, new Response(file, {headers: {'Content-Type': file.type || 'application/octet-stream',
                                                     'X-Name': encodeURIComponent(file.name)}}));
      meta.files.push(key);
    }
    await c.put('/share-meta', new Response(JSON.stringify(meta), {headers: {'Content-Type': 'application/json'}}));
    return Response.redirect('/?shared=1', 303);
  })());
});
// Athena's own notifications (jarvis/webpush.py); a tap opens the app at d.url.
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data.json(); } catch (_) { d = {title: 'Athena', body: e.data ? e.data.text() : ''}; }
  e.waitUntil(self.registration.showNotification(d.title || 'Athena', {
    body: d.body || '', icon: '/icons/icon-192.png', badge: '/icons/maskable-192.png',
    tag: d.tag || undefined, renotify: !!d.tag, data: {url: d.url || '/'},
    requireInteraction: !!d.urgent, vibrate: d.urgent ? [250, 120, 250, 120, 500] : [120]}));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || '/', self.location.origin).href;
  e.waitUntil(clients.matchAll({type: 'window', includeUncontrolled: true}).then(cs => {
    for (const c of cs) {
      if (c.url.startsWith(self.location.origin) && 'focus' in c) {
        return c.focus().then(w => (w && w.navigate ? w.navigate(url) : w)).catch(() => clients.openWindow(url));
      }
    }
    return clients.openWindow(url);
  }));
});
"""


# ---------- the HUD's live panels ----------

_web_log: list[list[str]] = []          # typed commands, merged with voice's
_net_prev: dict = {}


def _log(line: str) -> None:
    _web_log.append([time.strftime("%H:%M:%S"), line])
    del _web_log[:-40]


_cpu = {"now": 0.0, "hist": []}
_status_seen = {"t": 0.0}


def _watched() -> bool:
    """True while a HUD has asked for readings in the last 15 seconds.

    The samplers used to run from the first HUD poll until the server
    exited - an nvidia-smi process every two seconds and a CPU reading every
    second, all day, with no window open to show them. They now stop when
    nobody is looking and start again on the next poll.
    """
    return time.time() - _status_seen["t"] < 15


def _cpu_sampler() -> None:
    """One CPU reading a second, shared by every open HUD.

    psutil measures CPU as the change since its previous call. With the
    phone, the desktop window and a browser all polling, calls landed
    milliseconds apart and each measured an interval too short to see any
    load - the panel sat at 0% on a busy machine.
    """
    import psutil

    while _watched():
        v = psutil.cpu_percent(interval=1.0)
        _cpu["now"] = v
        _cpu["hist"] = (_cpu["hist"] + [v])[-24:]
    _cpu["started"] = False


_gpu: dict = {"now": None}


def _gpu_sampler() -> None:
    """One nvidia-smi reading every two seconds, shared by every open HUD.

    Each nvidia-smi call took 77-104 ms here, so running it inside /status
    would add that to every poll from every open HUD. A missing or failing
    nvidia-smi leaves the reading at None and the HUD shows dashes.
    """
    import subprocess

    while _watched():
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,"
                 "memory.total,temperature.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            # First GPU only - the laptop has one; an iGPU doesn't show up here.
            util, used, total, temp = (float(x) for x in out.splitlines()[0].split(","))
            _gpu["now"] = {"util": round(util), "vram_used": round(used / 1024, 1),
                           "vram_total": round(total / 1024, 1), "temp": round(temp)}
        except Exception:
            _gpu["now"] = None
        time.sleep(2.0)
    _gpu["started"] = False


# ---------------------------------------------------------------- overview
# The Command Center's slower panels: today's timeline, memory, providers,
# weather. Reading them means the calendar, Gmail and a weather service, so
# they are gathered on a background thread every few minutes and the page is
# handed the last result at once - never a page load waiting on the network.
_overview_cache: dict = {"data": None, "t": 0.0}
_overview_lock = threading.Lock()


def _gather_overview() -> dict:
    import datetime as dt
    from .. import cloud
    from ..config import DATA

    out: dict = {"t": time.time()}
    # Today's timeline, each item marked done / now / next.
    try:
        from ..tools import brief
        events, mail = brief.collect(dt.date.today())
        now = dt.datetime.now()
        rows = []
        for e in events:
            s = brief._local(e["start"])
            end = brief._local(e["end"]) if e.get("end") else s
            state = ("now" if s <= now < end else "done" if end <= now else "next")
            rows.append({"what": e["what"], "at": "all day" if e["all_day"] else s.strftime("%H:%M"),
                         "until": end.strftime("%H:%M"), "where": e.get("where", ""),
                         "state": state, "mins": int((s - now).total_seconds() // 60)})
        # Teams classes, as read by tools/teams.py this morning (cancelled ones left out).
        try:
            from ..tools import teams
            seen = {r["what"].lower() for r in rows}
            for m in teams._meetings_today():
                if m["canceled"] or m["title"].lower() in seen:
                    continue
                s = dt.datetime.combine(dt.date.today(), dt.time.fromisoformat(m["start"]))
                e = dt.datetime.combine(dt.date.today(), dt.time.fromisoformat(m["end"]))
                rows.append({"what": m["title"], "at": m["start"], "until": m["end"], "where": "Teams",
                             "state": "now" if s <= now < e else "done" if e <= now else "next",
                             "mins": int((s - now).total_seconds() // 60)})
        except Exception:
            pass
        # UPES timetable (upes.py), from the last portal read - with rooms.
        try:
            from .. import upes
            seen = {r["what"].lower() for r in rows}
            for c in upes.classes(cached_only=True):
                if c["cancelled"] or c["course"].lower() in seen:
                    continue
                s = dt.datetime.combine(dt.date.today(), dt.time.fromisoformat(c["start"]))
                e = dt.datetime.combine(dt.date.today(), dt.time.fromisoformat(c["end"]))
                rows.append({"what": c["course"], "at": c["start"], "until": c["end"], "where": c["room"],
                             "state": "now" if s <= now < e else "done" if e <= now else "next",
                             "mins": int((s - now).total_seconds() // 60)})
        except Exception:
            pass
        rows.sort(key=lambda r: r["at"])
        out["schedule"], out["unread"] = rows, len(mail)
        # Senders only - brief gives "subject - from who"; the subject stays out.
        out["mail_from"] = [m.rsplit(" - from ", 1)[-1] for m in mail if " - from " in m][:4]
        tomorrow = brief._first_tomorrow()
        out["tomorrow"] = ({"what": tomorrow["what"],
                            "at": brief._local(tomorrow["start"]).strftime("%H:%M")}
                           if tomorrow else None)
    except Exception as e:
        out["schedule"], out["unread"], out["schedule_error"] = [], None, str(e)[:120]
    # Memory.
    try:
        from .. import db, profile
        items = [i for i in profile.load() if i.live()]
        out["memory"] = {
            "learned": len(items),
            "sections": {k: sum(1 for i in items if i.section == k) for k in profile.SECTIONS},
            "chats": len(list((DATA / "chats").glob("*.json"))),
            "snippets": db.count("chat"),
            "style": any(i.section == "style" for i in items),
        }
    except Exception:
        out["memory"] = None
    # Providers: a saved key is "connected"; Ollama lists what is pulled.
    prov = {}
    for name in ("groq", "gemini", "openrouter", "cerebras"):
        try:
            prov[name] = bool(cloud.get_key(name))
        except Exception:
            prov[name] = False
    try:
        import ollama
        prov["ollama"] = len(ollama.Client(host=cfg.get("llm.host")).list().models)
    except Exception:
        prov["ollama"] = 0
    out["providers"] = prov
    out["planner"] = cfg.get("planner.model", "") if cfg.get("planner.enabled", True) else ""
    try:
        from ..tools import routines
        out["routines"] = list(routines.load())[:8]
    except Exception:
        out["routines"] = []
    # Command Centre: who messaged (sender only), open deadlines, today's reminders.
    try:
        from .. import phone_notify
        out["senders"] = phone_notify.recent_senders()
        out["notify_muted"] = phone_notify.muted()
    except Exception:
        out["senders"] = []
    try:
        from ..tools import todo
        today = dt.date.today()
        due = sorted((t for t in todo.load() if not t["done"] and t.get("due")), key=lambda t: t["due"])
        out["deadlines"] = [{"text": t["text"][:60], "due": t["due"],
                             "days": (dt.date.fromisoformat(t["due"][:10]) - today).days} for t in due[:3]]
    except Exception:
        out["deadlines"] = []
    try:
        from ..tools.scheduler import _load as load_jobs
        rem = []
        for j in load_jobs():
            at = dt.datetime.fromisoformat(j["fire_at"])
            if at.date() == dt.date.today():
                rem.append({"what": j["text"][:50], "at": at.strftime("%H:%M"),
                            "mins": int((at - dt.datetime.now()).total_seconds() // 60)})
        out["reminders"] = sorted(rem, key=lambda r: r["at"])
    except Exception:
        out["reminders"] = []
    out["tools"] = len(registry.REGISTRY)
    out["city"] = cfg.get("assistant.city", "Dehradun")
    try:
        from ..tools.web import get_weather
        w = get_weather(out["city"])
        out["weather"] = "" if w.startswith("ERROR") else w.split("\n")[0].split(": ", 1)[-1][:60]
    except Exception:
        out["weather"] = ""
    return out


def _overview() -> dict:
    """The cached overview, refreshed in the background when stale. Automation
    activity is read fresh - it is a small local file."""
    data = dict(_overview_cached())
    try:
        from .. import rules
        data["activity"] = rules.recent_activity(6)
        data["auto_paused"] = rules.paused()
    except Exception:
        pass
    try:                                     # live attendance (upes.py), from its last read
        from .. import upes
        data["attendance"] = upes.attendance(cached_only=True)
        from ..store import load_json as _lj
        data["upes_read"] = _lj(upes.CACHE, {}).get("t", 0)
        # Signed out is normal (MyUPES ends sign-ins ~25 min in); only stale data is worth showing.
        data["upes_signed_out"] = ((upes.DATA / "upes_signed_out.json").exists()
                                   and time.time() - data["upes_read"] > 20 * 3600)
    except Exception:
        pass
    try:                                       # the phone's Today card: goal tasks left, missions at work
        from .. import goals, missions
        data["goal_today"] = [{"task": t["task"], "goal": g["title"]} for g in goals.load() if g["status"] == "active"
                              for t in goals.today_tasks(g) if not t["done"]][:4]
        data["missions"] = [{"title": m["title"], "status": m["status"]} for m in missions.load()
                            if not m.get("parent") and m["status"] in ("running", "starting")]
        from .. import attention
        data["needs"] = [{"who": i["who"], "title": i["title"]} for i in attention.items()][:5]
    except Exception:
        pass
    return data


def _overview_cached() -> dict:
    from .views import _sources_changed
    fresh = time.time() - _overview_cache["t"] < 180 and _overview_cache["t"] > _sources_changed()
    if not fresh and _overview_lock.acquire(blocking=False):
        def work():
            try:
                _overview_cache["data"] = _gather_overview()
                _overview_cache["t"] = time.time()
            finally:
                _overview_lock.release()
        threading.Thread(target=work, daemon=True, name="overview").start()
    return _overview_cache["data"] or {}


def _system_status() -> dict:
    """Real readings for the side panels - nothing on the HUD is decoration."""
    import psutil

    _status_seen["t"] = time.time()
    if not _cpu.get("started"):
        _cpu["started"] = True
        threading.Thread(target=_cpu_sampler, daemon=True, name="cpu").start()
    if not _gpu.get("started"):
        _gpu["started"] = True
        threading.Thread(target=_gpu_sampler, daemon=True, name="gpu").start()

    vm = psutil.virtual_memory()
    try:
        disk = psutil.disk_usage("C:\\")
    except Exception:
        disk = None
    bat = psutil.sensors_battery()

    # Network speed is a difference between two readings, so remember the last.
    now = time.time()
    io = psutil.net_io_counters()
    up = down = 0.0
    if _net_prev:
        dt = max(0.001, now - _net_prev["t"])
        up = (io.bytes_sent - _net_prev["s"]) / dt
        down = (io.bytes_recv - _net_prev["r"]) / dt
    _net_prev.update(t=now, s=io.bytes_sent, r=io.bytes_recv)

    from ..tools import study
    return {
        "cpu": _cpu["now"],
        "cpu_hist": _cpu["hist"],
        "cores": psutil.cpu_count(),
        "gpu": _gpu["now"],     # {util %, vram_used/total GB, temp C} or None
        "mem_used": round(vm.used / 2**30, 1),
        "mem_total": round(vm.total / 2**30, 1),
        "mem_pct": vm.percent,
        "disk_pct": disk.percent if disk else 0,
        "disk_free": round(disk.free / 2**30) if disk else 0,
        "battery": round(bat.percent) if bat else None,
        "plugged": bool(bat.power_plugged) if bat else None,
        "up_kbs": round(up / 1024, 1),
        "down_kbs": round(down / 1024, 1),
        "uptime_h": round((now - psutil.boot_time()) / 3600, 1),
        "study_model": study.current_model(),
        "everyday_model": cfg.get("llm.model", ""),
        "procs": len(psutil.pids()),
        "user": cfg.get("assistant.user_name", "OPERATOR"),
        "model_on": _where_model_runs(),
    }


_where = {"t": 0.0, "v": "unknown"}


def _where_model_runs() -> str:
    """GPU, CPU or mixed - checked every ten seconds, not on every poll."""
    if time.time() - _where["t"] > 10:
        try:
            from ..llm import LLM
            _where["v"] = LLM().processor()
        except Exception:
            _where["v"] = "unknown"
        _where["t"] = time.time()
    return _where["v"]


def _live_payload() -> dict:
    voice = live.read_voice()
    merged = sorted(voice.get("log", []) + _web_log, key=lambda e: e[0])[-40:]
    voice["log"] = merged
    voice["time"] = time.strftime("%H:%M:%S")
    from .. import modes
    voice["mode"] = modes.current()          # read here: data/mode.json is shared by every process
    voice["jobs"] = _jobs()
    return voice


_JOBS: dict = {"t": 0.0, "v": []}


def _jobs() -> list[dict]:
    """What is working in the background, for the HUD's timer chips: Deck
    consoles Athena started on a task, and running missions (Hedwig's
    "Claude Code · Building your website · 11s", 23 Oct 2026). /live is polled
    every 300 ms, so this is worked out at most every 2 s."""
    if time.time() - _JOBS["t"] < 2:
        return _JOBS["v"]
    out: list[dict] = []
    try:
        from .. import deck_link
        out += [{"who": j["who"], "title": j["title"], "since": j["started"], "state": j["state"], "console": j["id"]}
                for j in deck_link.jobs() if deck_link.job_active(j)]
    except Exception:
        pass
    try:
        from .. import missions
        out += [{"who": missions.WORKERS.get(m.get("worker"), "Athena"), "title": m["title"], "since": m["started"],
                 "state": m["status"], "mission": m["id"]}
                for m in missions.load() if m["status"] in ("running", "starting") and not m.get("parent") and not m.get("loop")]
    except Exception:
        pass
    _JOBS.update(t=time.time(), v=out)
    return out


def _hud_page() -> bytes:
    """Read on every request, so editing the page needs no restart."""
    page = Path(__file__).parent / "hud.html"
    try:
        return page.read_bytes()
    except OSError:
        return PAGE.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "Athena"

    def log_message(self, *args) -> None:      # noqa: D102 - quieter console
        pass

    # ---------- helpers ----------

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass        # the page hung up first (Stop, a closed window): nothing to deliver to

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _authorised(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Token", ""), get_token())

    def _orb_ok(self) -> bool:
        """The desk orb's own key (data/orb_key.txt): it can read Athena's
        state and press "listen", nothing else - never the full token."""
        return secrets.compare_digest(self.headers.get("X-Orb-Key", ""), orb_key())

    # ---------- routes ----------

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, _hud_page(), "text/html; charset=utf-8")
        elif path == "/classic":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/stage":
            # The floating panel (ui/stage_window.py); like the HUD, no data in the page itself.
            self._send(200, (Path(__file__).parent / "stage.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/live":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            self._json(200, _live_payload())
        elif path == "/orb":
            # The desk orb (C:\claude code\orb) polls this a few times a second.
            if not self._orb_ok():
                self._json(401, {"error": "Bad or missing orb key."})
                return
            v = live.read_voice()
            from .. import modes
            self._json(200, {"s": v.get("state", "idle"), "l": round(float(v.get("level") or 0), 2),
                             "m": modes.current()})
        elif path == "/status":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            self._json(200, _system_status())
        elif path.startswith("/view/"):
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            from . import views
            fn = views.GET.get(path[len("/view/"):])
            if fn is None:
                self._json(404, {"error": "No such page."})
                return
            try:
                self._json(200, fn())
            except Exception as e:
                self._json(500, {"error": str(e)[:200]})
        elif path == "/overview":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            self._json(200, _overview())
        elif path == "/health":
            self._json(200, {"ok": True, "tools": len(registry.REGISTRY)})
        elif path == "/manifest.webmanifest":
            # Unauthenticated on purpose: the browser fetches this before the
            # page can hand it a token, and it says nothing secret.
            self._send(200, MANIFEST.encode("utf-8"),
                       "application/manifest+json; charset=utf-8")
        elif path == "/sw.js":
            self._send(200, SERVICE_WORKER.encode("utf-8"),
                       "application/javascript; charset=utf-8")
        elif path in ("/face.js", "/hud.js", "/hud.css", "/neuron-brain.js", "/stage.js", "/stage.css"):
            # The HUD's code and styles (no data in them, so no token): served
            # from disk, so an edit only needs a page reload. hud.js and hud.css
            # came out of hud.html on 16 Oct 2026 (it had grown past 200 KB).
            self._send(200, (Path(__file__).parent / path[1:]).read_bytes(),
                       "text/css; charset=utf-8" if path.endswith(".css") else "application/javascript; charset=utf-8")
        elif path.startswith("/icons/"):
            self._serve_icon(path)
        elif path == "/models":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            from ..tools import study
            self._json(200, {"models": study.choices()})
        elif path == "/chats":
            self._list_chats()
        elif path == "/projects":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            self._json(200, {"projects": _projects()})
        elif path.startswith("/chats/") and path.endswith("/export"):
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            cid = Path(path[len("/chats/"):-len("/export")]).name
            if cid == "current":
                cid = _agent.memory.chat_id or ""
            text = chats.export_md(cid)
            name = re.sub(r"[^\w\- ]+", "", chats.meta(cid).get("title") or "chat").strip()[:60] or "chat"
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{name}.md"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path.startswith("/chats/"):
            self._open_chat(path[len("/chats/"):])
        elif path.startswith("/answers/"):
            self._serve_answer(path)
        elif path.startswith("/mission/"):
            self._serve_mission(path)
        elif path == "/shares":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            from .. import share
            self._json(200, {"shares": share.pending(), "folder": str(share.receive_folder())})
        elif path.startswith("/get/"):
            self._serve_share(path[len("/get/"):])
        else:
            self._json(404, {"error": "not found"})

    # ---------- files between the PC and the phone ----------

    def _range_start(self, size: int):
        """Answers a Range request's status line and range headers; returns
        (start, end) to send, or None after a 416. Ranges: a big video resumes
        after a dropped connection and a player can seek - and phone/WebView
        players won't play a video at all from a server that ignores them."""
        start, end = 0, size - 1
        rng = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", "") or "")
        if rng and (rng.group(1) or rng.group(2)):
            if rng.group(1):
                start = int(rng.group(1))
                end = min(int(rng.group(2)), size - 1) if rng.group(2) else size - 1
            else:
                start = max(0, size - int(rng.group(2)))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return None
        self.send_response(206 if rng and (start, end) != (0, size - 1) else 200)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if (start, end) != (0, size - 1):
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        return start, end

    def _serve_share(self, share_id: str) -> None:
        """A file the PC shared with the phone (jarvis/share.py).

        Like /answers, a link the phone's browser follows, so the token comes
        in the query string. Both it and the share id must be right, and the
        file is checked again now - still there, still allowed.
        """
        import urllib.parse

        from .. import share

        query = urllib.parse.parse_qs(self.path.partition("?")[2])
        if not secrets.compare_digest((query.get("t") or [""])[0], get_token()):
            self._send(401, b"Bad or missing token.", "text/plain; charset=utf-8")
            return
        target = share.resolve(urllib.parse.unquote(share_id))
        if target is None:
            self._send(404, b"That file is no longer shared - ask Athena to send it again.",
                       "text/plain; charset=utf-8")
            return
        import mimetypes

        size = target.stat().st_size
        quoted = urllib.parse.quote(target.name)
        ascii_name = target.name.encode("ascii", "replace").decode().replace('"', "_")
        # The real type, not application/octet-stream: with nosniff the phone
        # saved a video as unknown data and showed "no preview" (2 Oct 2026).
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        span = self._range_start(size)
        if span is None:
            return
        start, end = span
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition",
                         f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        left = end - start + 1
        with open(target, "rb") as f:
            f.seek(start)
            while left > 0:
                block = f.read(min(1 << 20, left))
                left -= len(block)
                if not block:
                    break
                try:
                    self.wfile.write(block)
                except (BrokenPipeError, ConnectionResetError):
                    return                      # the phone gave up; nothing to clean up
        _log(f"phone downloaded {target.name}")

    def _receive_upload(self) -> None:
        """A file sent from the phone, streamed straight to disk (share.receive)."""
        import urllib.parse

        from .. import share

        if not self._authorised():
            self._json(401, {"error": "Bad or missing token."})
            return
        query = urllib.parse.parse_qs(self.path.partition("?")[2])
        name = (query.get("name") or ["file"])[0]
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        try:
            saved = share.receive(self.rfile, length, name)
        except share.UploadError as e:
            self._json(e.status, {"error": str(e)})
            return
        except OSError as e:
            self._json(500, {"error": f"Could not save it on the PC: {e}"})
            return
        _log(f"received from phone: {saved.name} ({share.human_size(saved.stat().st_size)})")
        self._json(200, {"saved": saved.name, "folder": str(saved.parent),
                         "size": share.human_size(saved.stat().st_size)})

    # ---------- saved conversations ----------

    def _list_chats(self) -> None:
        if not self._authorised():
            self._json(401, {"error": "Bad or missing token."})
            return
        q = urllib.parse.parse_qs(self.path.partition("?")[2]).get("q", [""])[0].strip()
        found = chats.search(q) if q else chats.listing()[:200]
        self._json(200, {"chats": [
            {**c, "when": chats.when(c["updated"]),
             "live": c["id"] == _agent.memory.chat_id}
            for c in found], "projects": _projects(), "query": q})

    def _open_chat(self, chat_id: str) -> None:
        """Switch to a saved conversation and hand back its messages."""
        if not self._authorised():
            self._json(401, {"error": "Bad or missing token."})
            return
        chat_id = Path(chat_id).name
        with _lock:
            if chat_id == "current":
                if _agent.memory.stale():       # back after a break: a fresh chat, not the old one
                    _agent.memory.start_chat()
            elif chat_id == "new":
                pid = urllib.parse.parse_qs(self.path.partition("?")[2]).get("project", [""])[0]
                _agent.memory.start_chat(project=pid if chats.project(pid) else "")
            elif not chats.load(chat_id):
                self._json(404, {"error": "No such chat."})
                return
            else:
                _agent.memory.start_chat(chat_id)
            history = [{"role": m.get("role"), "text": m.get("content")}
                       for m in _agent.memory.full
                       if m.get("role") in ("user", "assistant")
                       and isinstance(m.get("content"), str)
                       and m["content"].strip()]
        self._json(200, {"id": _agent.memory.chat_id, "messages": history})

    def _serve_icon(self, path: str) -> None:
        name = Path(path[len("/icons/"):]).name
        icon = Path(__file__).parent / "icons" / name
        if icon.suffix != ".png" or not icon.is_file():
            self._json(404, {"error": "not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(icon.stat().st_size))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(icon.read_bytes())

    def do_HEAD(self) -> None:
        """Answer a HEAD like the GET it mirrors, minus the body.

        BaseHTTPRequestHandler returns 501 without this, and reverse proxies
        and install checks probe with HEAD before they will trust a site.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

    def _look(self) -> None:
        """A photo plus a question about it - from the phone, or this PC's webcam.

        The phone's own camera does the capturing - `capture` on a file input
        opens it directly - so the picture arrives here as base64 and only
        needs handing to the vision model.

        Desktop Chrome ignores `capture` and shows a file dialog instead, so
        the HUD on the PC sends `{"webcam": true}` with no image and the frame
        is taken here with ffmpeg, the same path the voice `look` tool uses.
        Capturing server-side rather than with getUserMedia means no camera
        permission prompt in the `--app` window, and it keeps working if that
        Chrome profile ever has the permission denied.
        """
        if not self._authorised():
            self._json(401, {"error": "Bad or missing token."})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 12 * 1024 * 1024:
                self._json(413, {"error": "That image is too large (12 MB limit)."})
                return
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            self._json(400, {"error": f"Could not read the upload: {e}"})
            return

        raw = (payload.get("image") or "").split(",")[-1]
        question = (payload.get("text") or "").strip()
        if not raw and payload.get("webcam"):
            self._look_webcam(question)
            return
        if not raw:
            self._json(400, {"error": "No image was sent."})
            return

        import base64

        vision.CAPTURES.mkdir(parents=True, exist_ok=True)
        name = f"phone_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
        shot = vision.CAPTURES / name
        try:
            shot.write_bytes(base64.b64decode(raw))
        except Exception as e:
            self._json(400, {"error": f"That image did not decode: {e}"})
            return

        with _lock:
            answer = vision.describe(shot, question)
        self._json(200, {"answer": answer, "steps": [
            {"tool": "look", "result": f"{name} ({shot.stat().st_size // 1024} KB)"}]})

    def _look_webcam(self, question: str) -> None:
        """Take a frame from the PC's webcam and ask the vision model about it."""
        import base64

        # Under the lock as well: ffmpeg holds the camera, and two overlapping
        # dshow opens of the same device fail with "Could not run graph".
        with _lock:
            shot = vision.capture()
            if isinstance(shot, str):
                self._json(200, {"answer": shot, "steps": []})
                return
            answer = vision.describe(shot, question)
        # The frame goes back too, so the transcript shows what the camera
        # actually saw - the same thumbnail an attached photo gets.
        image = "data:image/jpeg;base64," + base64.b64encode(shot.read_bytes()).decode()
        self._json(200, {"answer": answer, "image": image, "steps": [
            {"tool": "look", "result": f"{shot.name} ({shot.stat().st_size // 1024} KB)"}]})

    def _read_document(self) -> None:
        """A PDF, Word file or text file sent from the phone.

        The phone is where you actually have the worksheet, and until now it
        could only send pictures - `accept="image/*"` meant the file picker
        offered the photo gallery and nothing else. This takes the document,
        pulls the text out of it and gives it the same treatment the desktop
        does: worked through question by question and rendered to a page.
        """
        if not self._authorised():
            self._json(401, {"error": "Bad or missing token."})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 25 * 1024 * 1024:
                self._json(413, {"error": "That file is too large (25 MB limit)."})
                return
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            self._json(400, {"error": f"Could not read the upload: {e}"})
            return

        raw = (payload.get("data") or "").split(",")[-1]
        question = (payload.get("text") or "").strip()
        # Only the file name, never a path: the phone supplies this, and
        # "../../something" must not escape the uploads folder.
        name = Path(payload.get("name") or "attachment").name
        if not raw:
            self._json(400, {"error": "No file was sent."})
            return

        import base64

        uploads = DATA / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        saved = uploads / f"{time.strftime('%Y%m%d_%H%M%S')}_{name}"
        try:
            saved.write_bytes(base64.b64decode(raw))
        except Exception as e:
            self._json(400, {"error": f"That file did not decode: {e}"})
            return

        body = extract_text(saved).strip()
        if not body:
            self._json(200, {"answer": f"I could not read any text out of {name}. "
                                       f"If it is a scan, send it as a photo instead "
                                       f"and I will look at it.", "steps": []})
            return

        # Same ceiling the desktop uses: roughly what fits alongside an answer.
        limit = 20000
        clipped = body[:limit] + ("\n[...truncated]" if len(body) > limit else "")
        with _lock:
            answer = answer_page(question, clipped, name)
        self._json(200, {"answer": answer, "steps": [
            {"tool": "read", "result": f"{name} ({len(body)} characters)"}]})

    def _serve_answer(self, path: str) -> None:
        """Serve a rendered answer page to the phone.

        The token has to travel in the query string here: this is a link the
        phone browser follows, and a navigation cannot carry the X-Token
        header the rest of the interface uses.
        """
        import urllib.parse

        query = urllib.parse.parse_qs(self.path.partition("?")[2])
        supplied = (query.get("t") or [""])[0]
        if not secrets.compare_digest(supplied, get_token()):
            self._send(401, b"Bad or missing token.", "text/plain; charset=utf-8")
            return

        # Only ever a file inside the answers folder: taking the bare name
        # stops "../../" from reaching anything else on the disk.
        name = Path(urllib.parse.unquote(path[len("/answers/"):])).name
        target = (render.ANSWERS / name).resolve()
        if target.parent != render.ANSWERS.resolve() or not target.is_file():
            self._send(404, b"No such answer.", "text/plain; charset=utf-8")
            return
        self._send(200, target.read_bytes(), "text/html; charset=utf-8")

    def _serve_mission(self, path: str) -> None:
        """A file a mission made: /mission/<id>/<token>/<path in its folder>.

        The token sits in the path, not the query, so a built site's relative
        links (style.css, app.js) carry it too. Pages are served sandboxed - an
        origin of their own - so code Claude wrote can't read the HUD's token
        or call Athena's API.
        """
        import mimetypes
        import urllib.parse

        from .. import missions

        parts = urllib.parse.unquote(path).split("/", 4)          # "", "mission", id, token, rest
        if len(parts) < 4 or not secrets.compare_digest(parts[3], get_token()):
            self._send(401, b"Bad or missing token.", "text/plain; charset=utf-8")
            return
        m = next((x for x in missions.load() if x["id"] == parts[2]), None)
        if m is None:
            self._send(404, b"No such mission.", "text/plain; charset=utf-8")
            return
        root = Path(m["folder"]).resolve()
        target = (root / (parts[4] if len(parts) > 4 and parts[4] else (m.get("preview") or "index.html"))).resolve()
        if root not in target.parents or not target.is_file():   # never outside the mission's folder
            self._send(404, b"No such file.", "text/plain; charset=utf-8")
            return
        body = target.read_bytes()
        span = self._range_start(len(body))
        if span is None:
            return
        body = body[span[0]:span[1] + 1]
        # .md and friends as plain text: readable on the phone instead of a download
        ctype = ("text/plain; charset=utf-8" if target.suffix.lower() in (".md", ".txt", ".csv")
                 else mimetypes.guess_type(target.name)[0] or "text/plain; charset=utf-8")
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "sandbox allow-scripts allow-forms allow-popups allow-modals")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def do_DELETE(self) -> None:
        route = self.path.split("?")[0]
        if not route.startswith("/chats/"):
            self._json(404, {"error": "not found"})
            return
        if not self._authorised():
            self._json(401, {"error": "Bad or missing token."})
            return
        chat_id = Path(route[len("/chats/"):]).name
        with _lock:
            gone = chats.delete(chat_id)
            if chat_id == _agent.memory.chat_id:
                _agent.memory.start_chat()
        self._json(200, {"deleted": gone, "id": _agent.memory.chat_id})

    def do_POST(self) -> None:
        route = self.path.split("?")[0]
        if route == "/ask/stop":                    # the Stop button: no lock, the answer holds it
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            _agent.cancelled = True
            self._json(200, {"ok": True})
            return
        if route in ("/chats/rewind", "/projects", "/projects/delete") or (
                route.startswith("/chats/") and route.endswith("/project")):
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                body = {}
            if route == "/chats/rewind":            # Edit / Regenerate: cut back to before question n
                with _lock:
                    ok = _agent.memory.rewind(int(body.get("n", -1)), str(body.get("text", "")))
                self._json(200 if ok else 400, {"ok": ok})
            elif route == "/projects":
                self._json(200, {"project": chats.save_project(str(body.get("name", "")), str(body.get("notes", "")),
                                                               str(body.get("id", "")))})
            elif route == "/projects/delete":
                self._json(200, {"ok": chats.delete_project(str(body.get("id", "")))})
            else:                                   # /chats/<id>/project {"project": id or ""}
                cid = Path(route[len("/chats/"):-len("/project")]).name
                pid = str(body.get("project", ""))
                ok = (not pid or bool(chats.project(pid))) and chats.update(cid, project=pid)
                if ok and cid == _agent.memory.chat_id:
                    _agent.memory.project = pid
                self._json(200 if ok else 404, {"ok": bool(ok)})
            return
        if route.startswith("/chats/") and route.rsplit("/", 1)[-1] in ("rename", "pin"):
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            chat_id, what = Path(route[len("/chats/"):].rsplit("/", 1)[0]).name, route.rsplit("/", 1)[-1]
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                body = {}
            ok = (chats.rename(chat_id, str(body.get("title", ""))) if what == "rename"
                  else chats.pin(chat_id, bool(body.get("on"))))
            self._json(200 if ok else 404, {"ok": ok})
            return
        if route.startswith("/chats/") and route.endswith("/restore"):      # Undo a delete
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            chat_id = Path(route[len("/chats/"):-len("/restore")]).name
            self._json(200, {"restored": chats.restore(chat_id)})
            return
        if route == "/orb/listen":                  # the orb's button: same as the HUD's mic button
            if not self._orb_ok():
                self._json(401, {"error": "Bad or missing orb key."})
                return
            live.request_wake()
            self._json(200, {"ok": True})
            return
        if route == "/look":
            self._look()
            return
        if route == "/read":
            self._read_document()
            return
        if route.startswith("/view/"):
            # Buttons on the Command Center pages: run a routine, control the
            # music, add or tick off a task, forget a memory.
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                self._json(400, {"error": "bad request"})
                return
            from . import views
            page = route[len("/view/"):]
            try:
                if page == "music":
                    out = views.music_action(body.get("action", ""))
                elif page == "routine":
                    out = views.run_routine(body.get("name", ""))
                elif page == "tasks":
                    out = views.task_action(body)
                elif page == "rules":                     # the Automations page: switch one off, remove one
                    out = views.rule_action(body)
                elif page == "goals":                     # the Goals page: tick a task, replan, drop
                    out = views.goal_action(body)
                elif page == "missions":                  # the Missions page: start, stop, follow up, open folder
                    out = views.mission_action(body)
                elif page == "alarm":
                    from .. import alarm
                    out = {"said": alarm.snooze(int(body.get("minutes", 10))) if body.get("action") == "snooze"
                           else alarm.stop()}
                elif page == "automation":
                    from .. import rules
                    out = {"said": rules.pause_automation() if body.get("pause") else rules.resume_automation(),
                           "paused": rules.paused()}
                elif page == "notify":
                    from .. import phone_notify
                    out = phone_notify.set_muted(body.get("muted", True))
                elif page == "forget":
                    out = views.forget(body.get("text", ""))
                elif page == "phone":
                    out = views.phone_action(body)
                else:
                    self._json(404, {"error": "No such action."})
                    return
            except Exception as e:
                out = {"said": f"ERROR: {e}"}
            _log(f"page action: {page} {body.get('action') or body.get('name') or ''}".strip())
            self._json(200, out)
            return
        if route.startswith("/projects/") and route.endswith("/files"):      # add a file to a project
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            pid = Path(route[len("/projects/"):-len("/files")]).name
            name = urllib.parse.parse_qs(self.path.partition("?")[2]).get("name", ["file"])[0]
            size = int(self.headers.get("Content-Length") or 0)
            if size > chats.MAX_FILE:
                self._json(413, {"error": f"{name}: over 30 MB."})
                return
            try:
                self._json(200, {"file": chats.add_file(pid, name, self.rfile.read(size))})
            except ValueError as e:
                self._json(400, {"error": str(e)})
            return
        if route.startswith("/projects/") and route.endswith("/files/delete"):
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            pid = Path(route[len("/projects/"):-len("/files/delete")]).name
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                body = {}
            self._json(200, {"ok": chats.remove_file(pid, str(body.get("name", "")))})
            return
        if route == "/upload":
            self._receive_upload()
            return
        if route in ("/push/key", "/push/subscribe", "/push/test"):     # the phone app's own notifications
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            from .. import webpush
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if route == "/push/key":
                    self._json(200, {"key": webpush.public_key()})
                elif route == "/push/subscribe":
                    n = webpush.subscribe(body.get("sub") or {}, str(body.get("label") or ""))
                    self._json(200, {"ok": True, "phones": n})
                else:
                    n = webpush.send("Athena", "Notifications from the Athena app are on.", "/", tag="test")
                    self._json(200, {"ok": bool(n), "sent": n})
            except ValueError as e:
                self._json(400, {"error": str(e)})
            except Exception as e:
                self._json(500, {"error": str(e)[:200]})
            return
        if route == "/voice/start":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            self._json(200, {"said": start_voice()})
            return
        if route == "/wake":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            live.request_wake()
            _log("mic: listening for your next sentence")
            self._json(200, {"ok": True})
            return
        if route == "/model":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            try:
                n = int(self.headers.get("Content-Length", 0))
                wanted = json.loads(self.rfile.read(n) or b"{}").get("model", "")
            except Exception:
                self._json(400, {"error": "bad request"})
                return
            from ..tools import study
            self._json(200, {"said": study.set_model(wanted),
                             "model": study.current_model()})
            return
        if route in ("/phone/hear", "/phone/say"):      # phone voice: the voice process hears and speaks
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            from .. import phonevoice
            n = int(self.headers.get("Content-Length") or 0)
            if n > phonevoice.MAX_AUDIO:
                self._json(413, {"error": "That recording is too long."})
                return
            body = self.rfile.read(n)
            try:
                if route == "/phone/hear":
                    self._send(200, phonevoice.call("/stt", body, self.headers.get("Content-Type", "")),
                               "application/json")
                else:
                    self._send(200, phonevoice.call("/tts", body, "application/json"), "audio/wav")
            except RuntimeError as e:
                self._json(503, {"error": str(e)})
            return
        if route != "/ask":
            self._json(404, {"error": "not found"})
            return
        if not self._authorised():
            self._json(401, {"error": "Bad or missing token. Reopen the link from your PC."})
            return

        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json(400, {"error": "bad request"})
            return

        text = (payload.get("text") or "").strip()
        if not text:
            self._json(400, {"error": "empty request"})
            return

        global _allow_destructive
        with _lock:
            _last_steps.clear()
            _allow_destructive = bool(payload.get("confirm"))
            blocked: list[str] = []

            def confirm(name: str, args: dict) -> bool:
                if _allow_destructive:
                    return True
                blocked.append(f"{name}({', '.join(f'{k}={v}' for k, v in args.items())})")
                return False

            _agent.confirm = confirm
            _log(f"typed: {text[:70]}")
            from .. import share
            # Typed on the phone (or the HUD): "send me X" means this device,
            # and it is already on screen, so no push is needed.
            share.from_phone = True
            try:
                answer = _agent.ask(text)
                _log(f"replied: {answer.splitlines()[0][:70] if answer else ''}")
            except Exception as e:
                self._json(500, {"error": f"Athena failed: {e}"})
                return
            finally:
                share.from_phone = False

            if blocked:
                self._json(200, {
                    "answer": f"This needs confirmation: {blocked[0]}",
                    "needs_confirm": True,
                    "steps": list(_last_steps),
                })
                return
            self._json(200, {"answer": answer, "steps": list(_last_steps)})



# ---------------------------------------------------------------- voice supervisor
# Voice is the process that hears you, and nothing restarted it when it died:
# on 25 Sep 2026 Windows closed its hidden console as "not responding" at
# 14:30 and the HUD said VOICE OFFLINE for twenty minutes until someone asked
# why. The web server is always running, so it keeps an eye on voice.

def _voice_pids() -> list[int]:
    import psutil
    out = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if "python" in (p.info["name"] or "").lower() and "run.py" in cmd and " voice" in cmd:
                out.append(p.pid)
        except Exception:
            continue
    return out


def _projects() -> list[dict]:
    """Projects with their files, for the chat list."""
    from .. import goals
    out = []
    for p in chats.projects():
        g = goals.for_project(p["id"])
        out.append({**p, "files": chats.project_files(p["id"]), "goal": {
            "title": g["title"], "pct": round(100 * sum(t["done"] for t in g["tasks"]) / max(1, len(g["tasks"]))),
            "today_left": sum(1 for t in goals.today_tasks(g) if not t["done"])} if g else None})
    return out


def start_voice() -> str:
    """Launch the voice process, windowless, if it is not already running."""
    import subprocess
    import sys

    if _voice_pids():
        return "Voice is already running."
    try:
        (DATA / "live_voice.json").unlink(missing_ok=True)   # clear a "stopped" mark
    except OSError:
        pass
    pyw = Path(sys.executable).with_name("pythonw.exe")
    root = Path(__file__).resolve().parents[2]
    subprocess.Popen([str(pyw if pyw.exists() else sys.executable), "run.py", "voice"], cwd=str(root),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _log("voice: starting")
    return "Starting voice - give it about 20 seconds to load."


def _supervise_voice() -> None:
    time.sleep(40)                      # let autostart bring it up first
    while True:
        try:
            if cfg.get("voice.supervise", True) and not _voice_pids():
                if live.read_voice().get("state") != "stopped":
                    _log("voice was not running - restarting it")
                    start_voice()
                    time.sleep(60)      # give it time to load before checking again
        except Exception:
            pass
        time.sleep(20)


def run() -> None:
    global _agent

    port = int(cfg.get("web.port", 8080))
    token = get_token()

    # A question asked from the phone should not make a browser window appear
    # on the PC, possibly in another room. The page is still written, and the
    # phone gets a link to it.
    render.AUTO_OPEN = False

    threading.Thread(target=_supervise_voice, daemon=True, name="voice-supervisor").start()
    try:
        from ..tools import taste
        taste.start_sampler()     # what you listen to, for "play something"
        from .. import deadlines
        deadlines.start()          # assignment dates from mail + calendar into Tasks
    except Exception:
        pass
    _log("core online: web interface up")
    _log(f"everyday model: {cfg.get('llm.model', '')}")
    _agent = Agent(on_step=lambda s: _last_steps.append(
        {"tool": s.tool, "result": s.result.splitlines()[0][:70] if s.result else ""}))
    # Phone conversations are saved too, and share the same store as the
    # desktop window - a chat started on the phone is there on the PC.
    recent = chats.listing()
    _agent.memory.start_chat(recent[0]["id"] if recent else None)

    ok, msg = _agent.llm.available()
    if not ok:
        print(msg)
        return

    from ..tools import scheduler
    scheduler.start_scheduler("web")

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Athena web interface on port {port}")
    print("\nOpen this on your phone (the token is in the link, once):\n")
    https = _tailscale_https()
    if https:
        # Named first: a browser will only offer to install a site, and only
        # grant it a camera, over HTTPS.
        print(f"  installable  {https}/?t={token}")
    for ip in local_ips():
        label = "  tailscale" if ip.startswith("100.") else "  local Wi-Fi"
        print(f"{label:14} http://{ip}:{port}/?t={token}")
    print(f"\n  token: {token}")
    print("\nCtrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    run()
