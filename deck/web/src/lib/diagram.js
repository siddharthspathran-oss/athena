// Diagrams from the Diagram studio: {title, nodes:[{id,label,group}], edges:[{from,to,label}]}
// laid out top-down in layers, drawn in a hand-inked style, exported as SVG, PNG or .excalidraw.
import { esc } from './hl.js'

export const GROUP = { ui: '#a48bff', service: '#6aa7ff', data: '#3ddc97', external: '#f7b84b', other: '#9aa4bd' }
const H = 56, GAP_X = 44, GAP_Y = 96, PAD = 40

const width = label => Math.max(120, Math.min(260, Math.round(label.length * 8.4 + 36)))

// A layered (Sugiyama-style) layout, top-down:
// 1. layers by longest path from the roots (back edges ignored, so a cycle can't loop forever);
// 2. an edge that skips layers gets an invisible waypoint in each layer it crosses, which holds a lane
//    for it - so it bends around the boxes in between instead of running through them;
// 3. barycentre ordering, four sweeps, to uncross edges; 4. ports spread along each box's edge.
const LANE = 18
export function layout(d) {
  const ids = d.nodes.map(n => n.id)
  const seen = new Set(), stack = new Set(), fwd = new Set()
  const visit = v => {
    seen.add(v); stack.add(v)
    for (const e of d.edges.filter(e => e.from === v)) {
      if (stack.has(e.to)) continue                                  // a back edge: drawn, not ranked
      fwd.add(e)
      if (!seen.has(e.to)) visit(e.to)
    }
    stack.delete(v)
  }
  ids.forEach(v => { if (!seen.has(v)) visit(v) })
  const inn0 = Object.fromEntries(ids.map(i => [i, []]))
  fwd.forEach(e => inn0[e.to].push(e.from))
  const rank = {}
  const rankOf = v => (rank[v] ??= inn0[v].length ? Math.max(...inn0[v].map(rankOf)) + 1 : 0)
  ids.forEach(rankOf)
  const out = Object.fromEntries(ids.map(i => [i, []])), inn = Object.fromEntries(ids.map(i => [i, []]))
  const chains = new Map()                                           // edge -> its waypoint ids, top to bottom
  d.edges.forEach((e, k) => {
    if (!fwd.has(e)) return
    let prev = e.from
    const way = []
    for (let r = rank[e.from] + 1; r < rank[e.to]; r++) {
      const w = `~${k}_${r}`
      rank[w] = r; out[w] = []; inn[w] = []; way.push(w)
      out[prev].push(w); inn[w].push(prev); prev = w
    }
    out[prev].push(e.to); inn[e.to].push(prev)
    chains.set(e, way)
  })
  const layers = []
  Object.keys(rank).forEach(v => (layers[rank[v]] ||= []).push(v))
  const pos = {}
  const place = () => layers.forEach(l => l.forEach((v, i) => { pos[v] = i }))
  place()
  for (let pass = 0; pass < 4; pass++) {
    const down = pass % 2 === 0
    for (const l of down ? layers.slice(1) : layers.slice(0, -1).reverse()) {
      const bary = v => { const ns = down ? inn[v] : out[v]; return ns.length ? ns.reduce((a, n) => a + pos[n], 0) / ns.length : pos[v] }
      l.sort((a, b) => bary(a) - bary(b))
      place()
    }
  }
  const by = Object.fromEntries(d.nodes.map(n => [n.id, n]))
  const rows = layers.map(l => l.map(v => (by[v] ? { ...by[v], w: width(by[v].label), h: H } : { id: v, dummy: true, w: LANE, h: H })))
  const rowW = r => r.reduce((a, n) => a + n.w, 0) + GAP_X * (r.length - 1)
  const W = Math.max(...rows.map(rowW))
  const all = []
  rows.forEach((r, y) => {
    let x = PAD + (W - rowW(r)) / 2
    r.forEach(n => { all.push({ ...n, x, y: PAD + 44 + y * (H + GAP_Y) }); x += n.w + GAP_X })
  })
  const at = Object.fromEntries(all.map(n => [n.id, n]))
  // Ports: the arrows leaving a box's bottom (or entering its top) are spread along it, left to right
  // in the order of whatever they connect to, so their heads don't land on top of each other.
  const ports = {}
  const port = (node, side, other, k) => {                            // keyed by the edge: two edges between the same pair still part
    const entry = { x: other.x + other.w / 2, k }
    ;(ports[node.id + side] ||= []).push(entry)
    return () => {
      const list = ports[node.id + side].slice().sort((a, b) => a.x - b.x || a.k - b.k), i = list.indexOf(entry)
      return node.x + (node.w * (i + 1)) / (list.length + 1)
    }
  }
  const edges = d.edges.map((e, k) => {
    const a = at[e.from], b = at[e.to], way = (chains.get(e) || []).map(w => at[w])
    const down = b.y > a.y, side = b.y === a.y
    if (side) return { ...e, pts: () => [[a.x + (b.x > a.x ? a.w : 0), a.y + H / 2], [b.x + (b.x > a.x ? 0 : b.w), b.y + H / 2]] }
    const first = way[0] || b, last = way[way.length - 1] || a
    const xa = port(a, down ? 'b' : 't', first, k), xb = port(b, down ? 't' : 'b', last, k)
    return { ...e, pts: () => [[xa(), down ? a.y + H : a.y], ...way.map(w => [w.x + w.w / 2, w.y + H / 2]), [xb(), down ? b.y : b.y + H]] }
  }).map(e => { const pts = e.pts(); return { ...e, pts, p1: pts[0], p2: pts[pts.length - 1] } })
  return { title: d.title, nodes: all.filter(n => !n.dummy), edges, width: W + PAD * 2, height: PAD * 2 + 44 + rows.length * (H + GAP_Y) - GAP_Y }
}

