import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api, ago, toUrl, uid, ELECTRON } from './api.js'

// What every node body may need from the board: all consoles (to send a card to one), a toast.
export const DeckCtx = createContext({ consoles: [], status: {}, toast: () => {} })

// Save a node's field a moment after the last change (typing, dragging cards).
function usePatch(id, delay = 500) {
  const t = useRef(0)
  return useCallback((body, now = false) => {
    clearTimeout(t.current)
    const go = () => api(`/api/nodes/${id}`, { method: 'PATCH', body }).catch(() => {})
    if (now) go(); else t.current = setTimeout(go, delay)
  }, [id, delay])
}
const stop = e => e.stopPropagation()

export function Note({ node }) {
  const [text, setText] = useState(node.text || '')
  const patch = usePatch(node.id, 600)
  const ref = useRef(null)
  // A flow (flows.py) can fill it: take the board's text unless you're typing in it.
  useEffect(() => { if (document.activeElement !== ref.current) setText(node.text || '') }, [node.text])
  return <textarea ref={ref} className="note-t" value={text} placeholder="Plans, links, what to tell the agents…" spellCheck={false}
    onChange={e => { setText(e.target.value); patch({ text: e.target.value }) }} onPointerDown={stop} onWheel={stop} />
}

// Big free text on the board - a heading for a group of consoles, a label on a diagram.
export function TextNode({ node }) {
  const patch = usePatch(node.id, 600)
  const [size, setSize] = useState(node.size || 30)
  const [color, setColor] = useState(node.color || '#ecebf4')
  const [html] = useState(() => ({ __html: esc(node.text ?? 'Text') }))      // set once: a re-render mustn't move the caret
  const body = useRef(null)
  useEffect(() => {                                                           // filled by a flow: shown unless you're editing
    const el = body.current
    if (el && document.activeElement !== el && node.text != null && el.innerText !== node.text) el.innerText = node.text
  }, [node.text])
  const set = (k, v) => { k === 'size' ? setSize(v) : setColor(v); patch({ [k]: v }, true) }
  return (
    <div className="text-node" onPointerDown={stop}>
      <div ref={body} className="text-body" contentEditable suppressContentEditableWarning spellCheck={false} style={{ fontSize: size, color }}
        onInput={e => patch({ text: e.currentTarget.innerText })} dangerouslySetInnerHTML={html} />
      <div className="text-tools">
        <button className="wbtn" onClick={() => set('size', Math.max(12, size - 4))} aria-label="Smaller text">A−</button>
        <button className="wbtn" onClick={() => set('size', Math.min(120, size + 4))} aria-label="Bigger text">A+</button>
        {INKS.map(c => <button key={c} className={`swatch${c === color ? ' on' : ''}`} style={{ background: c }} onClick={() => set('color', c)} aria-label={`Colour ${c}`} />)}
      </div>
    </div>
  )
}
export const INKS = ['#ecebf4', '#a48bff', '#ff6ec7', '#f7b84b', '#3ddc97', '#6aa7ff', '#ff6b7a']
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))

export function Checklist({ node }) {
  const [items, setItems] = useState(node.items || [])
  const [draft, setDraft] = useState('')
  const patch = usePatch(node.id, 300)
  const save = next => { setItems(next); patch({ items: next }) }
  const add = () => { if (draft.trim()) { save([...items, { id: uid(), text: draft.trim(), done: false }]); setDraft('') } }
  const done = items.filter(i => i.done).length
  return (
    <div className="list-node" onPointerDown={stop} onWheel={stop}>
      <div className="list-prog"><i style={{ width: `${items.length ? (done / items.length) * 100 : 0}%` }} /><span>{done}/{items.length}</span></div>
      <ul>{items.map(i => (
        <li key={i.id} className={i.done ? 'done' : ''}>
          <label><input type="checkbox" checked={i.done} onChange={() => save(items.map(x => (x.id === i.id ? { ...x, done: !x.done } : x)))} />
            <span contentEditable suppressContentEditableWarning spellCheck={false}
              onBlur={e => { const t = e.currentTarget.textContent.trim(); if (t !== i.text) save(items.map(x => (x.id === i.id ? { ...x, text: t } : x))) }}>{i.text}</span></label>
          <button className="mini" onClick={() => save(items.filter(x => x.id !== i.id))} aria-label={`Remove ${i.text}`}>✕</button>
        </li>))}</ul>
      <input className="list-add" value={draft} placeholder="+ Add an item" onChange={e => setDraft(e.target.value)} onKeyDown={e => e.key === 'Enter' && add()} />
    </div>
  )
}

