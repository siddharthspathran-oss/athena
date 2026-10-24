// The Stage (jarvis/stage.py): Athena's floating panel, drawn from /live. Your words as you say them,
// a jelly mascot that listens, thinks and talks, and what she did as a card (24 Oct 2026, after Hedwig).
// The window process (ui/stage_window.py) shows and hides it, calling stageEnter / stageLeave.

const q = new URLSearchParams(location.search)
const TOKEN = q.get('t') || (() => { try { return localStorage.getItem('jarvis_token') || '' } catch (e) { return '' } })()
try { if (q.get('t')) localStorage.setItem('jarvis_token', q.get('t')) } catch (e) {}
const $ = id => document.getElementById(id)
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
const py = () => window.pywebview && window.pywebview.api
async function api(path, opts) {
  const r = await fetch(path, Object.assign({ headers: { 'X-Token': TOKEN, 'Content-Type': 'application/json' } }, opts || {}))
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(j.error || 'HTTP ' + r.status)
  return j
}
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) }).catch(() => {})
const clock = s => { s = Math.max(0, Math.floor(s)); return s < 60 ? s + 's' : Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0') }

const panel = $('panel')
let live = {}, base = { heard: '', reply: '' }, shown = false, cardId = '', dismissed = '', extra = {}, extraAt = 0, joyUntil = 0

// ---------- shown / hidden (called by the window) ----------
window.stageEnter = () => { base = { heard: live.heard || '', reply: live.reply || '' }; shown = true; panel.classList.remove('out'); render() }
window.stageLeave = () => { shown = false; panel.classList.add('out') }
$('close').onclick = () => { py() && py().dismiss(); window.stageLeave() }
panel.addEventListener('mouseenter', () => py() && py().hover(true))
panel.addEventListener('mouseleave', () => py() && py().hover(false))
// The window is exactly the panel: it asks for the panel's height whenever that changes.
new ResizeObserver(() => py() && py().fit(panel.offsetHeight, devicePixelRatio || 1)).observe(panel)
window.addEventListener('pywebviewready', () => py().fit(panel.offsetHeight, devicePixelRatio || 1))

async function poll() {
  try { live = await api('/live'); render() } catch (e) {}
  setTimeout(poll, document.hidden ? 1000 : 140)
}
poll()

// ---------- what's on it ----------
function render() {
  const st = live.running ? live.state : 'offline'
  const card = live.card && live.card.id !== dismissed && Date.now() / 1000 - live.card.t < 600 ? live.card : null
  for (const s of ['listening', 'thinking', 'speaking', 'idle', 'offline']) panel.classList.toggle(s, s === st)
  panel.classList.toggle('has-card', !!card)
  const lastTool = ((live.log || []).slice(-1)[0] || [])[1] || ''
  $('status').textContent = st === 'listening' ? (live.partial ? 'Listening · let go to send' : 'Listening')
    : st === 'thinking' ? (/^tool: /.test(lastTool) ? lastTool.slice(6).replace(/_/g, ' ') + '…' : 'Thinking…')
    : st === 'speaking' ? 'Speaking' : st === 'offline' ? 'Voice is offline' : ''
  const words = $('words')
  if (st === 'listening') { words.textContent = live.partial || ''; words.className = 'live' }
  else {
    const heard = live.heard && live.heard !== base.heard ? live.heard : ''
    words.textContent = live.partial || heard; words.className = ''
  }
  $('reply').textContent = live.reply && live.reply !== base.reply && !card ? live.reply : ''
  renderChips(live.jobs || [])
  renderCard(card)
}

function renderChips(js) {
  const box = $('chips'), sig = js.map(x => (x.console || x.mission) + x.state).join('|')
  if (box.dataset.sig !== sig) {
    box.dataset.sig = sig
    box.innerHTML = js.map(x => `<div class="chip${x.state === 'needs' ? ' needs' : ''}" data-c="${esc(x.console || '')}" data-s="${+x.since || 0}">` +
      `<i></i><b>${esc(x.who)}</b><span>${esc(x.title)}${x.state === 'needs' ? ' · needs you' : ''}</span><em></em></div>`).join('')
    box.querySelectorAll('.chip').forEach(c => { c.onclick = () => c.dataset.c && post('/view/missions', { action: 'deck_show', console: c.dataset.c }) })
  }
  box.querySelectorAll('.chip').forEach(c => { c.querySelector('em').textContent = clock(Date.now() / 1000 - +c.dataset.s) })
}

const ICON = { music: ['♪', '#1ed760', 'Music'], console: ['✱', '#e3895f', 'Claude Code'], web: ['▶', '#ff4d5e', 'Web'], answer: ['✦', '#c4a8ff', 'Athena'] }

function renderCard(c) {
  const el = $('card')
  if (!c) {
    if (cardId && !el.hidden) { el.classList.add('leaving'); setTimeout(() => { el.hidden = true; el.classList.remove('leaving') }, 220) }
    cardId = ''
    return
  }
  if (c.id !== cardId) {
    cardId = c.id; extra = {}; extraAt = 0; joyUntil = performance.now() + 750
    el.classList.remove('leaving'); el.hidden = false
    el.style.animation = 'none'; void el.offsetWidth; el.style.animation = ''               // pop again for a new card
    el.innerHTML = cardHtml(c)
    el.querySelector('[data-x]').onclick = () => { dismissed = c.id; render() }
    el.querySelectorAll('[data-m]').forEach(b => { b.onclick = () => { post('/view/music', { action: b.dataset.m }); extraAt = 0 } })
    const open = el.querySelector('[data-deck]')
    if (open) open.onclick = () => post('/view/missions', { action: 'deck_show', console: c.console })
  }
  if ((c.type === 'music' || c.type === 'console') && performance.now() - extraAt > 1200) {
    extraAt = performance.now()
    api('/view/stage').then(d => { extra = d; fill(c) }).catch(() => {})
  }
  fill(c)
}

function cardHtml(c) {
  const [ic, col, name] = ICON[c.type] || ICON.answer
  const head = (title, more) => `<div class="ch"><span class="app"><i style="background:${col}">${ic}</i>${esc(c.type === 'console' ? (c.who === 'Claude' ? 'Claude Code' : c.who) : c.type === 'web' && /youtube/.test(c.url) ? 'YouTube' : name)}</span>` +
    `<span class="t">${esc(title || '')}</span>${more || ''}<button data-x aria-label="Close the card">✕</button></div>`
  if (c.type === 'music') return head('') + `<div class="cb"><div class="music"><div class="art"></div><div class="meta"><div class="song">…</div><div class="by"></div>` +
    `<div class="bar"><i></i></div><div class="row"><span class="pos"></span><span class="len"></span></div></div></div>` +
    `<div class="ctl"><button data-m="previous" aria-label="Previous">⏮</button><button class="big" data-m="pause" aria-label="Play or pause">⏸</button><button data-m="next" aria-label="Next">⏭</button></div></div>`
  if (c.type === 'console') return head(c.title, `<span class="pill">starting</span><button data-deck>Open</button>`) + `<pre class="term">Starting…</pre>`
  if (c.type === 'web') return head(c.title, `<a href="${esc(c.open || c.url)}" target="_blank" rel="noopener">Open</a>`) +
    `<div class="web${/youtube/.test(c.url) ? '' : ' site'}"><iframe src="${esc(c.url)}" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen></iframe></div>`
  return head(c.title) + `<div class="answer">${md(c.text || '')}</div>`
}

function fill(c) {
  const el = $('card')
  if (c.type === 'music') {
    const m = extra.music
    if (!m) return
    el.querySelector('.song').textContent = m.title || 'Nothing playing'
    el.querySelector('.by').textContent = m.artist || ''
    el.querySelector('.ch .t').textContent = /spotify/i.test(m.app || '') ? 'Spotify' : (m.app || '').split('!').pop()
    el.querySelector('.art').style.backgroundImage = m.art ? `url("${m.art}")` : ''
    el.querySelector('.bar i').style.width = m.length ? Math.min(100, m.position / m.length * 100) + '%' : '0'
    el.querySelector('.pos').textContent = clock(m.position || 0); el.querySelector('.len').textContent = m.length ? clock(m.length) : ''
    const pp = el.querySelector('.big'); pp.dataset.m = m.playing ? 'pause' : 'play'; pp.textContent = m.playing ? '⏸' : '▶'
  } else if (c.type === 'console' && 'screen' in extra) {
    const lines = (extra.screen || '').split('\n').map(l => l.replace(/\s+$/, '')).filter((l, i, a) => l || (a[i - 1] || '').trim())
    el.querySelector('.term').textContent = lines.slice(-16).join('\n') || '…'
    const pill = el.querySelector('.pill'), s = extra.state || 'starting'
    pill.textContent = s === 'needs' ? 'needs you' : s; pill.className = 'pill ' + s
  }
}

function md(text) {
  const inline = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>')
  return text.trim().split(/\n{2,}/).map(block => {
    const lines = block.split('\n')
    if (lines.every(l => /^\s*[-*•]\s+/.test(l))) return '<ul>' + lines.map(l => `<li>${inline(l.replace(/^\s*[-*•]\s+/, ''))}</li>`).join('') + '</ul>'
    if (lines.every(l => /^\s*\d+[.)]\s+/.test(l))) return '<ol>' + lines.map(l => `<li>${inline(l.replace(/^\s*\d+[.)]\s+/, ''))}</li>`).join('') + '</ol>'
    return `<p>${lines.map(inline).join('<br>')}</p>`
  }).join('')
}

