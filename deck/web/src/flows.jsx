import { useContext, useEffect, useRef, useState } from 'react'
import { api, ago, uid } from './api.js'
import { Modal } from './Composer.jsx'
import { DeckCtx } from './nodes.jsx'

// Flows (deck/flows.py), Langflow-style: drag from a box's right dot onto another box and its text
// flows in. An Agent step runs its instructions on what arrives; a note at the end of a wire gets the result.

export const PORT_Y = 19                                    // ports sit on the header's midline
const port = (n, side) => [side === 'out' ? n.x + n.w : n.x, n.y + PORT_Y]
const curve = ([x1, y1], [x2, y2]) => {
  const dx = Math.max(50, Math.abs(x2 - x1) / 2)
  return `M${x1} ${y1} C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`
}

export function WireLayer({ nodes, wires, drawing, onCut }) {
  const at = Object.fromEntries(nodes.map(n => [n.id, n]))
  return (
    <svg className="wires">
      {wires.map(w => {
        const a = at[w.from], b = at[w.to]
        if (!a || !b) return null
        const p1 = port(a, 'out'), p2 = port(b, 'in'), d = curve(p1, p2)
        const st = b.type === 'agent' ? b.run?.state : ''
        return (
          <g key={w.id} className={`wire${st === 'running' || st === 'testing' ? ' live' : st === 'failed' ? ' bad' : ''}`}>
            <path className="hit" d={d} />
            <path className="line" d={d} />
            <g className="cut" transform={`translate(${(p1[0] + p2[0]) / 2} ${(p1[1] + p2[1]) / 2})`} role="button" tabIndex={0}
              aria-label={`Remove the wire from ${a.title} to ${b.title}`} onClick={() => onCut(w)} onKeyDown={e => e.key === 'Enter' && onCut(w)}>
              <circle r="10" /><path d="M-3.5 -3.5 L3.5 3.5 M3.5 -3.5 L-3.5 3.5" />
            </g>
          </g>
        )
      })}
      {drawing && <path className="wire-draft" d={curve(drawing.from, drawing.to)} />}
    </svg>
  )
}

const RUN_LABEL = { running: 'Running…', testing: 'Testing…', done: 'Done', failed: 'Failed', tested: 'Tested' }

// Langflow's Agent box: instructions, which agent, run, the reply - and its examples (Tests).
export function AgentNode({ node }) {
  const { toast, open } = useContext(DeckCtx)
  const [text, setText] = useState(node.text || '')
  const run = node.run || {}
  const busy = run.state === 'running' || run.state === 'testing'
  const tests = node.tests || []
  const timer = useRef(0)
  useEffect(() => { if (!busy) setText(t => (document.activeElement?.dataset?.agent === node.id ? t : node.text || '')) }, [node.text]) // eslint-disable-line
  const save = (body, now) => {
    clearTimeout(timer.current)
    const go = () => api(`/api/nodes/${node.id}`, { method: 'PATCH', body }).catch(() => {})
    if (now) go(); else timer.current = setTimeout(go, 500)
  }
  const go = async () => {
    save({ text }, true)
    try { await api('/api/flows/run', { method: 'POST', body: { node: node.id } }) } catch (e) { toast(e.message) }
  }
  const status = run.state === 'tested' ? `Tested · ${run.passed}/${run.of} pass` : run.state === 'failed' ? `Failed · ${run.error || ''}`
    : RUN_LABEL[run.state] ? `${RUN_LABEL[run.state]}${run.at && !busy ? ` · ${ago(run.at)} ago` : ''}` : 'Not run yet'
  return (
    <div className="agent-node" onPointerDown={e => e.stopPropagation()} onWheel={e => e.stopPropagation()}>
      <textarea className="agent-ins" data-agent={node.id} value={text} spellCheck={false} disabled={busy}
        placeholder="What should this step do with what flows in? Write {input} where it goes - otherwise it follows."
        onChange={e => { setText(e.target.value); save({ text: e.target.value }) }} />
      <div className="agent-bar">
        <select value={node.engine || 'claude'} disabled={busy} aria-label="Which agent runs this step"
          onChange={e => save({ engine: e.target.value }, true)}>
          <option value="claude">Claude</option><option value="codex">Codex</option>
        </select>
        <button className="btn primary" disabled={busy} onClick={go}>{busy ? <span className="spin" aria-hidden="true" /> : '▶'} Run</button>
        <button className="btn" onClick={() => open({ tests: true, nodeId: node.id })}>Tests{tests.length ? ` · ${tests.length}` : ''}</button>
        <span className={`agent-st s-${run.state || 'idle'}`} title={status}>{status}</span>
      </div>
      <pre className={`agent-out${node.out ? '' : ' empty'}`}>{node.out || 'The reply lands here - and flows on to whatever this box is wired to.'}</pre>
    </div>
  )
}

