import { useContext, useEffect, useRef, useState } from 'react'
import { api, TOKEN } from './api.js'
import { Modal } from './Composer.jsx'
import { DeckCtx } from './nodes.jsx'

const CLI = { claude: 'Claude Code', codex: 'Codex' }

// Account profiles: a second Claude Code or Codex account, each with its own sign-in folder.
export function ProfilesModal({ profiles, onClose }) {
  const { toast } = useContext(DeckCtx)
  const [cli, setCli] = useState('claude')
  const [name, setName] = useState('')
  const add = async () => {
    try { await api('/api/profiles', { method: 'POST', body: { cli, name } }); setName(''); toast('Profile added - open a console with it and sign in once') }
    catch (e) { toast(e.message) }
  }
  return (
    <Modal small onClose={onClose} title="Accounts" sub="Run two accounts of the same CLI side by side. Each profile keeps its own sign-in folder.">
      {profiles.length > 0 && <ul className="loops">{profiles.map(p => (
        <li key={p.id} className="on"><span className="lp-dot" aria-hidden="true" />
          <div className="lp-main"><b>{p.name}</b><small>{CLI[p.cli]} · {p.dir}</small></div>
          <button className="ghost icon" aria-label={`Forget ${p.name}`} onClick={() => api(`/api/profiles/${p.id}`, { method: 'DELETE' }).catch(e => toast(e.message))}>✕</button>
        </li>))}</ul>}
      <div className="seg" role="radiogroup" aria-label="CLI">{Object.entries(CLI).map(([k, l]) =>
        <button key={k} role="radio" aria-checked={cli === k} className={cli === k ? 'on' : ''} onClick={() => setCli(k)}>{l}</button>)}</div>
      <label className="fld">Profile name<input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Work" onKeyDown={e => e.key === 'Enter' && name.trim() && add()} /></label>
      <p className="dim small">A new profile starts signed out: open a console with it from the Add menu and sign in there. Forgetting a profile keeps its folder on disk.</p>
      <div className="sheet-f"><span /><button className="btn primary" disabled={!name.trim()} onClick={add}>Add profile</button></div>
    </Modal>
  )
}

// Project memory: what every agent on this project should know - kept here, and only reaches a
// console when you push it.
export function MemoryModal({ memory, ws, onClose }) {
  const { consoles, toast } = useContext(DeckCtx)
  const [scope, setScope] = useState(ws.id)
  const [texts, setTexts] = useState({ global: memory.global || '', [ws.id]: memory[ws.id] || '' })
  const [saved, setSaved] = useState(true)
  const [to, setTo] = useState('')
  const timer = useRef(0)
  const here = consoles.filter(c => c.ws === ws.id)
  useEffect(() => { if (!to && here[0]) setTo(here[0].id) }, [here.length])                      // eslint-disable-line
  const edit = v => {
    setTexts(t => ({ ...t, [scope]: v })); setSaved(false); clearTimeout(timer.current)
    timer.current = setTimeout(() => api(`/api/memory/${scope}`, { method: 'PUT', body: { text: v } }).then(() => setSaved(true)).catch(e => toast(e.message)), 600)
  }
  const push = async () => {
    const parts = [texts[ws.id] && `Project memory (${ws.name}):\n${texts[ws.id].trim()}`, texts.global && `General memory:\n${texts.global.trim()}`].filter(Boolean)
    if (!parts.length || !to) return
    try {
      await api(`/api/consoles/${to}/send`, { method: 'POST', body: { text: `Keep this in mind for the work here:\n\n${parts.join('\n\n')}`, submit: true } })
      toast(`Memory pushed to ${consoles.find(c => c.id === to)?.title}`)
    } catch (e) { toast(e.message) }
  }
  return (
    <Modal onClose={onClose} title="Memory" sub="What your agents should know. It stays here until you push it to a console - nothing is sent on its own.">
      <div className="seg" role="tablist">{[[ws.id, `This project · ${ws.name}`], ['global', 'Everywhere']].map(([k, l]) =>
        <button key={k} role="tab" aria-selected={scope === k} className={scope === k ? 'on' : ''} onClick={() => setScope(k)}>{l}</button>)}</div>
      <label className="fld"><textarea rows={12} value={texts[scope]} onChange={e => edit(e.target.value)} spellCheck={false}
        placeholder={scope === 'global' ? 'e.g. I work on Windows 11. Prefer small diffs. Never push without asking.' : 'e.g. The API lives in server/, the UI in web/. Run tests with npm test. Deploys go through main.'} /></label>
      <div className="sheet-f"><span className="dim small">{saved ? 'Saved' : 'Saving…'}</span>
        <div className="fld-row" style={{ gap: 8, alignItems: 'center' }}>
          {here.length ? <select className="sel" style={{ marginTop: 0, width: 220 }} value={to} onChange={e => setTo(e.target.value)} aria-label="Console">
            {here.map(c => <option key={c.id} value={c.id}>{c.title}</option>)}</select> : <span className="dim small">No consoles on this project</span>}
          <button className="btn primary" disabled={!to || !(texts.global || texts[ws.id])} onClick={push}>Push to console</button>
        </div></div>
    </Modal>
  )
}

// Dictation: talk, and the words are typed into the console - you read them and press Enter.
// Heard on this PC by Athena's Whisper (deck/server.py /api/dictate); nothing is sent until you do.
export function DictateButton({ node }) {
  const { toast } = useContext(DeckCtx)
  const [state, setState] = useState('idle')        // idle | rec | busy
  const rec = useRef(null)
  const start = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const r = new MediaRecorder(stream), chunks = []
      r.ondataavailable = e => e.data.size && chunks.push(e.data)
      r.onstop = async () => {
        stream.getTracks().forEach(t => t.stop())
        setState('busy')
        try {
          const blob = new Blob(chunks, { type: r.mimeType || 'audio/webm' })
          const res = await fetch('/api/dictate', { method: 'POST', headers: { 'X-Token': TOKEN, 'Content-Type': blob.type }, body: blob })
          const j = await res.json()
          if (!res.ok) throw new Error(j.error || res.statusText)
          if (j.text) await api(`/api/consoles/${node.id}/send`, { method: 'POST', body: { text: j.text, submit: false } })
          else toast("Didn't catch that")
        } catch (e) { toast(e.message) } finally { setState('idle') }
      }
      rec.current = r; r.start(); setState('rec')
    } catch (e) { toast(`No microphone: ${e.message}`) }
  }
  return (
    <button className={`nbtn mic-btn ${state}`} title={state === 'rec' ? 'Stop - the words get typed in' : 'Dictate into this console'}
      aria-pressed={state === 'rec'} disabled={state === 'busy'} onClick={() => (state === 'rec' ? rec.current?.stop() : start())}>
      {state === 'busy' ? '…' : '🎙'}
    </button>
  )
}