// To do / Doing / Done (or your own columns). Drag cards between columns; send a card to a console as a task.
export function Kanban({ node }) {
  const { consoles, toast } = useContext(DeckCtx)
  const [cols, setCols] = useState(node.columns || [])
  const [drafts, setDrafts] = useState({})
  const [sending, setSending] = useState('')
  const [hover, setHover] = useState(null)                 // while dragging a card: {card, col, before}
  const patch = usePatch(node.id, 300)
  const save = next => { setCols(next); patch({ columns: next }) }
  const addCard = cid => {
    const t = (drafts[cid] || '').trim()
    if (!t) return
    save(cols.map(c => (c.id === cid ? { ...c, cards: [...c.cards, { id: uid(), text: t }] } : c))); setDrafts({ ...drafts, [cid]: '' })
  }
  const move = (cardId, toCol, before) => {
    let card = null
    const without = cols.map(c => ({ ...c, cards: c.cards.filter(k => (k.id === cardId ? ((card = k), false) : true)) }))
    if (!card) return
    save(without.map(c => {
      if (c.id !== toCol) return c
      const i = before ? c.cards.findIndex(k => k.id === before) : -1
      const cards = [...c.cards]; cards.splice(i < 0 ? cards.length : i, 0, card)
      return { ...c, cards }
    }))
  }
  // Pointer dragging (not HTML5 drag-and-drop): works at any board zoom and with touch, and a plain
  // click still edits the card's text - the drag only starts once the pointer has moved a few pixels.
  const grab = (e, card) => {
    if (e.button !== 0 || e.target.closest('button')) return
    const sx = e.clientX, sy = e.clientY, root = e.currentTarget.closest('.kanban-node')
    let at = null
    const mv = ev => {
      if (!at && Math.hypot(ev.clientX - sx, ev.clientY - sy) < 6) return
      if (!at) { document.activeElement?.blur?.(); window.getSelection()?.removeAllRanges(); document.body.classList.add('kdrag') }
      const hit = document.elementFromPoint(ev.clientX, ev.clientY)
      const col = hit?.closest('.kcol[data-col]')
      at = { card: card.id, col: col && root.contains(col) ? col.dataset.col : null, before: hit?.closest('.kcard')?.dataset.id }
      if (at.before === card.id) at.before = undefined
      setHover(at)
    }
    const up = () => {
      window.removeEventListener('pointermove', mv); window.removeEventListener('pointerup', up)
      document.body.classList.remove('kdrag'); setHover(null)
      if (at?.col) move(card.id, at.col, at.before)
    }
    window.addEventListener('pointermove', mv); window.addEventListener('pointerup', up)
  }
  const sendTo = async (card, cid) => {
    setSending('')
    try {
      await api(`/api/consoles/${cid}/send`, { method: 'POST', body: { text: card.text, submit: true } })
      const doing = cols.find(c => /doing|progress/i.test(c.title))
      if (doing) move(card.id, doing.id)
      toast(`Sent to ${consoles.find(c => c.id === cid)?.title || 'the console'}`)
    } catch (e) { toast(e.message) }
  }
  return (
    <div className="kanban-node" onPointerDown={stop} onWheel={stop}>
      {cols.map(col => (
        <section key={col.id} data-col={col.id} className={`kcol${hover?.col === col.id ? ' drop' : ''}`}>
          <header><span contentEditable suppressContentEditableWarning spellCheck={false}
            onBlur={e => { const t = e.currentTarget.textContent.trim(); if (t && t !== col.title) save(cols.map(c => (c.id === col.id ? { ...c, title: t } : c))) }}>{col.title}</span>
            <b>{col.cards.length}</b>
            {cols.length > 1 && !col.cards.length && <button className="mini" onClick={() => save(cols.filter(c => c.id !== col.id))} aria-label={`Remove column ${col.title}`}>✕</button>}
          </header>
          <div className="kcards">
            {col.cards.map(card => (
              <div key={card.id} data-id={card.id} onPointerDown={e => grab(e, card)}
                className={`kcard${hover?.card === card.id ? ' lifted' : ''}${hover?.col === col.id && hover?.before === card.id ? ' gap' : ''}`}>
                <span contentEditable suppressContentEditableWarning spellCheck={false}
                  onBlur={e => { const t = e.currentTarget.textContent.trim(); if (t !== card.text) save(cols.map(c => ({ ...c, cards: c.cards.map(k => (k.id === card.id ? { ...k, text: t } : k)) }))) }}>{card.text}</span>
                <div className="kact">
                  {consoles.length > 0 && <button className="mini" title="Give this to a console" onClick={() => setSending(sending === card.id ? '' : card.id)}>→</button>}
                  <button className="mini" onClick={() => save(cols.map(c => ({ ...c, cards: c.cards.filter(k => k.id !== card.id) })))} aria-label="Delete card">✕</button>
                </div>
                {sending === card.id && (
                  <div className="kpick">{consoles.map(c => <button key={c.id} onClick={() => sendTo(card, c.id)}><i className={`k ${c.kind}`} />{c.title}</button>)}</div>
                )}
              </div>
            ))}
          </div>
          <input className="kadd" value={drafts[col.id] || ''} placeholder="+ Add a card" onChange={e => setDrafts({ ...drafts, [col.id]: e.target.value })}
            onKeyDown={e => e.key === 'Enter' && addCard(col.id)} />
        </section>
      ))}
      <button className="kcol knew" onClick={() => save([...cols, { id: uid(), title: 'New column', cards: [] }])}>+ Column</button>
    </div>
  )
}

