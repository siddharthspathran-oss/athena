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
