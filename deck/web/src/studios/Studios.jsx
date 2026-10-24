import { useEffect, useState } from 'react'
import { CodeStudio } from './CodeStudio.jsx'
import { DiagramStudio, DocStudio } from './MakeStudios.jsx'
import { NotesStudio } from './NotesStudio.jsx'

const TABS = [['code', 'Code'], ['notes', 'Markdown'], ['docs', 'Documents'], ['diagram', 'Diagrams']]

// The studios, over the board: browse code, keep notes, have an agent write docs or draw the repo.
export default function Studios({ workspaces, active, onClose }) {
  const [tab, setTab] = useState(() => { try { return localStorage.getItem('deck_studio') || 'code' } catch { return 'code' } })
  const [root, setRoot] = useState(active.folder)
  const pick = t => { setTab(t); try { localStorage.setItem('deck_studio', t) } catch {} }
  useEffect(() => { const k = e => e.key === 'Escape' && !e.target.closest?.('textarea,input') && onClose(); window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k) }, [onClose])
  const folders = [...new Map(workspaces.map(w => [w.folder, w])).values()]
  return (
    <div className="studio" role="dialog" aria-label="Studios">
      <header className="studio-h">
        <b className="studio-brand">Studios</b>
        <nav className="seg studio-tabs" role="tablist">{TABS.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => pick(k)}>{l}</button>)}</nav>
        <span className="grow" />
        <label className="studio-root">Folder
          <select value={root} onChange={e => setRoot(e.target.value)}>{folders.map(w => <option key={w.folder} value={w.folder}>{w.name} — {w.folder}</option>)}</select></label>
        <button className="ghost icon" onClick={onClose} aria-label="Close studios">✕</button>
      </header>
      <div className="studio-body">
        {tab === 'code' && <CodeStudio root={root} ws={active.id} />}
        {tab === 'notes' && <NotesStudio root={root} />}
        {tab === 'docs' && <DocStudio root={root} />}
        {tab === 'diagram' && <DiagramStudio root={root} ws={active.id} />}
      </div>
    </div>
  )
}