// A little wobble, the same every time for the same shape (seeded by its id).
function rng(seed) {
  let h = 2166136261
  for (const ch of String(seed)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619)
  return () => { h = Math.imul(h ^ (h >>> 15), 2246822507); h = Math.imul(h ^ (h >>> 13), 3266489909); return ((h ^= h >>> 16) >>> 0) / 4294967296 }
}
export function inkRect(n) {
  const r = rng(n.id), j = () => (r() - 0.5) * 3.2
  const { x, y, w, h } = n, k = 10
  const one = () => `M${x + k + j()} ${y + j()} L${x + w - k + j()} ${y + j()} Q${x + w} ${y} ${x + w + j()} ${y + k + j()} ` +
    `L${x + w + j()} ${y + h - k + j()} Q${x + w} ${y + h} ${x + w - k + j()} ${y + h + j()} L${x + k + j()} ${y + h + j()} ` +
    `Q${x} ${y + h} ${x + j()} ${y + h - k + j()} L${x + j()} ${y + k + j()} Q${x} ${y} ${x + k + j()} ${y + j()}`
  return [one(), one()]
}
// An arrow through its points: a hand-drawn bow for a direct one, smooth curves through the
// waypoints of a long one; the head follows the last stretch, the label sits at the middle.
export function inkEdge(e) {
  const r = rng(e.from + e.to), pts = e.pts || [e.p1, e.p2]
  let body, ctrl
  if (pts.length === 2) {
    const [[x1, y1], [x2, y2]] = pts
    ctrl = [(x1 + x2) / 2 + (r() - 0.5) * 18, (y1 + y2) / 2 + (r() - 0.5) * 10]
    body = `M${x1} ${y1} Q${ctrl[0]} ${ctrl[1]} ${x2} ${y2}`
  } else {
    body = `M${pts[0][0]} ${pts[0][1]}`
    for (let i = 1; i < pts.length - 1; i++) {
      const [x, y] = pts[i], [nx, ny] = pts[i + 1]
      body += ` Q${x} ${y} ${i === pts.length - 2 ? nx : (x + nx) / 2} ${i === pts.length - 2 ? ny : (y + ny) / 2}`
    }
    ctrl = pts[pts.length - 2]
  }
  const [x2, y2] = pts[pts.length - 1], ang = Math.atan2(y2 - ctrl[1], x2 - ctrl[0]), a = 11
  const head = `M${x2 - a * Math.cos(ang - 0.42)} ${y2 - a * Math.sin(ang - 0.42)} L${x2} ${y2} L${x2 - a * Math.cos(ang + 0.42)} ${y2 - a * Math.sin(ang + 0.42)}`
  const mid = pts.length === 2 ? [(pts[0][0] + 2 * ctrl[0] + x2) / 4, (pts[0][1] + 2 * ctrl[1] + y2) / 4] : pts[Math.floor(pts.length / 2)]
  return { body, head, mid }
}

// A standalone SVG (light or dark), for export and for drawing onto a canvas for PNG.
export function toSVG(L, dark = true) {
  const fg = dark ? '#ecebf4' : '#1d1b26', bg = dark ? '#0d0c14' : '#ffffff', dim = dark ? '#8d8ba6' : '#6b6880'
  const font = 'Segoe UI, system-ui, sans-serif'
  const parts = [`<svg xmlns="http://www.w3.org/2000/svg" width="${L.width}" height="${L.height}" viewBox="0 0 ${L.width} ${L.height}">`,
    `<rect width="100%" height="100%" fill="${bg}"/>`,
    `<text x="${PAD}" y="${PAD + 10}" font-family="${font}" font-size="20" font-weight="700" fill="${fg}">${esc(L.title)}</text>`]
  for (const e of L.edges) {
    const g = inkEdge(e)
    parts.push(`<path d="${g.body}" fill="none" stroke="${dim}" stroke-width="2" stroke-linecap="round"/><path d="${g.head}" fill="none" stroke="${dim}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`)
    if (e.label) parts.push(`<text x="${g.mid[0]}" y="${g.mid[1] - 4}" text-anchor="middle" font-family="${font}" font-size="12" fill="${dim}" paint-order="stroke" stroke="${bg}" stroke-width="4">${esc(e.label)}</text>`)
  }
  for (const n of L.nodes) {
    const c = GROUP[n.group] || GROUP.other, [a, b] = inkRect(n)
    parts.push(`<rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="10" fill="${c}" fill-opacity="${dark ? 0.14 : 0.18}"/>`,
      `<path d="${a}" fill="none" stroke="${c}" stroke-width="2"/><path d="${b}" fill="none" stroke="${c}" stroke-width="1" opacity=".55"/>`,
      `<text x="${n.x + n.w / 2}" y="${n.y + n.h / 2 + 5}" text-anchor="middle" font-family="${font}" font-size="14" font-weight="600" fill="${fg}">${esc(n.label)}</text>`)
  }
  return parts.join('') + '</svg>'
}