// ---------- the mascot: a jelly squircle on springs ----------
const cv = $('blob'), g = cv.getContext('2d')
const S = { sx: { x: 1, v: 0 }, sy: { x: 1, v: 0 }, ex: { x: 0, v: 0 }, ey: { x: 0, v: 0 }, glow: { x: 0.4, v: 0 } }
const spring = (s, to, dt, k = 180, d = 12) => { s.v += (-k * (s.x - to) - d * s.v) * dt; s.x += s.v * dt }
let lv = 0, t0 = performance.now(), last = t0, blinkAt = t0 + 2500

function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000), t = (now - t0) / 1000
  last = now
  const r = cv.getBoundingClientRect(), dpr = devicePixelRatio || 1
  if (cv.width !== Math.round(r.width * dpr) || cv.height !== Math.round(r.height * dpr)) { cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr) }
  g.setTransform(cv.width / 180, 0, 0, cv.height / 150, 0, 0)
  g.clearRect(0, 0, 180, 150)

  const st = live.running ? live.state : 'offline'
  lv += ((+live.level || 0) - lv) * 0.3
  let sx = 1 + 0.018 * Math.sin(t * 1.7), sy = 1 - 0.018 * Math.sin(t * 1.7), ex = 0, ey = 0, wob = 0.012, glow = 0.35
  if (st === 'listening') { sx = 1 + 0.14 * lv + 0.012 * Math.sin(t * 3); sy = 1 - 0.17 * lv; ey = -1.5 - 3 * lv; wob = 0.02 + 0.07 * lv; glow = 0.55 + 0.4 * lv }
  else if (st === 'thinking') { sx = 1 + 0.035 * Math.sin(t * 3.1); sy = 1 - 0.035 * Math.sin(t * 3.1); ex = 6 * Math.sin(t * 1.1); ey = -3.5; wob = 0.045; glow = 0.6 }
  else if (st === 'speaking') { const b = Math.abs(Math.sin(t * 7.2)); sx = 1 - 0.05 * b; sy = 1 + 0.08 * b; ey = 1; wob = 0.03; glow = 0.7 }
  spring(S.sx, sx, dt); spring(S.sy, sy, dt); spring(S.ex, ex, dt, 90, 11); spring(S.ey, ey, dt, 90, 11); spring(S.glow, glow, dt, 40, 9)

  const R = 44, cx = 90, floor = 128, cy = floor - R * S.sy.x
  const off = st === 'offline'
  // the floor shadow
  g.save(); g.globalAlpha = 0.28; g.fillStyle = off ? '#000' : '#7a4cff'
  g.beginPath(); g.ellipse(cx, floor + 6, R * 0.95 * S.sx.x, 6, 0, 0, Math.PI * 2); g.filter = 'blur(4px)'; g.fill(); g.restore()
  // the body: a squircle whose edge ripples
  const N = 72, pts = []
  for (let i = 0; i < N; i++) {
    const th = i / N * Math.PI * 2, c = Math.cos(th), s = Math.sin(th), n = 3.2
    const se = Math.pow(Math.pow(Math.abs(c), n) + Math.pow(Math.abs(s), n), -1 / n)
    const rip = wob * (Math.sin(2 * th + t * 1.4) + 0.6 * Math.sin(3 * th - t * 2.1) + 0.35 * Math.sin(5 * th + t * 3.3))
    const rr = R * se * (1 + rip)
    pts.push([cx + c * rr * S.sx.x, cy + s * rr * S.sy.x])
  }
  g.save()
  g.shadowColor = off ? 'rgba(0,0,0,0)' : st === 'listening' ? `rgba(255,163,77,${0.35 + 0.4 * lv})` : `rgba(196,168,255,${S.glow.x})`
  g.shadowBlur = 26
  g.beginPath()
  for (let i = 0; i <= N; i++) {
    const a = pts[i % N], b = pts[(i + 1) % N], mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2
    i === 0 ? g.moveTo(mx, my) : g.quadraticCurveTo(a[0], a[1], mx, my)
  }
  g.closePath()
  const fill = g.createRadialGradient(cx - 16, cy - 22, 4, cx, cy, R * 1.5)
  if (off) { fill.addColorStop(0, '#5a5670'); fill.addColorStop(1, '#2b2838') }
  else { fill.addColorStop(0, '#f6eeff'); fill.addColorStop(0.35, '#cbb2ff'); fill.addColorStop(0.75, '#9b74ff'); fill.addColorStop(1, '#ff6ec7') }
  g.fillStyle = fill; g.fill(); g.restore()
  // a soft highlight
  g.save(); g.globalAlpha = off ? 0.08 : 0.35; g.fillStyle = '#fff'
  g.beginPath(); g.ellipse(cx - R * 0.38 * S.sx.x, cy - R * 0.5 * S.sy.x, 11, 6, -0.5, 0, Math.PI * 2); g.fill(); g.restore()
  // the eyes: blink now and then, look around while thinking, arcs of joy when a card arrives
  if (now > blinkAt + 160) blinkAt = now + 2200 + Math.random() * 3600
  const bp = now > blinkAt ? Math.min(1, (now - blinkAt) / 160) : 0, open = off ? 0.15 : 1 - 0.88 * Math.sin(Math.PI * bp)
  const joy = now < joyUntil
  g.fillStyle = g.strokeStyle = '#1a0f2e'; g.lineWidth = 3.4; g.lineCap = 'round'
  for (const side of [-1, 1]) {
    const x = cx + side * 15 * S.sx.x + S.ex.x, y = cy - 4 + S.ey.x
    if (joy) { g.beginPath(); g.arc(x, y + 3, 6, Math.PI * 1.1, Math.PI * 1.9); g.stroke(); continue }
    const w = 10, h = Math.max(2, 18 * open)
    g.beginPath(); g.roundRect(x - w / 2, y - h / 2, w, h, 5); g.fill()
  }
  requestAnimationFrame(frame)
}
requestAnimationFrame(frame)
