import { useEffect, useState } from 'react'
import { api, pickFolder } from './api.js'
import { ROLES } from './orchestration.jsx'

const CYCLE = ['', 'claude', 'codex', 'gemini', 'shell']
const LABEL = { claude: 'Claude', codex: 'Codex', gemini: 'Antigravity', shell: 'PowerShell' }
const PRESETS = [
  ['Solo Claude', ['claude']],
  ['Claude + Codex', ['claude', 'codex']],
  ['Claude + Antigravity', ['claude', 'gemini']],
  ['Builder trio', ['claude', 'claude', 'gemini']],
  ['Squad of four', ['claude', 'claude', 'gemini', 'shell']],
  ['Full floor', ['claude', 'claude', 'claude', 'gemini', 'gemini', 'shell']],
]
// Presets that come with a sensible split of work (used once a team name is given).
const pad = r => [...r, ...Array(12 - r.length).fill('')]
const PRESET_ROLES = {
  'Claude + Codex': pad(['builder', 'reviewer']),
  'Claude + Antigravity': pad(['builder', 'researcher']),
  'Builder trio': pad(['lead', 'builder', 'researcher']),
  'Squad of four': pad(['lead', 'builder', 'researcher', '']),
  'Full floor': pad(['lead', 'builder', 'tester', 'researcher', 'reviewer', '']),
}

// Line up a team and launch it in one action (deck/board.py composer).
export function Composer({ ws, origin, onClose, toast }) {
  const [slots, setSlots] = useState(['claude', 'gemini', ...Array(10).fill('')])
  const [roles, setRoles] = useState(Array(12).fill(''))
  const [team, setTeam] = useState('')
  const [folder, setFolder] = useState(ws?.folder || '')
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState(false)
  const filled = slots.map((kind, i) => ({ kind, role: roles[i], i })).filter(s => s.kind)
  const cycle = i => setSlots(s => s.map((k, j) => (j === i ? CYCLE[(CYCLE.indexOf(k) + 1) % CYCLE.length] : k)))
  const launch = async () => {
    if (!filled.length) return
    setBusy(true)
    try {
      await api('/api/composer', { method: 'POST', body: { ws: ws.id, cwd: folder, prompt, origin, team, slots: filled.map(({ kind, role }) => ({ kind, role })) } })
      toast(`Launched ${filled.length} console${filled.length > 1 ? 's' : ''}`)
      onClose()
    } catch (e) { toast(`Couldn't launch: ${e.message}`) } finally { setBusy(false) }
  }
  return (
    <Modal onClose={onClose} title="Composer" sub="Line up a team and launch it in one go. Click a slot to change it.">
      <div className="presets">{PRESETS.map(([n, ks]) => (
        <button key={n} className="preset" onClick={() => { setSlots([...ks, ...Array(12 - ks.length).fill('')]); setRoles(PRESET_ROLES[n] || Array(12).fill('')) }}>{n}</button>))}</div>
      <div className="slots">{slots.map((k, i) => (
        <button key={i} className={`slot${k ? ' on ' + k : ''}`} onClick={() => cycle(i)} aria-label={`Slot ${i + 1}: ${LABEL[k] || 'empty'}`}>
          {k ? <><b>{LABEL[k].toUpperCase()}</b><span>console {i + 1}</span></> : <span>+ empty</span>}
        </button>))}</div>
      <label className="fld">Team <small>(optional — groups them on the board and the mission map)</small>
        <input value={team} onChange={e => setTeam(e.target.value)} placeholder="e.g. Checkout revamp" /></label>
      {team.trim() && (
        <div className="roles">{filled.map(s => (
          <label key={s.i}><span>{LABEL[s.kind]} · console {s.i + 1}</span>
            <input value={s.role} list="deck-roles" placeholder="role, e.g. builder" onChange={e => setRoles(r => r.map((x, j) => (j === s.i ? e.target.value : x)))} /></label>))}
          <datalist id="deck-roles">{ROLES.map(r => <option key={r} value={r} />)}</datalist></div>
      )}
      <label className="fld">Folder<div className="fld-row"><input value={folder} onChange={e => setFolder(e.target.value)} spellCheck={false} />
        <button className="btn" onClick={async () => { const f = await pickFolder(); if (f) setFolder(f) }}>Browse…</button></div></label>
      <label className="fld">First instruction for every agent <small>(optional — each starts on it straight away{team.trim() ? ', told its role' : ''})</small>
        <textarea rows={2} value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="e.g. Read the README and tell me what this project does" /></label>
      <div className="sheet-f"><span>{filled.length} console{filled.length === 1 ? '' : 's'}{prompt && filled.some(s => s.kind !== 'shell') ? ' · agents start on it now (uses your limits)' : ''}</span>
        <button className="btn primary" disabled={!filled.length || busy} onClick={launch}>{busy ? 'Launching…' : `Launch ${filled.length || ''}`}</button></div>
    </Modal>
  )
}

export function WorkspaceModal({ onClose, toast }) {
  const [folder, setFolder] = useState('')
  const [name, setName] = useState('')
  const go = async () => {
    try { await api('/api/workspaces', { method: 'POST', body: { folder, name } }); onClose() } catch (e) { toast(e.message) }
  }
  return (
    <Modal onClose={onClose} title="New workspace" sub="A workspace is a board for one project folder." small>
      <label className="fld">Folder<div className="fld-row"><input value={folder} onChange={e => setFolder(e.target.value)} spellCheck={false} placeholder="C:\claude code\athena-sites" autoFocus />
        <button className="btn" onClick={async () => { const f = await pickFolder(); if (f) setFolder(f) }}>Browse…</button></div></label>
      <label className="fld">Name<input value={name} onChange={e => setName(e.target.value)} placeholder="defaults to the folder's name" onKeyDown={e => e.key === 'Enter' && go()} /></label>
      <div className="sheet-f"><span /><button className="btn primary" disabled={!folder.trim()} onClick={go}>Create</button></div>
    </Modal>
  )
}

export function AskModal({ title, sub, placeholder, onDone, onClose }) {
  const [v, setV] = useState('')
  const go = () => { if (v.trim()) { onDone(v.trim()); onClose() } }
  return (
    <Modal onClose={onClose} title={title} sub={sub} small>
      <label className="fld"><input value={v} onChange={e => setV(e.target.value)} placeholder={placeholder} spellCheck={false} autoFocus onKeyDown={e => e.key === 'Enter' && go()} /></label>
      <div className="sheet-f"><span /><button className="btn primary" disabled={!v.trim()} onClick={go}>Open</button></div>
    </Modal>
  )
}

export function Modal({ title, sub, small, onClose, children }) {
  useEffect(() => { const k = e => e.key === 'Escape' && onClose(); window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k) }, [onClose])
  return (
    <div className="modal" role="dialog" aria-label={title} onPointerDown={e => e.target === e.currentTarget && onClose()}>
      <div className={`sheet${small ? ' small' : ''}`}>
        <div className="sheet-h"><div><h2>{title}</h2><p>{sub}</p></div><button className="ghost icon" onClick={onClose} aria-label="Close">✕</button></div>
        {children}
      </div>
    </div>
  )
}
