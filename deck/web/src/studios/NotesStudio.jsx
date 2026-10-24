import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api.js'
import { markdown } from '../lib/md.js'
import { DeckCtx } from '../nodes.jsx'

const q = o => new URLSearchParams(o).toString()
const dirOf = p => (p.includes('/') ? p.slice(0, p.lastIndexOf('/') + 1) : '')
const norm = p => { const out = []; for (const s of p.split('/')) { if (s === '..') out.pop(); else if (s && s !== '.') out.push(s) } return out.join('/') }

// An Obsidian-style vault over the workspace folder's Markdown: list, autosaving editor,
// live preview with [[links]] and backlinks, and a force-directed graph of how notes link up.
export function NotesStudio({ root }) {
  const { toast } = useContext(DeckCtx)
  const [notes, setNotes] = useState([])
  const [cur, setCur] = useState('')
  const [text, setText] = useState('')
  const [mode, setMode] = useState('split')
  const [saved, setSaved] = useState('saved')
  const [find, setFind] = useState('')
  const [draft, setDraft] = useState('')
  const timer = useRef(0)
  const refresh = useCallback(() => api(`/api/fs/notes?${q({ root })}`).then(setNotes).catch(e => toast(e.message)), [root, toast])
  useEffect(() => { setCur(''); setText(''); refresh() }, [root, refresh])
  const openNote = async path => {
    clearTimeout(timer.current)
    try { const f = await api(`/api/fs/file?${q({ root, path })}`); setCur(path); setText(f.text); setSaved('saved') }
    catch (e) { toast(e.message) }
  }
  const create = async name => {
    const clean = name.trim().replace(/[\\:*?"<>|]/g, '').replace(/\.md$/i, '')
    if (!clean) return
    const path = `${clean}.md`
    if (!notes.some(n => n.path.toLowerCase() === path.toLowerCase())) {
      await api('/api/fs/file', { method: 'PUT', body: { root, path, text: `# ${clean.split('/').pop()}\n\n` } }).catch(e => toast(e.message))
      await refresh()
    }
    openNote(path)
  }
  const edit = v => {
    setText(v); setSaved('saving'); clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      try { await api('/api/fs/file', { method: 'PUT', body: { root, path: cur, text: v } }); setSaved('saved'); refresh() }
      catch (e) { setSaved('failed'); toast(e.message) }
    }, 700)
  }
  const follow = e => {
    const a = e.target.closest('a.wiki')
    if (!a) return
    e.preventDefault()
    if (a.dataset.href) return openNote(norm(dirOf(cur) + a.dataset.href.split('#')[0]))
    const want = a.dataset.wiki.toLowerCase()
    const hit = notes.find(n => n.path.toLowerCase().replace(/\.md$/, '').split('/').pop() === want) || notes.find(n => n.title.toLowerCase() === want)
    hit ? openNote(hit.path) : create(a.dataset.wiki)
  }
  const html = useMemo(() => markdown(text), [text])
  const back = notes.filter(n => n.links.includes(cur))
  const shown = notes.filter(n => !find || (n.title + ' ' + n.path).toLowerCase().includes(find.toLowerCase()))
  return (
    <div className="studio-split">
      <aside className="tree scrolls">
        <input className="tree-filter" value={find} onChange={e => setFind(e.target.value)} placeholder={`Search ${notes.length} notes`} />
        <div className="new-note"><input value={draft} onChange={e => setDraft(e.target.value)} placeholder="+ New note" onKeyDown={e => { if (e.key === 'Enter') { create(draft); setDraft('') } }} /></div>
        <button className={`tree-row${mode === 'graph' ? ' on' : ''}`} onClick={() => setMode('graph')}><span className="tw">◎</span>Graph view</button>
        {shown.map(n => (
          <button key={n.path} className={`tree-row${cur === n.path && mode !== 'graph' ? ' on' : ''}`} onClick={() => { openNote(n.path); if (mode === 'graph') setMode('split') }} title={n.path}>
            <span className="tw">{n.links.length ? '•' : ''}</span><span>{n.title}</span>
          </button>))}
        {!notes.length && <p className="dim small" style={{ padding: 10 }}>No Markdown here yet - make the first note above.</p>}
      </aside>
      <section className="viewer">
        {mode === 'graph' ? <Graph notes={notes} cur={cur} onOpen={p => { openNote(p); setMode('split') }} /> : !cur
          ? <div className="studio-empty">Pick a note, or make one. Link notes with [[Note name]]; the graph shows how they connect.</div> : <>
            <div className="viewer-h"><code>{cur}</code><span className={`save ${saved}`}>{saved === 'saving' ? 'Saving…' : saved === 'failed' ? 'Not saved' : 'Saved'}</span>
              <span className="grow" />
              <div className="seg mini-seg">{['edit', 'split', 'preview'].map(m => <button key={m} className={mode === m ? 'on' : ''} onClick={() => setMode(m)}>{m}</button>)}</div></div>
            <div className={`md-split ${mode}`}>
              {mode !== 'preview' && <textarea className="md-edit scrolls" value={text} onChange={e => edit(e.target.value)} spellCheck={false} />}
              {mode !== 'edit' && <div className="md-view scrolls" onClick={follow}>
                <div className="md" dangerouslySetInnerHTML={{ __html: html }} />
                {back.length > 0 && <div className="backlinks"><b>Linked from</b>{back.map(n => <button key={n.path} onClick={() => openNote(n.path)}>{n.title}</button>)}</div>}
              </div>}
            </div>
          </>}
      </section>
    </div>
  )
}

