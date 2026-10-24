import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import NodeCard from './NodeCard.jsx'
import MissionMap from './MissionMap.jsx'
import { AskModal, Composer, WorkspaceModal } from './Composer.jsx'
import { InkLayer, InkTools } from './Ink.jsx'
import { DeckCtx } from './nodes.jsx'
import { HandoffModal, LoopModal, TeamModal } from './orchestration.jsx'
import Studios from './studios/Studios.jsx'
import { UsageModal, UsagePills } from './Usage.jsx'
import { MemoryModal, ProfilesModal } from './continuity.jsx'
import { PORT_Y, TestsModal, WireLayer } from './flows.jsx'
import { api, money, toUrl, uploadImage, wsURL } from './api.js'
import { snapshot } from './Terminal.jsx'

const SIZES = { console: [760, 470], note: [300, 260], web: [640, 460], text: [380, 90], checklist: [320, 320],
  kanban: [880, 480], image: [480, 360], git: [420, 460], file: [640, 560], diagram: [760, 520], agent: [420, 380] }
const BOARD_TYPES = ['note', 'web', 'text', 'checklist', 'kanban', 'git', 'image', 'file', 'diagram', 'agent']
const typing = el => el?.closest?.('input,textarea,[contenteditable="true"],.xterm')
// The Deck's own window (Electron, or the WebView2 fallback) - not the board opened in some browser tab.
// Only it counts as "the Deck is open": a browser tab left open stopped the window from launching (20 Oct 2026).
const WINDOWED = !!window.deckShell?.electron || !!window.chrome?.webview
const clamp = (v, a, b) => Math.max(a, Math.min(b, v))
const ease = t => 1 - Math.pow(1 - t, 4)

