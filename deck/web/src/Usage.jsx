import { useEffect, useRef, useState } from 'react'
import { Modal } from './Composer.jsx'

// Token use per CLI, from their own logs on this PC (deck/usage.py). One small chart per CLI, each
// on its own scale: Claude's cache reads run ~600x Codex's, a shared axis would flatten Codex to nothing.
// Colours validated for the dark surface (dataviz validator: CVD 10.3, both in the L band).
const SERIES = [['claude', 'Claude Code', '#c96a38'], ['codex', 'Codex', '#319a80']]

export const fmt = n => (n >= 1e9 ? `${(n / 1e9).toFixed(1)}B` : n >= 1e6 ? `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`
  : n >= 1e3 ? `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k` : String(n))
const niceMax = v => { if (!v) return 1; const p = 10 ** Math.floor(Math.log10(v)), m = v / p; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p }
const dayLabel = d => new Date(d + 'T12:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

export function UsagePills({ usage, onOpen }) {
  if (!usage) return null
  const c = usage.today.claude.total, x = usage.today.codex.total, lim = usage.codex_limit
  return (
    <button className="chip usage-pill" onClick={onOpen} title="Tokens used today, from the CLIs' own logs - click for 14 days">
      <i style={{ background: SERIES[0][2] }} />Claude {fmt(c)}
      <span className="sep" /><i style={{ background: SERIES[1][2] }} />Codex {lim?.used != null ? `${lim.used}%` : fmt(x)}
    </button>
  )
}

function Bars({ title, sub, color, rows }) {
  const box = useRef(null)
  const [w, setW] = useState(560)
  const [hov, setHov] = useState(-1)
  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, Math.floor(e.contentRect.width))))
    ro.observe(box.current); return () => ro.disconnect()
  }, [])
  const H = 196, L = 48, R = 10, T = 12, B = 28, n = rows.length
  const max = niceMax(Math.max(...rows.map(r => r.total)))
  const col = (w - L - R) / n, bw = Math.max(5, Math.min(26, col - 8))
  const y = v => T + (H - T - B) * (1 - v / max)
  const bar = (i, v) => {
    const x = L + i * col + (col - bw) / 2, top = y(v), base = y(0), h = base - top
    if (v <= 0) return null
    const r = Math.min(4, bw / 2, h)
    return `M${x} ${base} V${top + r} Q${x} ${top} ${x + r} ${top} H${x + bw - r} Q${x + bw} ${top} ${x + bw} ${top + r} V${base} Z`
  }
  const h = hov >= 0 ? rows[hov] : null
  return (
    <figure className="uchart">
      <figcaption><b>{title}</b><span>{sub}</span></figcaption>
      <div ref={box} className="uplot" onMouseLeave={() => setHov(-1)}>
        <svg width={w} height={H} role="img" aria-label={`${title}: tokens per day for ${n} days`}>
          {[0, max / 2, max].map(t => <g key={t}><line x1={L} x2={w - R} y1={y(t)} y2={y(t)} className="ugrid" />
            <text x={L - 8} y={y(t) + 4} textAnchor="end" className="utick">{fmt(t)}</text></g>)}
          {rows.map((r, i) => <path key={r.day} d={bar(i, r.total) || ''} fill={color} opacity={hov < 0 || hov === i ? 1 : 0.45} />)}
          {rows.map((r, i) => (i % 2 === (n - 1) % 2) && <text key={r.day} x={L + i * col + col / 2} y={H - 8} textAnchor="middle" className="utick">{dayLabel(r.day).replace(/^\w+ /, '')}</text>)}
          {rows.map((r, i) => <rect key={r.day} x={L + i * col} y={T} width={col} height={H - T - B} fill="transparent" onMouseEnter={() => setHov(i)} />)}
        </svg>
        {h && (
          <div className="utip" style={{ left: Math.min(w - 180, Math.max(0, L + hov * col + col / 2 - 90)) }}>
            <b>{dayLabel(h.day)}</b><span className="big">{h.total.toLocaleString()} tokens</span>
            <span><i>fresh input</i>{h.in.toLocaleString()}</span><span><i>cache</i>{h.cache.toLocaleString()}</span><span><i>output</i>{h.out.toLocaleString()}</span>
          </div>
        )}
      </div>
    </figure>
  )
}

export function UsageModal({ usage, onClose }) {
  const [table, setTable] = useState(false)
  if (!usage) return null
  const lim = usage.codex_limit
  const sum = k => usage.series[k].reduce((a, r) => a + r.total, 0)
  return (
    <Modal onClose={onClose} title="Usage" sub="Tokens your CLIs used, counted from their own session logs on this PC. Nothing leaves it.">
      <div className="usage-top">
        {SERIES.map(([k, label]) => <div key={k} className="ustat"><span>{label} · today</span><b>{fmt(usage.today[k].total)}</b>
          <small>{fmt(usage.today[k].out)} output · {fmt(sum(k))} in 14 days</small></div>)}
        {lim && <div className="ustat"><span>Codex allowance</span><b>{lim.used}%</b>
          <div className="ubar" role="meter" aria-valuenow={lim.used} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${Math.min(100, lim.used)}%` }} /></div>
          <small>of {Math.round((lim.window_min || 0) / 1440)} days{lim.resets_at ? ` · resets ${new Date(lim.resets_at * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}` : ''}</small></div>}
      </div>
      <div className="seg mini-seg" style={{ width: 'fit-content', marginBottom: 10 }}>
        <button className={!table ? 'on' : ''} onClick={() => setTable(false)}>Charts</button><button className={table ? 'on' : ''} onClick={() => setTable(true)}>Table</button></div>
      {!table ? SERIES.map(([k, label, color]) => (
        <Bars key={k} title={label} sub="tokens per day, last 14 days" color={color} rows={usage.series[k]} />))
        : <div className="tbl scrolls"><table className="utable"><thead><tr><th>Day</th>{SERIES.map(([k, l]) => <th key={k}>{l}</th>)}{SERIES.map(([k, l]) => <th key={k + 'o'}>{l} output</th>)}</tr></thead>
          <tbody>{[...usage.days].reverse().map((d, i) => { const j = usage.days.length - 1 - i; return (
            <tr key={d}><td>{dayLabel(d)}</td>{SERIES.map(([k]) => <td key={k}>{usage.series[k][j].total.toLocaleString()}</td>)}
              {SERIES.map(([k]) => <td key={k + 'o'}>{usage.series[k][j].out.toLocaleString()}</td>)}</tr>) })}</tbody></table></div>}
      <p className="dim small" style={{ marginTop: 10 }}>Most of Claude's count is cache reads - cheap re-reads of the conversation so far. Antigravity keeps no token log, so it isn't shown.</p>
    </Modal>
  )
}
