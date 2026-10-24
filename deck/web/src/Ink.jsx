import { useEffect, useRef, useState } from 'react'
import { api } from './api.js'
import { INKS } from './nodes.jsx'

// Freehand ink on the board, in board coordinates - it pans and zooms with everything else and
// sits over images and cards, so you can mark them up in place. Strokes live on the workspace.

// A smooth path through the points: quadratic curves between midpoints.
export function pathOf(pts) {
  if (pts.length < 2) return ''
  let d = `M${pts[0][0]} ${pts[0][1]}`
  for (let i = 1; i < pts.length - 1; i++) {
    const [x, y] = pts[i], [nx, ny] = pts[i + 1]
    d += ` Q${x} ${y} ${(x + nx) / 2} ${(y + ny) / 2}`
  }
  const [lx, ly] = pts[pts.length - 1]
  return d + ` L${lx} ${ly}`
}

// Fewer points, same shape (Ramer-Douglas-Peucker).
export function simplify(pts, eps) {
  if (pts.length < 3) return pts
  const [ax, ay] = pts[0], [bx, by] = pts[pts.length - 1]
  const dx = bx - ax, dy = by - ay, len = Math.hypot(dx, dy) || 1
  let worst = 0, at = 0
  for (let i = 1; i < pts.length - 1; i++) {
    const d = Math.abs(dy * pts[i][0] - dx * pts[i][1] + bx * ay - by * ax) / len
    if (d > worst) { worst = d; at = i }
  }
  if (worst <= eps) return [pts[0], pts[pts.length - 1]]
  return [...simplify(pts.slice(0, at + 1), eps).slice(0, -1), ...simplify(pts.slice(at), eps)]
}

const near = (s, x, y, r) => s.pts.some((p, i) => {
  const q = s.pts[i + 1]
  if (!q) return Math.hypot(p[0] - x, p[1] - y) < r
  const dx = q[0] - p[0], dy = q[1] - p[1], L = dx * dx + dy * dy || 1
  const t = Math.max(0, Math.min(1, ((x - p[0]) * dx + (y - p[1]) * dy) / L))
  return Math.hypot(p[0] + t * dx - x, p[1] + t * dy - y) < r + s.s / 2
})

// The strokes themselves: drawn inside #world, above the nodes, never in the way of a click.
export function InkLayer({ strokes, live }) {
  return (
    <svg className="ink" width="1" height="1" aria-hidden="true">
      {strokes.map(s => <path key={s.id} d={pathOf(s.pts)} stroke={s.c} strokeWidth={s.s} />)}
      {live && <path d={pathOf(live.pts)} stroke={live.c} strokeWidth={live.s} />}
    </svg>
  )
}

// While inking: a sheet over the stage that turns the pointer into strokes (or erases them).
export function InkTools({ ws, strokes, view, stageRef, setLive, onExit, toast }) {
  const [tool, setTool] = useState('pen')
  const [color, setColor] = useState(INKS[1])
  const [size, setSize] = useState(4)
  const [sure, setSure] = useState(false)
  const mine = useRef([]), gone = useRef(new Set()), last = useRef(null)
  const toWorld = e => {
    const r = stageRef.current.getBoundingClientRect()
    return [(e.clientX - r.left - view.x) / view.z, (e.clientY - r.top - view.y) / view.z]
  }
  // Erase along the whole swipe since the last pointer event, not just where it ended: a fast
  // flick reports few points and would jump straight over a stroke.
  const erase = to => {
    const from = last.current || to, r = 6 / view.z
    const steps = Math.max(1, Math.ceil(Math.hypot(to[0] - from[0], to[1] - from[1]) / r))
    for (let i = 0; i <= steps; i++) {
      const x = from[0] + ((to[0] - from[0]) * i) / steps, y = from[1] + ((to[1] - from[1]) * i) / steps
      strokes.forEach(s => {
        if (gone.current.has(s.id) || !near(s, x, y, r)) return
        gone.current.add(s.id)
        api(`/api/workspaces/${ws}/ink?stroke=${s.id}`, { method: 'DELETE' }).catch(() => {})
      })
    }
    last.current = to
  }
  const down = e => {
    if (e.button !== 0) return
    e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId)
    last.current = null
    if (tool === 'erase') { erase(toWorld(e)); return }
    setLive({ pts: [toWorld(e)], c: color, s: size })
  }
  const move = e => {
    if (!(e.buttons & 1)) return
    if (tool === 'erase') { erase(toWorld(e)); return }
    const p = toWorld(e)
    setLive(l => (l ? { ...l, pts: [...l.pts, p] } : l))
  }
  const up = () => setLive(l => {
    if (l && l.pts.length > 1) {
      const stroke = { ...l, pts: simplify(l.pts, 0.6 / view.z) }
      api(`/api/workspaces/${ws}/ink`, { method: 'POST', body: stroke }).then(s => mine.current.push(s.id)).catch(e => toast(e.message))
    }
    return null
  })
  const undo = () => {
    const id = mine.current.pop() || strokes[strokes.length - 1]?.id
    if (id) api(`/api/workspaces/${ws}/ink?stroke=${id}`, { method: 'DELETE' }).catch(() => {})
  }
  useEffect(() => {
    const k = e => {
      if (e.key === 'Escape') onExit()
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); undo() }
    }
    window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k)
  })
  useEffect(() => { if (!sure) return; const t = setTimeout(() => setSure(false), 3000); return () => clearTimeout(t) }, [sure])
  return (
    <>
      <div className={`ink-sheet ${tool}`} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} />
      <div className="ink-bar" role="toolbar" aria-label="Ink">
        <button className={`wbtn${tool === 'pen' ? ' on' : ''}`} onClick={() => setTool('pen')} title="Pen">✎</button>
        <button className={`wbtn${tool === 'erase' ? ' on' : ''}`} onClick={() => setTool('erase')} title="Eraser">⌫</button>
        <span className="sep" />
        {INKS.map(c => <button key={c} className={`swatch${c === color && tool === 'pen' ? ' on' : ''}`} style={{ background: c }}
          onClick={() => { setColor(c); setTool('pen') }} aria-label={`Ink colour ${c}`} />)}
        <span className="sep" />
        {[2, 4, 9].map(s => <button key={s} className={`wbtn${s === size ? ' on' : ''}`} onClick={() => setSize(s)} aria-label={`Pen size ${s}`}>
          <i className="dot" style={{ width: s + 2, height: s + 2 }} /></button>)}
        <span className="sep" />
        <button className="wbtn" onClick={undo} title="Undo (Ctrl+Z)">↶</button>
        <button className="wbtn wide" onClick={() => {
          if (!sure) { setSure(true); return }
          api(`/api/workspaces/${ws}/ink`, { method: 'DELETE' }); setSure(false)
        }}>{sure ? 'Clear all?' : 'Clear'}</button>
        <button className="btn primary" onClick={onExit}>Done</button>
      </div>
    </>
  )
}
