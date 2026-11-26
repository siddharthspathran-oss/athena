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