export function ImageNode({ node }) {
  return <div className="img-node"><img src={node.src} alt={node.title} draggable={false} /></div>
}

// A live card for one repo: branch, what changed, recent commits, and commit / pull / push / fetch.
const XY = { M: 'mod', A: 'add', D: 'del', R: 'ren', '?': 'new', U: 'conf' }
export function GitCard({ node }) {
  const { toast } = useContext(DeckCtx)
  const [st, setSt] = useState(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')
  const [out, setOut] = useState('')
  const [path, setPath] = useState(node.path)
  const patch = usePatch(node.id, 0)
  const load = useCallback(() => api(`/api/git?path=${encodeURIComponent(path)}`).then(setSt).catch(e => setSt({ repo: false, error: e.message })), [path])
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t) }, [load])
  const run = async (act, arg = '') => {
    setBusy(act); setOut('')
    try {
      const r = await api(`/api/git/${act}`, { method: 'POST', body: { path, arg } })
      setOut(r.out); if (r.ok && act === 'commit') setMsg('')
      toast(r.ok ? `git ${act}: done` : `git ${act} failed`)
    } catch (e) { setOut(e.message) } finally { setBusy(''); load() }
  }
  if (!st) return <div className="git-node dim">Reading the repo…</div>
  return (
    <div className="git-node" onPointerDown={stop} onWheel={stop}>
      <div className="git-path"><input value={path} onChange={e => setPath(e.target.value)} onBlur={() => patch({ path }, true)} spellCheck={false} aria-label="Repository folder" /></div>
      {!st.repo ? <p className="dim">{st.error}</p> : <>
        <div className="git-head">
          <select value={st.branch} disabled={!!busy} onChange={e => run('checkout', e.target.value)} aria-label="Branch">
            {st.branches.map(b => <option key={b.name} value={b.name}>{b.name}</option>)}
          </select>
          {st.ahead > 0 && <span className="gbadge up">↑{st.ahead}</span>}{st.behind > 0 && <span className="gbadge down">↓{st.behind}</span>}
          <span className="dim small">{st.upstream || 'no upstream'}</span>
          <span className="grow" />
          {['fetch', 'pull', 'push'].map(a => <button key={a} className="wbtn wide" disabled={!!busy} onClick={() => run(a)}>{busy === a ? '…' : a}</button>)}
        </div>
        <div className="git-sec"><b>Changes</b><span>{st.changed}</span></div>
        <ul className="git-files">{st.files.slice(0, 60).map(f => (
          <li key={f.path}><i className={XY[f.xy.trim()[0]] || 'mod'}>{f.xy.trim() || 'M'}</i><span title={f.path}>{f.path}</span></li>))}
          {!st.changed && <li className="dim">Nothing to commit.</li>}</ul>
        <div className="git-commit">
          <input value={msg} onChange={e => setMsg(e.target.value)} placeholder="Commit message" onKeyDown={e => e.key === 'Enter' && msg.trim() && run('commit', msg)} />
          <button className="btn primary" disabled={!msg.trim() || !st.changed || !!busy} onClick={() => run('commit', msg)}>{busy === 'commit' ? '…' : 'Commit all'}</button>
        </div>
        {out && <pre className="git-out">{out}</pre>}
        <div className="git-sec"><b>Recent commits</b></div>
        <ul className="git-log">{st.commits.map(c => <li key={c.hash}><code>{c.hash}</code><span>{c.subject}</span><em>{ago(c.time)}</em></li>)}</ul>
      </>}
    </div>
  )
}