// PNG (base64, no prefix) at 2x, drawn from the SVG.
export function toPNG(L, dark = true) {
  return new Promise((ok, no) => {
    const img = new Image(), url = URL.createObjectURL(new Blob([toSVG(L, dark)], { type: 'image/svg+xml' }))
    img.onload = () => {
      const c = document.createElement('canvas'); c.width = L.width * 2; c.height = L.height * 2
      const g = c.getContext('2d'); g.scale(2, 2); g.drawImage(img, 0, 0); URL.revokeObjectURL(url)
      ok(c.toDataURL('image/png').split(',')[1])
    }
    img.onerror = e => { URL.revokeObjectURL(url); no(e) }
    img.src = url
  })
}

// Excalidraw's file format: rectangles with their labels bound inside, arrows bound to both ends.
export function toExcalidraw(L) {
  const now = Date.now(), seed = () => Math.floor(Math.random() * 2 ** 31)
  const base = { angle: 0, strokeStyle: 'solid', roughness: 1, opacity: 100, groupIds: [], frameId: null, isDeleted: false,
    link: null, locked: false, updated: now, version: 1 }
  const els = [], arrowsOf = {}
  L.edges.forEach((e, i) => { (arrowsOf[e.from] ||= []).push(`a${i}`); (arrowsOf[e.to] ||= []).push(`a${i}`) })
  els.push({ ...base, id: 'title', type: 'text', x: PAD, y: PAD - 8, width: L.title.length * 11, height: 25, text: L.title, originalText: L.title,
    fontSize: 20, fontFamily: 1, textAlign: 'left', verticalAlign: 'top', containerId: null, lineHeight: 1.25, strokeColor: '#1e1e1e',
    backgroundColor: 'transparent', fillStyle: 'solid', strokeWidth: 1, roundness: null, seed: seed(), versionNonce: seed(), boundElements: null })
  for (const n of L.nodes) {
    const c = GROUP[n.group] || GROUP.other
    els.push({ ...base, id: `n_${n.id}`, type: 'rectangle', x: n.x, y: n.y, width: n.w, height: n.h, strokeColor: c, backgroundColor: c + '33',
      fillStyle: 'solid', strokeWidth: 2, roundness: { type: 3 }, seed: seed(), versionNonce: seed(),
      boundElements: [{ type: 'text', id: `t_${n.id}` }, ...(arrowsOf[n.id] || []).map(id => ({ type: 'arrow', id }))] })
    els.push({ ...base, id: `t_${n.id}`, type: 'text', x: n.x + 8, y: n.y + n.h / 2 - 12, width: n.w - 16, height: 24, text: n.label, originalText: n.label,
      fontSize: 16, fontFamily: 1, textAlign: 'center', verticalAlign: 'middle', containerId: `n_${n.id}`, lineHeight: 1.25, strokeColor: '#1e1e1e',
      backgroundColor: 'transparent', fillStyle: 'solid', strokeWidth: 1, roundness: null, seed: seed(), versionNonce: seed(), boundElements: null })
  }
  L.edges.forEach((e, i) => {
    const pts = e.pts || [e.p1, e.p2], [x1, y1] = pts[0], xs = pts.map(p => p[0]), ys = pts.map(p => p[1])
    els.push({ ...base, id: `a${i}`, type: 'arrow', x: x1, y: y1, width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys),
      points: pts.map(([x, y]) => [x - x1, y - y1]),
      strokeColor: '#495057', backgroundColor: 'transparent', fillStyle: 'solid', strokeWidth: 2, roundness: { type: 2 }, seed: seed(), versionNonce: seed(),
      boundElements: null, lastCommittedPoint: null, startArrowhead: null, endArrowhead: 'arrow',
      startBinding: { elementId: `n_${e.from}`, focus: 0, gap: 4 }, endBinding: { elementId: `n_${e.to}`, focus: 0, gap: 4 } })
  })
  return JSON.stringify({ type: 'excalidraw', version: 2, source: 'athena-deck', elements: els, appState: { viewBackgroundColor: '#ffffff', gridSize: null }, files: {} }, null, 1)
}
