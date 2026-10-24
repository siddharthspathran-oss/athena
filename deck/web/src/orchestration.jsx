import { useContext, useState } from 'react'
import { api, ago, KINDS } from './api.js'
import { Modal } from './Composer.jsx'
import { DeckCtx } from './nodes.jsx'

export const ROLES = ['lead', 'planner', 'builder', 'reviewer', 'tester', 'researcher', 'writer']
const TEAM_HUES = ['#a48bff', '#ff6ec7', '#3ddc97', '#f7b84b', '#6aa7ff', '#ff8a5c', '#4fd1c5', '#e879f9']
export function teamColor(name) {
  let h = 0
  for (const ch of name || '') h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return TEAM_HUES[h % TEAM_HUES.length]
}

const WHEN = { every: 'On a schedule', idle: 'When it goes idle', output: 'When its output matches' }
const describe = lp => (lp.mode === 'every' ? `every ${lp.every} min` : lp.mode === 'idle' ? `after ${lp.idle}s idle` : `on /${lp.match}/`)

// A console's loops: what it's told again and again, and when - until a goal is met or the run limit.
export function LoopModal({ node, onClose }) {
  const { toast } = useContext(DeckCtx)
  const [f, setF] = useState({ mode: 'every', every: 10, idle: 60, match: '', text: '', goal: '', max: 20 })
  const [busy, setBusy] = useState(false)
  const set = (k, v) => setF(x => ({ ...x, [k]: v }))
  const add = async () => {
    setBusy(true)
    try { await api(`/api/consoles/${node.id}/loops`, { method: 'POST', body: f }); toast('Loop added'); set('text', ''); set('goal', '') }
    catch (e) { toast(e.message) } finally { setBusy(false) }
  }
  const flip = (lp, body) => api(`/api/consoles/${node.id}/loops/${lp.id}`, { method: body ? 'PATCH' : 'DELETE', body }).catch(e => toast(e.message))
  return (
    <Modal onClose={onClose} title={`Loops · ${node.title}`} sub="Send this console the same message again and again. It never interrupts work in progress.">
      {(node.loops || []).length > 0 && (
        <ul className="loops">{node.loops.map(lp => (
          <li key={lp.id} className={lp.on ? 'on' : ''}>
            <span className="lp-dot" aria-hidden="true" />
            <div className="lp-main"><b>{lp.text}</b>
              <small>{describe(lp)} · {lp.runs}/{lp.max} runs{lp.goal ? ` · until “${lp.goal}”` : ''}{lp.last ? ` · last ${ago(lp.last)} ago` : ''}{lp.note ? ` · ${lp.note}` : ''}</small></div>
            <button className="btn" onClick={() => flip(lp, { on: !lp.on })}>{lp.on ? 'Pause' : 'Resume'}</button>
            <button className="ghost icon" onClick={() => flip(lp)} aria-label="Delete loop">✕</button>
          </li>))}</ul>
      )}
      <div className="seg" role="radiogroup" aria-label="When">
        {Object.entries(WHEN).map(([k, label]) => <button key={k} role="radio" aria-checked={f.mode === k} className={f.mode === k ? 'on' : ''} onClick={() => set('mode', k)}>{label}</button>)}
      </div>
      <div className="fld-row" style={{ marginBottom: 12 }}>
        {f.mode === 'every' && <label className="fld grow">Every <input type="number" min={1} max={1440} value={f.every} onChange={e => set('every', e.target.value)} /> <small>minutes, when it's resting</small></label>}
        {f.mode === 'idle' && <label className="fld grow">After <input type="number" min={10} max={86400} value={f.idle} onChange={e => set('idle', e.target.value)} /> <small>seconds idle - once per idle spell</small></label>}
        {f.mode === 'output' && <label className="fld grow">Output matches <input value={f.match} onChange={e => set('match', e.target.value)} placeholder="FAILED|Error" spellCheck={false} /> <small>a word or regex</small></label>}
        <label className="fld" style={{ width: 120 }}>Max runs<input type="number" min={1} max={500} value={f.max} onChange={e => set('max', e.target.value)} /></label>
      </div>
      <label className="fld">Message<textarea rows={2} value={f.text} onChange={e => set('text', e.target.value)} placeholder="e.g. Run the tests and fix what fails" /></label>
      <label className="fld">Goal <small>(optional - the loop retires itself when the agent says it's met)</small>
        <input value={f.goal} onChange={e => set('goal', e.target.value)} placeholder="e.g. every test passes" /></label>
      <div className="sheet-f"><span>{f.text ? `Sends: “${f.text.slice(0, 60)}${f.text.length > 60 ? '…' : ''}”` : ''}</span>
        <button className="btn primary" disabled={busy || !f.text.trim()} onClick={add}>Add loop</button></div>
    </Modal>
  )
}

// Pass this console's work to another agent: it writes a brief, the new one reads it and carries on.
export function HandoffModal({ node, onClose }) {
  const { consoles, toast } = useContext(DeckCtx)
  const others = consoles.filter(c => c.id !== node.id)
  const [to, setTo] = useState('new:claude')
  const [task, setTask] = useState('')
  const [summarize, setSummarize] = useState(['claude', 'codex', 'gemini'].includes(node.kind))
  const go = async () => {
    const [mode, val] = to.split(':')
    try {
      await api('/api/handoff', { method: 'POST', body: { from: node.id, task, summarize, ...(mode === 'new' ? { kind: val } : { to: val }) } })
      toast(summarize ? `${node.title} is writing its brief…` : 'Handing off…'); onClose()
    } catch (e) { toast(e.message) }
  }
  return (
    <Modal small onClose={onClose} title={`Hand off · ${node.title}`} sub="The outgoing agent writes a brief into .deck/handoffs/; the next agent reads it and carries on.">
      <label className="fld">To
        <select className="sel" value={to} onChange={e => setTo(e.target.value)}>
          <optgroup label="A new console beside it">
            {['claude', 'codex', 'gemini'].map(k => <option key={k} value={`new:${k}`}>New {KINDS[k].label}</option>)}
          </optgroup>
          {others.length > 0 && <optgroup label="An open console">{others.map(c => <option key={c.id} value={`to:${c.id}`}>{c.title}</option>)}</optgroup>}
        </select></label>
      <label className="fld">What should they do next? <small>(optional - otherwise they carry on)</small>
        <textarea rows={2} value={task} onChange={e => setTask(e.target.value)} placeholder="e.g. Finish the settings page and write its tests" /></label>
      <label className="check"><input type="checkbox" checked={summarize} onChange={e => setSummarize(e.target.checked)} />
        Ask {node.title} to write the brief first <small>(uses its limits; otherwise its last screen is passed on)</small></label>
      <div className="sheet-f"><span /><button className="btn primary" onClick={go}>Hand off</button></div>
    </Modal>
  )
}

// Which team a console is on, and its part in it.
export function TeamModal({ node, onClose }) {
  const { consoles, toast } = useContext(DeckCtx)
  const [team, setTeam] = useState(node.team || '')
  const [role, setRole] = useState(node.role || '')
  const teams = [...new Set(consoles.map(c => c.team).filter(Boolean))]
  const save = async () => {
    try { await api(`/api/nodes/${node.id}`, { method: 'PATCH', body: { team: team.trim(), role: role.trim() } }); onClose() }
    catch (e) { toast(e.message) }
  }
  return (
    <Modal small onClose={onClose} title={`Team · ${node.title}`} sub="Group consoles into teams; the mission map can show them by team.">
      <label className="fld">Team<input value={team} onChange={e => setTeam(e.target.value)} list="deck-teams" placeholder="e.g. Checkout revamp" autoFocus />
        <datalist id="deck-teams">{teams.map(t => <option key={t} value={t} />)}</datalist></label>
      <label className="fld">Role<input value={role} onChange={e => setRole(e.target.value)} list="deck-roles" placeholder="builder, reviewer…" />
        <datalist id="deck-roles">{ROLES.map(r => <option key={r} value={r} />)}</datalist></label>
      <div className="sheet-f"><button className="btn" onClick={() => { setTeam(''); setRole('') }}>Clear</button><button className="btn primary" onClick={save}>Save</button></div>
    </Modal>
  )
}
