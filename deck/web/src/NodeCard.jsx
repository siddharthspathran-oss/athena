import { memo, useContext, useEffect, useRef, useState } from 'react'
import Terminal from './Terminal.jsx'
import { api, ago, money, KINDS, STATE_LABEL } from './api.js'
import { Checklist, DeckCtx, GitCard, ImageNode, Kanban, Note, TextNode, WebTile } from './nodes.jsx'
import { teamColor } from './orchestration.jsx'
import { DictateButton } from './continuity.jsx'
import { FileNode } from './studios/CodeStudio.jsx'
import { DiagramNode } from './studios/MakeStudios.jsx'
import { AgentNode } from './flows.jsx'

const BODY = { note: Note, web: WebTile, text: TextNode, checklist: Checklist, kanban: Kanban, image: ImageNode, git: GitCard,
  file: FileNode, diagram: DiagramNode, agent: AgentNode }

// One thing on the board: a console, or a note, text, checklist, kanban, image, git card or web tile.
// Drag it by its header, resize from the corner; zoom comes from zoomRef so panning never re-renders it.
function NodeCard({ node, status, cost, selected, z, zoomRef, onSelect, onMoved, termKey, fed }) {
  const [pos, setPos] = useState({ x: node.x, y: node.y, w: node.w, h: node.h })
  const [editing, setEditing] = useState(false)
  const [sure, setSure] = useState(false)
  const [more, setMore] = useState(false)
  const { open, wire, toast } = useContext(DeckCtx)
  const focusTerm = useRef(null)
  const loopsOn = (node.loops || []).filter(l => l.on).length
  useEffect(() => { setPos({ x: node.x, y: node.y, w: node.w, h: node.h }) }, [node.x, node.y, node.w, node.h])
  useEffect(() => { if (!sure) return; const t = setTimeout(() => setSure(false), 3500); return () => clearTimeout(t) }, [sure])

  const kind = node.type === 'console' ? node.kind : node.type
  const st = node.type === 'console' ? (status?.state || 'starting') : 'idle'
  const Body = BODY[node.type]

  const drag = (e, mode) => {
    if (e.button !== 0 || e.target.closest('button,[contenteditable="true"],input')) return
    e.preventDefault(); e.stopPropagation(); onSelect(node.id)
    const sx = e.clientX, sy = e.clientY, start = { ...pos }, zz = zoomRef.current || 1
    let last = start
    document.body.classList.add('dragging')               // browser tiles would swallow the drag otherwise
    const move = ev => {
      const dx = (ev.clientX - sx) / zz, dy = (ev.clientY - sy) / zz
      last = mode === 'move' ? { ...start, x: Math.round(start.x + dx), y: Math.round(start.y + dy) }
        : { ...start, w: Math.max(260, Math.round(start.w + dx)), h: Math.max(160, Math.round(start.h + dy)) }
      setPos(last)
      onMoved(node.id, last)                               // wires follow while it moves
    }
    const up = () => {
      document.body.classList.remove('dragging')
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up)
      if (last !== start) { api(`/api/nodes/${node.id}`, { method: 'PATCH', body: last }); onMoved(node.id, last) }
    }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }

  const rename = e => {
    setEditing(false)
    const t = e.target.textContent.trim()
    if (t && t !== node.title) api(`/api/nodes/${node.id}`, { method: 'PATCH', body: { title: t } })
  }
  const close = () => {
    const live = node.type === 'console' && status?.alive
    if (live && !sure) { setSure(true); return }                       // a live console only dies on a confirmed tap
    api(`/api/nodes/${node.id}`, { method: 'DELETE' })
  }
  const restart = fresh => api(`/api/nodes/${node.id}/restart`, { method: 'POST', body: { fresh } })
  const runFlow = () => api('/api/flows/run', { method: 'POST', body: { node: node.id } }).catch(e => toast(e.message))

  return (
    <div className={`node k-${kind} s-${st}${selected ? ' sel' : ''}`} data-id={node.id}
      style={{ left: pos.x, top: pos.y, width: pos.w, height: pos.h, zIndex: z }} onPointerDown={() => onSelect(node.id)}>
      <span className={`port in${fed ? ' on' : ''}`} aria-hidden="true" />
      <button className="port out" title="Drag onto another box to send this one's text into it"
        aria-label={`Wire ${node.title} into another box`} onPointerDown={e => wire(e, node.id)} />
      <div className="nh" onPointerDown={e => drag(e, 'move')} onDoubleClick={e => { if (e.target.classList.contains('nt')) setEditing(true) }}>
        <span className="sd" aria-hidden="true" />
        <span className={`kb ${KINDS[kind] ? kind : 'extra'}`}>{KINDS[kind]?.short || kind.toUpperCase()}</span>
        <span className="nt" contentEditable={editing} suppressContentEditableWarning spellCheck={false}
          onBlur={rename} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); e.target.blur() } }}>{node.title}</span>
        {node.team && <button className="team-chip" style={{ '--tc': teamColor(node.team) }} onClick={() => open({ team: true, nodeId: node.id })}
          title={`Team ${node.team}${node.role ? ` · ${node.role}` : ''}`}><i />{node.team}{node.role ? <em>{node.role}</em> : null}</button>}
        {node.type === 'console' && <span className="st">{STATE_LABEL[st] || st}{status?.since ? ` · ${ago(status.since)}` : ''}</span>}
        {loopsOn > 0 && <button className="loop-chip" onClick={() => open({ loops: true, nodeId: node.id })} title="Loops on this console">⟳ {loopsOn}</button>}
        {cost > 0 && <span className="cost" title="This console's Claude spend at API prices">{money(cost)}</span>}
        {fed && node.type !== 'agent' && <button className="nbtn run" title="Run everything wired into this box" onClick={runFlow}>▶</button>}
        {node.type === 'console' && st !== 'exited' && <DictateButton node={node} />}
        {node.type === 'console' && <button className="nbtn" title="Restart (resumes the conversation)" onClick={() => restart(false)}>↻</button>}
        {node.type === 'console' && (
          <div className="more" onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) setMore(false) }}>
            <button className="nbtn" aria-haspopup="menu" aria-expanded={more} title="More" onClick={() => setMore(m => !m)}>⋯</button>
            {more && (
              <div className="menu" role="menu" onClick={() => setMore(false)}>
                <button role="menuitem" onClick={() => open({ loops: true, nodeId: node.id })}>⟳ Loops…</button>
                <button role="menuitem" onClick={() => open({ handoff: true, nodeId: node.id })}>⇢ Hand off…</button>
                <button role="menuitem" onClick={() => open({ team: true, nodeId: node.id })}>◎ Team &amp; role…</button>
                <hr />
                <button role="menuitem" onClick={() => restart(true)}>✦ Fresh start (new conversation)</button>
              </div>
            )}
          </div>
        )}
        <button className={`nbtn danger`} title={sure ? 'Tap again to stop it' : 'Close'} onClick={close}>{sure ? 'Stop?' : '✕'}</button>
      </div>
      {st === 'needs' && (
        <div className="need-bar"><b>Needs you</b><span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{status?.detail || 'waiting for your answer'}</span>
          <button className="btn" style={{ padding: '3px 10px' }} onClick={() => focusTerm.current?.()}>Jump in</button></div>
      )}
      <div className="nb">
        {node.type === 'console' && <Terminal key={termKey} id={node.id} focusRef={focusTerm} />}
        {node.type === 'console' && st === 'exited' && (
          <div className="dead"><button className="btn primary" onClick={() => restart(false)}>Resume</button><button className="btn" onClick={() => restart(true)}>Fresh start</button></div>
        )}
        {Body && <Body node={node} />}
      </div>
      <div className="rz" onPointerDown={e => drag(e, 'size')} />
    </div>
  )
}

export default memo(NodeCard)