// A browser tile. In the Electron shell it's a real browser (<webview>): any site, sign-ins kept
// in one saved profile, and a Web Inspector that docks under the page. Elsewhere it's an iframe.
export function WebTile({ node }) {
  const [url, setUrl] = useState(node.url)
  const [shown, setShown] = useState(node.url)
  const [n, setN] = useState(0)
  const [loading, setLoading] = useState(false)
  const [inspect, setInspect] = useState(false)
  const view = useRef(null), tools = useRef(null)
  const save = u => api(`/api/nodes/${node.id}`, { method: 'PATCH', body: { url: u } }).catch(() => {})
  useEffect(() => {
    const w = view.current
    if (!ELECTRON || !w) return
    const nav = e => { setUrl(e.url); save(e.url) }
    const inPage = e => { if (e.isMainFrame) setUrl(e.url) }
    const on = () => setLoading(true), off = () => setLoading(false)
    w.addEventListener('did-navigate', nav); w.addEventListener('did-navigate-in-page', inPage)
    w.addEventListener('did-start-loading', on); w.addEventListener('did-stop-loading', off)
    return () => {
      w.removeEventListener('did-navigate', nav); w.removeEventListener('did-navigate-in-page', inPage)
      w.removeEventListener('did-start-loading', on); w.removeEventListener('did-stop-loading', off)
    }
  }, [])                                                                            // eslint-disable-line
  useEffect(() => {                                                                 // dock DevTools into the lower webview
    const host = tools.current, page = view.current
    if (!inspect || !host || !page) return
    let target = 0
    const dock = () => {
      try { target = page.getWebContentsId(); window.deckShell.dockDevTools(target, host.getWebContentsId()) } catch {}
    }
    host.addEventListener('dom-ready', dock, { once: true })
    return () => { host.removeEventListener('dom-ready', dock); if (target) window.deckShell.closeDevTools(target) }
  }, [inspect])
  const go = () => {
    const u = toUrl(url)
    setUrl(u)
    if (ELECTRON && view.current) view.current.loadURL(u)
    else { setShown(u); setN(n + 1) }
    save(u)
  }
  const nav = fn => { try { view.current && view.current[fn]() } catch {} }
  return (
    <div className="web-tile" onPointerDown={stop}>
      <div className="web-bar">
        {ELECTRON && <><button className="wbtn" onClick={() => nav('goBack')} title="Back" aria-label="Back">‹</button>
          <button className="wbtn" onClick={() => nav('goForward')} title="Forward" aria-label="Forward">›</button></>}
        <button className="wbtn" onClick={() => (ELECTRON ? nav(loading ? 'stop' : 'reload') : go())} title={loading ? 'Stop' : 'Reload'}>{loading ? '✕' : '↻'}</button>
        <input value={url} onChange={e => setUrl(e.target.value)} onKeyDown={e => e.key === 'Enter' && go()} onFocus={e => e.target.select()}
          spellCheck={false} aria-label="Address or search" />
        {loading && <span className="web-spin" aria-hidden="true" />}
        {ELECTRON && <button className={`wbtn${inspect ? ' on' : ''}`} onClick={() => setInspect(i => !i)} title="Web Inspector" aria-pressed={inspect}>⚙</button>}
      </div>
      <div className="web-split">
        <div style={{ position: 'relative', flex: 1 }}>
          {ELECTRON
            ? <webview ref={view} className="web-frame" src={shown} partition="persist:deck-browser" allowpopups="true" />
            : <iframe key={n} className="web-frame" src={shown} title={node.title} />}
        </div>
        {inspect && <div className="web-tools"><webview ref={tools} className="web-frame" src="about:blank" /></div>}
      </div>
    </div>
  )
}