export default function App() {
  const [board, setBoard] = useState(null)
  const [status, setStatus] = useState({})
  const [costs, setCosts] = useState({})
  const [view, setView] = useState({ x: 40, y: 30, z: 0.85 })
  const [sel, setSel] = useState('')
  const [order, setOrder] = useState({})
  const [termKeys, setTermKeys] = useState({})
  const [modal, setModal] = useState(null)
  const [menu, setMenu] = useState(false)
  const [toastMsg, setToastMsg] = useState('')
  const [talking, setTalking] = useState(false)
  const [mapOpen, setMapOpen] = useState(() => { try { return localStorage.getItem('deck_map') !== '0' } catch { return true } })
  const [closingWs, setClosingWs] = useState('')
  const [usage, setUsage] = useState(null)
  const [inking, setInking] = useState(false)
  const [live, setLive] = useState(null)
  const [drawing, setDrawing] = useState(null)                     // a wire being dragged out: {from: [x, y], to: [x, y]}
  const [, setTick] = useState(0)
  const stage = useRef(null), boardRef = useRef(null), viewRef = useRef(view), zoomRef = useRef(view.z), seq = useRef(10)
  const imgPick = useRef(null)
  boardRef.current = board; viewRef.current = view; zoomRef.current = view.z

  const toast = useCallback(m => { setToastMsg(m); clearTimeout(toast.t); toast.t = setTimeout(() => setToastMsg(''), 3200) }, [])
  const load = useCallback(async () => {
    const s = await api('/api/state')
    setBoard(s); setStatus(s.status || {}); setCosts(s.costs || {})
    return s
  }, [])

  const active = board && (board.workspaces.find(w => w.id === board.active) || board.workspaces[0])
  const nodes = useMemo(() => (board && active ? board.nodes.filter(n => n.ws === active.id) : []), [board, active])

  // ---- view: per workspace, saved a moment after you stop moving ----
  useEffect(() => { if (active?.view) setView(active.view) }, [active?.id])          // eslint-disable-line
  useEffect(() => {
    if (!active) return
    const t = setTimeout(() => api(`/api/workspaces/${active.id}`, { method: 'PATCH', body: { view } }).catch(() => {}), 700)
    return () => clearTimeout(t)
  }, [view, active?.id])                                                             // eslint-disable-line

  const animateTo = useCallback(target => {
    const from = { ...viewRef.current }, t0 = performance.now()
    const step = now => {
      const k = ease(Math.min(1, (now - t0) / 420))
      setView({ x: from.x + (target.x - from.x) * k, y: from.y + (target.y - from.y) * k, z: from.z + (target.z - from.z) * k })
      if (k < 1) requestAnimationFrame(step)
    }
    requestAnimationFrame(step)
  }, [])

  const fit = useCallback((ids, snap) => {
    const b = snap || boardRef.current
    if (!b || !stage.current) return
    const list = b.nodes.filter(n => ids.includes(n.id))
    if (!list.length) return
    if (list[0].ws !== b.active) {                                     // it lives on another workspace: go there first
      api(`/api/workspaces/${list[0].ws}`, { method: 'PATCH', body: { activate: true } })
      setBoard({ ...b, active: list[0].ws })
    }
    const x0 = Math.min(...list.map(n => n.x)), y0 = Math.min(...list.map(n => n.y))
    const x1 = Math.max(...list.map(n => n.x + n.w)), y1 = Math.max(...list.map(n => n.y + n.h))
    const W = stage.current.clientWidth, H = stage.current.clientHeight
    const z = clamp(Math.min((W - 80) / (x1 - x0), (H - 80) / (y1 - y0)), 0.2, 1)
    animateTo({ z, x: W / 2 - ((x0 + x1) / 2) * z, y: H / 2 - ((y0 + y1) / 2) * z })
    if (ids.length === 1) select(ids[0])
  }, [animateTo])                                                                     // eslint-disable-line

  const select = useCallback(id => { setSel(id); setOrder(o => (o[id] === seq.current ? o : { ...o, [id]: ++seq.current })) }, [])
  const moved = useCallback((id, pos) => setBoard(b => ({ ...b, nodes: b.nodes.map(n => (n.id === id ? { ...n, ...pos } : n)) })), [])

  // ---- live board events ----
  useEffect(() => {
    load().catch(e => toast(`Can't reach the board: ${e.message}`))
    let ws, alive = true
    const connect = () => {
      ws = new WebSocket(wsURL(WINDOWED ? '/ws/events?w=1' : '/ws/events'))
      ws.onmessage = async e => {
        const ev = JSON.parse(e.data)
        if (ev.type === 'status') setStatus(s => ({ ...s, [ev.id]: ev }))
        else if (ev.type === 'costs') setCosts(ev.costs)
        else if (ev.type === 'board') load()
        else if (ev.type === 'restarted') setTermKeys(k => ({ ...k, [ev.id]: (k[ev.id] || 0) + 1 }))
        else if (ev.type === 'focus') { const s = await load(); fit(ev.fit || [ev.id], s) }
        else if (ev.type === 'snap') api(`/api/snap/${ev.req}`, { method: 'POST', body: { text: snapshot(ev.id) } }).catch(() => {})
        else if (ev.type === 'ink') setBoard(b => b && ({ ...b, workspaces: b.workspaces.map(w => (w.id !== ev.ws ? w : {
          ...w, ink: ev.add ? [...(w.ink || []), ev.add] : ev.del === '*' ? [] : (w.ink || []).filter(s => s.id !== ev.del) })) }))
        else if (ev.type === 'toast') toast(ev.text)
      }
      ws.onclose = () => { if (alive) setTimeout(connect, 1500) }
    }
    connect()
    const tick = setInterval(() => setTick(t => t + 1), 15000)
    return () => { alive = false; ws && ws.close(); clearInterval(tick) }
  }, [])                                                                              // eslint-disable-line

  useEffect(() => {                                                                   // token use: once a minute is plenty
    const get = () => api('/api/usage').then(setUsage).catch(() => {})
    get(); const t = setInterval(get, 60000); return () => clearInterval(t)
  }, [])

  useEffect(() => {
    document.body.classList.toggle('map-open', mapOpen)
    try { localStorage.setItem('deck_map', mapOpen ? '1' : '0') } catch {}
  }, [mapOpen])

  // ---- pan and zoom: drag the empty board, wheel to pan, Ctrl+wheel (or pinch) to zoom ----
  useEffect(() => {
    const el = stage.current
    if (!el) return
    const wheel = e => {
      if (!e.ctrlKey && e.target.closest('.term,.note-t,.web-frame,.menu,.kanban-node,.list-node,.git-node,.scrolls')) return  // they scroll themselves
      e.preventDefault()
      const v = viewRef.current
      if (e.ctrlKey) {
        const r = el.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top
        const z = clamp(v.z * Math.exp(-e.deltaY * 0.0018), 0.2, 1.75)
        setView({ z, x: px - (px - v.x) * (z / v.z), y: py - (py - v.y) * (z / v.z) })
      } else setView({ ...v, x: v.x - (e.shiftKey ? e.deltaY : e.deltaX), y: v.y - (e.shiftKey ? 0 : e.deltaY) })
    }
    el.addEventListener('wheel', wheel, { passive: false })
    return () => el.removeEventListener('wheel', wheel)
  }, [board === null])                                                                // eslint-disable-line

  const pan = e => {
    if (e.button !== 0 && e.button !== 1) return
    if (e.target !== stage.current && e.target.id !== 'world') return
    setSel(''); setMenu(false)
    const sx = e.clientX, sy = e.clientY, v0 = viewRef.current
    stage.current.classList.add('panning'); document.body.classList.add('dragging')
    const move = ev => setView({ ...v0, x: v0.x + ev.clientX - sx, y: v0.y + ev.clientY - sy })
    const up = () => { stage.current?.classList.remove('panning'); document.body.classList.remove('dragging'); window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }
  const zoomBy = f => {
    const v = viewRef.current, W = stage.current.clientWidth / 2, H = stage.current.clientHeight / 2
    const z = clamp(v.z * f, 0.2, 1.75)
    animateTo({ z, x: W - (W - v.x) * (z / v.z), y: H - (H - v.y) * (z / v.z) })
  }

  // ---- adding things: in the middle of what you're looking at ----
  const centre = type => {
    const [w, h] = SIZES[type], v = viewRef.current, el = stage.current
    return { x: Math.round((el.clientWidth / 2 - v.x) / v.z - w / 2), y: Math.round((el.clientHeight / 2 - v.y) / v.z - h / 2) }
  }
  const add = async (what, extra = {}) => {
    setMenu(false)
    if (what === 'custom' && !extra.cmd) return setModal({ ask: 'custom' })
    if (what === 'web' && !extra.url) return setModal({ ask: 'web' })
    if (what === 'image' && !extra.src) return imgPick.current?.click()
    const type = BOARD_TYPES.includes(what) ? what : 'console'
    try {
      await api('/api/nodes', { method: 'POST', body: { type, kind: what, ws: active.id, ...centre(type), focus: true, ...extra } })
    } catch (e) { toast(e.message) }
  }

  // ---- images: dropped, pasted or picked - placed where they land, at most 640 wide ----
  const placeImage = async (file, at) => {
    if (!file?.type?.startsWith('image/')) return
    try {
      const src = await uploadImage(file)
      const img = await new Promise((ok, no) => { const i = new Image(); i.onload = () => ok(i); i.onerror = no; i.src = src })
      const k = Math.min(1, 640 / img.naturalWidth), w = Math.round(img.naturalWidth * k), h = Math.round(img.naturalHeight * k) + 38
      const pos = at ? { x: Math.round(at[0] - w / 2), y: Math.round(at[1] - h / 2) } : centre('image')
      await api('/api/nodes', { method: 'POST', body: { type: 'image', ws: boardRef.current.active, src, title: file.name || 'Pasted image', w, h, ...pos } })
    } catch (e) { toast(`Couldn't add the image: ${e.message}`) }
  }
  const drop = e => {
    const files = [...(e.dataTransfer?.files || [])].filter(f => f.type.startsWith('image/'))
    if (!files.length) return
    e.preventDefault()
    const r = stage.current.getBoundingClientRect(), v = viewRef.current
    files.forEach((f, i) => placeImage(f, [(e.clientX - r.left - v.x) / v.z + i * 40, (e.clientY - r.top - v.y) / v.z + i * 40]))
  }
  useEffect(() => {
    const paste = e => {
      if (typing(e.target) || typing(document.activeElement)) return
      const f = [...(e.clipboardData?.files || [])].find(x => x.type.startsWith('image/'))
      if (f) { e.preventDefault(); placeImage(f) }
    }
    window.addEventListener('paste', paste)
    return () => window.removeEventListener('paste', paste)
  })

  const talk = async () => {
    try { await api('/api/athena/listen', { method: 'POST' }); setTalking(true); toast('Athena is listening…'); setTimeout(() => setTalking(false), 7000) }
    catch (e) { toast(e.message) }
  }

  // ---- wires (flows.jsx): drag from a box's right dot, let go over another box ----
  const startWire = useCallback((e, from) => {
    if (e.button !== 0) return
    e.preventDefault(); e.stopPropagation()
    const a = boardRef.current.nodes.find(n => n.id === from), r = stage.current.getBoundingClientRect()
    const at = ev => { const v = viewRef.current; return [(ev.clientX - r.left - v.x) / v.z, (ev.clientY - r.top - v.y) / v.z] }
    const start = [a.x + a.w, a.y + PORT_Y]
    setDrawing({ from: start, to: at(e) })
    document.body.classList.add('dragging')
    const move = ev => setDrawing({ from: start, to: at(ev) })
    const up = async ev => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up)
      document.body.classList.remove('dragging'); setDrawing(null)
      const to = document.elementFromPoint(ev.clientX, ev.clientY)?.closest('.node')?.dataset.id
      if (!to || to === from) return
      try { await api('/api/wires', { method: 'POST', body: { from, to } }) } catch (err) { toast(err.message) }
    }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }, [toast])
  const cutWire = w => api(`/api/workspaces/${active.id}/wires/${w.id}`, { method: 'DELETE' }).catch(e => toast(e.message))

  // What node bodies share. Memoised: the board pans at 60 fps and every consumer would re-render with it.
  const all = useMemo(() => (board ? board.nodes.filter(n => n.type === 'console') : []), [board])
  const ctx = useMemo(() => ({ consoles: all, status, toast, open: setModal, fly: id => fit([id]), wire: startWire }),
    [all, status, toast, fit, startWire])

  if (!board) return <div className="boot"><span className="mark" />Opening the board…</div>

  const count = s => all.filter(n => (status[n.id]?.state || 'starting') === s)
  const needs = count('needs'), working = count('working'), resting = count('resting')
  const spent = Object.values(costs).reduce((a, b) => a + b, 0)
  const wsNeeds = id => all.filter(n => n.ws === id && status[n.id]?.state === 'needs').length
  const modalNode = modal?.nodeId && board.nodes.find(n => n.id === modal.nodeId)      // live: loops update while it's open
  const extras = Object.entries(board.kinds || {}).filter(([, v]) => v.extra && v.installed)
  const wires = active.wires || []
  const fed = new Set(wires.map(w => w.to))

  return (
    <DeckCtx.Provider value={ctx}>
      <header id="top">
        <div className="brand"><span className="mark" aria-hidden="true" /><b>Athena</b><span>Deck</span></div>
        <nav id="tabs" aria-label="Workspaces">
          {board.workspaces.map(w => (
            <div key={w.id} className={`tab${w.id === active.id ? ' on' : ''}`} role="button" tabIndex={0} title={w.folder}
              onClick={() => { api(`/api/workspaces/${w.id}`, { method: 'PATCH', body: { activate: true } }); setBoard({ ...board, active: w.id }) }}>
              {w.name}
              {wsNeeds(w.id) > 0 && <span className="n need">{wsNeeds(w.id)}</span>}
              {board.workspaces.length > 1 && (
                <button className="x ghost" aria-label={`Close ${w.name}`} onClick={e => {
                  e.stopPropagation()
                  if (closingWs !== w.id) { setClosingWs(w.id); toast(`Tap ✕ again to close ${w.name} and stop its consoles`); return }
                  api(`/api/workspaces/${w.id}`, { method: 'DELETE' }); setClosingWs('')
                }}>{closingWs === w.id ? 'close?' : '✕'}</button>
              )}
            </div>
          ))}
        </nav>
        <button className="ghost icon" title="New workspace for a folder" aria-label="New workspace" onClick={() => setModal({ ws: true })}>+</button>
        <div className="grow" />
        <div id="chips">
          {needs.length > 0 && <button className="chip need" onClick={() => fit([needs[0].id])}><i />{needs.length} need you</button>}
          {working.length > 0 && <button className="chip work" onClick={() => setMapOpen(true)}><i />{working.length} working</button>}
          {resting.length > 0 && <button className="chip rest" onClick={() => setMapOpen(true)}><i />{resting.length} resting</button>}
          {spent > 0 && <span className="chip" title="Claude spend of these consoles, at API prices">{money(spent)}</span>}
          <UsagePills usage={usage} onOpen={() => setModal({ usage: true })} />
        </div>
        <div className="add-wrap">
          <button className="btn" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(m => !m)}>Add <span aria-hidden="true">▾</span></button>
          {menu && (
            <div className="menu" role="menu">
              <button role="menuitem" onClick={() => add('claude')}><i className="k claude" />Claude Code console</button>
              <button role="menuitem" onClick={() => add('gemini')}><i className="k gemini" />Antigravity console</button>
              <button role="menuitem" onClick={() => add('codex')}><i className="k codex" />Codex console (ChatGPT)</button>
              {(board.profiles || []).map(p => <button key={p.id} role="menuitem" onClick={() => add(p.cli, { profile: p.id })}>
                <i className={`k ${p.cli}`} />{p.cli === 'claude' ? 'Claude Code' : 'Codex'} · {p.name}</button>)}
              <button role="menuitem" onClick={() => add('shell')}><i className="k shell" />PowerShell</button>
              <button role="menuitem" onClick={() => add('custom')}><i className="k custom" />Custom command…</button>
              {extras.length > 0 && <><hr /><small className="menu-h">More agents on this PC</small>
                {extras.map(([k, v]) => <button key={k} role="menuitem" onClick={() => add(k)}><i className="k extra" />{v.label}</button>)}</>}
              <button role="menuitem" onClick={() => setModal({ profiles: true })}><i className="k" />Accounts &amp; profiles…</button>
              <hr />
              <button role="menuitem" onClick={() => add('note')}><i className="k note" />Sticky note</button>
              <button role="menuitem" onClick={() => add('text')}><i className="k text" />Text</button>
              <button role="menuitem" onClick={() => add('checklist')}><i className="k checklist" />Checklist</button>
              <button role="menuitem" onClick={() => add('kanban')}><i className="k kanban" />Kanban board</button>
              <button role="menuitem" onClick={() => add('image')}><i className="k image" />Image…</button>
              <button role="menuitem" onClick={() => add('agent', { text: '' })}><i className="k agent" />Agent step (for flows)</button>
              <hr />
              <button role="menuitem" onClick={() => add('web')}><i className="k web" />Browser…</button>
              <button role="menuitem" onClick={() => add('git', { path: active.folder })}><i className="k git" />Git card</button>
            </div>
          )}
          <input ref={imgPick} type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden
            onChange={e => { [...e.target.files].forEach(f => placeImage(f)); e.target.value = '' }} />
        </div>
        <button className="btn" onClick={() => setModal({ composer: true })} title="Launch a whole team at once">Composer</button>
        <button className="btn" onClick={() => setModal({ studios: true })} title="Code, Markdown vault, documents and diagrams">Studios</button>
        <button className="btn" onClick={() => setModal({ memory: true })} title="What your agents should know - pushed to a console when you say">Memory</button>
        <button className={`btn talk${talking ? ' live' : ''}`} onClick={talk} title="Talk to Athena - she can drive the board"><span className="mic" aria-hidden="true" />Athena</button>
        <button className="ghost icon" onClick={() => setMapOpen(o => !o)} title="Mission map" aria-label="Toggle mission map" aria-pressed={mapOpen}>☰</button>
      </header>

      <main id="stage" ref={stage} tabIndex={-1} onPointerDown={pan} onDragOver={e => e.dataTransfer?.types?.includes('Files') && e.preventDefault()} onDrop={drop}
        style={{ backgroundSize: `${24 * view.z}px ${24 * view.z}px, auto, auto`, backgroundPosition: `${view.x}px ${view.y}px, 0 0, 0 0` }}>
        <div id="world" style={{ transform: `translate(${view.x}px,${view.y}px) scale(${view.z})` }}>
          <WireLayer nodes={nodes} wires={wires} drawing={drawing} onCut={cutWire} />
          {nodes.map(n => (
            <NodeCard key={n.id} node={n} status={status[n.id]} cost={costs[n.id] || 0} selected={sel === n.id} z={order[n.id] || 1}
              zoomRef={zoomRef} onSelect={select} onMoved={moved} termKey={termKeys[n.id] || 0} tick={Math.floor(Date.now() / 15000)}
              fed={fed.has(n.id)} />
          ))}
          <InkLayer strokes={active.ink || []} live={live} />
        </div>
        {inking && <InkTools ws={active.id} strokes={active.ink || []} view={view} stageRef={stage} setLive={setLive} toast={toast}
          onExit={() => { setInking(false); setLive(null) }} />}
        {!nodes.length && (
          <div id="empty">
            <div className="empty-card">
              <div className="orbit" aria-hidden="true"><i /><i /><i /></div>
              <h1>Put your agents on the board.</h1>
              <p>Real terminals for Claude Code, Codex, Antigravity and PowerShell — side by side, each showing whether it’s working, resting or needs you.</p>
              <div className="row">
                <button className="btn primary" onClick={() => add('claude')}>Claude console</button>
                <button className="btn" onClick={() => add('gemini')}>Antigravity console</button>
                <button className="btn" onClick={() => setModal({ composer: true })}>Composer</button>
              </div>
              <small>Or tell Athena: “open two Claude consoles on the orb site”.</small>
            </div>
          </div>
        )}
        <div id="zoomCtl">
          <button onClick={() => zoomBy(1 / 1.2)} aria-label="Zoom out">−</button>
          <span id="zLbl">{Math.round(view.z * 100)}%</span>
          <button onClick={() => zoomBy(1.2)} aria-label="Zoom in">+</button>
          <button onClick={() => fit(nodes.map(n => n.id))} title="Fit everything">Fit</button>
          <span className="sep" />
          <button className={inking ? 'on' : ''} onClick={() => setInking(i => !i)} title="Ink: draw on the board, mark up images" aria-pressed={inking}>✎ Ink</button>
        </div>
      </main>

      <MissionMap nodes={board.nodes} status={status} workspaces={board.workspaces} onFly={id => fit([id])} />

      {modal?.composer && <Composer ws={active} origin={[centre('console').x - 400, centre('console').y - 250]} onClose={() => setModal(null)} toast={toast} />}
      {modal?.ws && <WorkspaceModal onClose={() => setModal(null)} toast={toast} />}
      {modal?.studios && <Studios workspaces={board.workspaces} active={active} onClose={() => setModal(null)} />}
      {modal?.usage && <UsageModal usage={usage} onClose={() => setModal(null)} />}
      {modal?.profiles && <ProfilesModal profiles={board.profiles || []} onClose={() => setModal(null)} />}
      {modal?.memory && <MemoryModal memory={board.memory || {}} ws={active} onClose={() => setModal(null)} />}
      {modalNode && modal.loops && <LoopModal node={modalNode} onClose={() => setModal(null)} />}
      {modalNode && modal.handoff && <HandoffModal node={modalNode} onClose={() => setModal(null)} />}
      {modalNode && modal.team && <TeamModal node={modalNode} onClose={() => setModal(null)} />}
      {modalNode && modal.tests && <TestsModal node={modalNode} nodes={board.nodes} onClose={() => setModal(null)} />}
      {modal?.ask === 'custom' && <AskModal title="Custom command" sub="Any command line - it runs in its own console in this workspace's folder."
        placeholder="e.g. npm run dev" onClose={() => setModal(null)} onDone={cmd => add('custom', { cmd, title: cmd.split(' ')[0] })} />}
      {modal?.ask === 'web' && <AskModal title="Browser" sub="A real browser on the board - any site, your dev server's localhost, or a search."
        placeholder="google.com, localhost:5173 or a search" onClose={() => setModal(null)}
        onDone={v => { const url = toUrl(v); add('web', { url, title: new URL(url).hostname.replace(/^www\./, '') }) }} />}
      {toastMsg && <div id="toast" role="status">{toastMsg}</div>}
    </DeckCtx.Provider>
  )
}