// Notes as dots, links as lines: repulsion between every pair, springs along links, a pull to the centre.
function Graph({ notes, cur, onOpen }) {
  const canvas = useRef(null)
  const [hover, setHover] = useState(null)
  const sim = useRef(null)
  useEffect(() => {
    const list = notes.slice(0, 400), idx = Object.fromEntries(list.map((n, i) => [n.path, i]))
    const links = list.flatMap((n, i) => n.links.filter(l => idx[l] !== undefined).map(l => [i, idx[l]]))
    const deg = list.map(() => 0); links.forEach(([a, b]) => { deg[a]++; deg[b]++ })
    const pts = list.map((n, i) => ({ n, x: Math.cos(i * 2.4) * (60 + i * 3), y: Math.sin(i * 2.4) * (60 + i * 3), vx: 0, vy: 0, r: 4 + Math.min(10, Math.sqrt(deg[i]) * 2.4) }))
    sim.current = { pts, links }
    let frame, ticks = 0
    const step = () => {
      const k = Math.max(0.02, 1 - ticks / 260)
      for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) {
        const a = pts[i], b = pts[j], dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy + 0.01, f = (1400 / d2) * k
        a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f
      }
      for (const [i, j] of links) {
        const a = pts[i], b = pts[j], dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1, f = ((d - 90) / d) * 0.04 * k
        a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f
      }
      for (const p of pts) { p.vx -= p.x * 0.004 * k; p.vy -= p.y * 0.004 * k; p.x += p.vx; p.y += p.vy; p.vx *= 0.82; p.vy *= 0.82 }
      draw(); ticks++
      if (ticks < 320) frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [notes])                                                           // eslint-disable-line
  const view = () => {
    const c = canvas.current, { pts } = sim.current
    const xs = pts.map(p => p.x), ys = pts.map(p => p.y)
    const w = Math.max(1, Math.max(...xs) - Math.min(...xs)), h = Math.max(1, Math.max(...ys) - Math.min(...ys))
    const s = Math.min(2, Math.min((c.clientWidth - 80) / w, (c.clientHeight - 80) / h))
    return { s, ox: c.clientWidth / 2 - ((Math.max(...xs) + Math.min(...xs)) / 2) * s, oy: c.clientHeight / 2 - ((Math.max(...ys) + Math.min(...ys)) / 2) * s }
  }
  const draw = useCallback(() => {
    const c = canvas.current
    if (!c || !sim.current?.pts.length) return
    const dpr = devicePixelRatio || 1
    if (c.width !== c.clientWidth * dpr) { c.width = c.clientWidth * dpr; c.height = c.clientHeight * dpr }
    const g = c.getContext('2d'), { pts, links } = sim.current, { s, ox, oy } = view()
    g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, c.clientWidth, c.clientHeight)
    const near = hover && new Set([hover, ...links.filter(l => pts[l[0]].n.path === hover || pts[l[1]].n.path === hover).flatMap(([a, b]) => [pts[a].n.path, pts[b].n.path])])
    g.lineWidth = 1
    for (const [a, b] of links) {
      const on = near && near.has(pts[a].n.path) && near.has(pts[b].n.path) && (pts[a].n.path === hover || pts[b].n.path === hover)
      g.strokeStyle = on ? 'rgba(164,139,255,.9)' : 'rgba(170,160,230,.18)'
      g.beginPath(); g.moveTo(ox + pts[a].x * s, oy + pts[a].y * s); g.lineTo(ox + pts[b].x * s, oy + pts[b].y * s); g.stroke()
    }
    for (const p of pts) {
      const x = ox + p.x * s, y = oy + p.y * s, isCur = p.n.path === cur, faded = near && !near.has(p.n.path)
      g.globalAlpha = faded ? 0.25 : 1
      g.fillStyle = isCur ? '#ff6ec7' : p.n.path === hover ? '#ffffff' : '#a48bff'
      g.beginPath(); g.arc(x, y, p.r, 0, Math.PI * 2); g.fill()
      if (p.r > 6 || p.n.path === hover || isCur || pts.length < 40) {
        g.fillStyle = '#ecebf4'; g.font = '12px Segoe UI, system-ui, sans-serif'; g.textAlign = 'center'; g.fillText(p.n.title.slice(0, 32), x, y + p.r + 14)
      }
      g.globalAlpha = 1
    }
  }, [hover, cur])                                                     // eslint-disable-line
  useEffect(draw, [draw])
  const hit = e => {
    if (!sim.current) return null
    const r = canvas.current.getBoundingClientRect(), { s, ox, oy } = view()
    const mx = e.clientX - r.left, my = e.clientY - r.top
    return sim.current.pts.find(p => Math.hypot(ox + p.x * s - mx, oy + p.y * s - my) < p.r + 5)?.n.path || null
  }
  if (!notes.length) return <div className="studio-empty">No notes to graph yet.</div>
  return <canvas ref={canvas} className="graph" onMouseMove={e => setHover(hit(e))} onMouseLeave={() => setHover(null)}
    onClick={e => { const p = hit(e); if (p) onOpen(p) }} style={{ cursor: hover ? 'pointer' : 'default' }} />
}