// Langflow's Playground: examples run through the whole flow, each marked pass or miss, with every step's output.
export function TestsModal({ node, nodes, onClose }) {
  const { toast } = useContext(DeckCtx)
  const [rows, setRows] = useState(() => (node.tests?.length ? node.tests : [{ id: uid(), input: '', expect: '' }]))
  const [open, setOpen] = useState('')
  const busy = node.run?.state === 'testing' || node.run?.state === 'running'
  const title = Object.fromEntries(nodes.map(n => [n.id, n.title]))
  useEffect(() => { if (busy || node.run?.state === 'tested') setRows(node.tests || []) }, [node.tests, node.run?.state]) // eslint-disable-line
  const edit = (id, k, v) => setRows(rs => rs.map(r => (r.id === id ? { id: r.id, input: r.input, expect: r.expect, [k]: v } : r)))
  const keep = rs => rs.filter(r => r.input.trim()).map(({ id, input, expect }) => ({ id, input, expect }))
  const save = () => api(`/api/nodes/${node.id}`, { method: 'PATCH', body: { tests: keep(rows) } })
  const runAll = async () => {
    try { await save(); await api('/api/flows/test', { method: 'POST', body: { node: node.id } }) } catch (e) { toast(e.message) }
  }
  const done = rows.filter(r => 'ok' in r)
  return (
    <Modal title={`Tests · ${node.title}`} onClose={() => { if (!busy) save().catch(() => {}); onClose() }}
      sub="Real examples run through the whole flow - each input goes in where the flow starts, nothing on the board is changed. A miss shows every step's output, so you can fix just that step.">
      <div className="tests">
        {rows.map((r, i) => (
          <div key={r.id} className={`test${'ok' in r ? (r.ok ? ' pass' : ' miss') : ''}`}>
            <span className="test-n" aria-hidden="true">{'ok' in r ? (r.ok ? '✓' : '!') : i + 1}</span>
            <label className="fld"><small>Input</small>
              <textarea rows={2} value={r.input} disabled={busy} onChange={e => edit(r.id, 'input', e.target.value)} placeholder="e.g. Does it work with Slack?" /></label>
            <label className="fld"><small>The answer must contain</small>
              <input value={r.expect} disabled={busy} onChange={e => edit(r.id, 'expect', e.target.value)} placeholder="e.g. Slack integration (empty: any answer)" /></label>
            <button className="wbtn" disabled={busy} onClick={() => setRows(rs => rs.filter(x => x.id !== r.id))} aria-label={`Remove example ${i + 1}`}>✕</button>
            {'ok' in r && (
              <div className="test-got">
                <b>{r.ok ? 'Pass' : r.error ? 'Error' : 'Miss'}</b> {r.error || (r.got || '').slice(0, 280)}
                {Object.keys(r.steps || {}).length > 0 && (
                  <button className="ghost" onClick={() => setOpen(o => (o === r.id ? '' : r.id))} aria-expanded={open === r.id}>
                    {open === r.id ? 'Hide steps' : 'Show each step'}</button>
                )}
                {open === r.id && Object.entries(r.steps).map(([id, out]) => (
                  <div key={id} className="test-step"><small>{title[id] || 'Step'}</small><pre>{out || '(nothing)'}</pre></div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="sheet-f">
        <button className="btn" disabled={busy || rows.length >= 12} onClick={() => setRows(rs => [...rs, { id: uid(), input: '', expect: '' }])}>+ Example</button>
        <span>{busy ? 'Running the examples…' : done.length ? `${done.filter(r => r.ok).length} of ${done.length} pass` : 'Every example runs every step - it uses your agent limits.'}</span>
        <button className="btn primary" disabled={busy || !keep(rows).length} onClick={runAll}>{busy ? 'Running…' : '▶ Run tests'}</button>
      </div>
    </Modal>
  )
}
