import { useContext, useEffect, useMemo, useState } from 'react'
import { api } from '../api.js'
import { highlight, langOf } from '../lib/hl.js'
import { DeckCtx } from '../nodes.jsx'

const q = o => new URLSearchParams(o).toString()
const size = b => (b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1048576).toFixed(1)} MB`)

// The repo's files: a tree on the left, the chosen file highlighted on the right, pinnable to the board.
export function CodeStudio({ root, ws }) {
  const { toast } = useContext(DeckCtx)
  const [dirs, setDirs] = useState({})                 // rel path -> entries
  const [open, setOpen] = useState({ '': true })
  const [file, setFile] = useState(null)
  const [filter, setFilter] = useState('')
  const load = rel => api(`/api/fs/tree?${q({ root, path: rel })}`).then(es => setDirs(d => ({ ...d, [rel]: es }))).catch(e => toast(e.message))
  useEffect(() => { setDirs({}); setOpen({ '': true }); setFile(null); load('') }, [root])          // eslint-disable-line
  const toggle = e => {
    if (!e.dir) return api(`/api/fs/file?${q({ root, path: e.path })}`).then(f => setFile({ ...f, path: e.path })).catch(err => toast(err.message))
    setOpen(o => ({ ...o, [e.path]: !o[e.path] }))
    if (!dirs[e.path]) load(e.path)
  }
  const pin = async () => {
    try {
      await api('/api/nodes', { method: 'POST', body: { type: 'file', ws, title: file.path.split('/').pop(), path: file.path, data: { root }, focus: true } })
      toast('Pinned to the board')
    } catch (e) { toast(e.message) }
  }
  const rows = []
  const walk = (rel, depth) => (dirs[rel] || []).forEach(e => {
    if (filter && !e.dir && !e.name.toLowerCase().includes(filter.toLowerCase())) return
    rows.push({ ...e, depth })
    if (e.dir && open[e.path]) walk(e.path, depth + 1)
  })
  walk('', 0)
  return (
    <div className="studio-split">
      <aside className="tree scrolls">
        <input className="tree-filter" value={filter} onChange={e => setFilter(e.target.value)} placeholder="Filter open folders" spellCheck={false} />
        {rows.map(e => (
          <button key={e.path} className={`tree-row${file?.path === e.path ? ' on' : ''}`} style={{ paddingLeft: 10 + e.depth * 14 }} onClick={() => toggle(e)} title={e.path}>
            <span className="tw">{e.dir ? (open[e.path] ? '▾' : '▸') : ''}</span><span className={e.dir ? 'dir' : ''}>{e.name}</span>
          </button>))}
      </aside>
      <section className="viewer">
        {!file ? <div className="studio-empty">Pick a file to read it. Pin one to keep it on the board next to the console working on it.</div> : <>
          <div className="viewer-h"><code>{file.path}</code><span className="dim small">{size(file.size)}{file.truncated ? ' · first 1 MB' : ''}</span>
            <span className="grow" /><button className="btn" onClick={() => navigator.clipboard?.writeText(file.path).catch(() => {})}>Copy path</button>
            <button className="btn primary" onClick={pin}>Pin to board</button></div>
          {file.binary ? <div className="studio-empty">A binary file - nothing to show.</div> : <CodeView text={file.text} name={file.path} />}
        </>}
      </section>
    </div>
  )
}

export function CodeView({ text, name }) {
  const html = useMemo(() => highlight(text, langOf(name)), [text, name])
  const lines = useMemo(() => text.split('\n').length, [text])
  return (
    <div className="code-view scrolls">
      <pre className="ln" aria-hidden="true">{Array.from({ length: lines }, (_, i) => i + 1).join('\n')}</pre>
      <pre className="src"><code dangerouslySetInnerHTML={{ __html: html }} /></pre>
    </div>
  )
}

// A file pinned to the board: the same view, kept current while it's there.
export function FileNode({ node }) {
  const [f, setF] = useState(null)
  useEffect(() => {
    let t, alive = true, last = 0
    const get = async () => {
      try {
        const r = await api(`/api/fs/file?${q({ root: node.data?.root || '', path: node.path })}`)
        if (alive && r.mtime !== last) { last = r.mtime; setF(r) }
      } catch (e) { if (alive) setF({ error: e.message }) }
      if (alive) t = setTimeout(get, 4000)
    }
    get()
    return () => { alive = false; clearTimeout(t) }
  }, [node.path, node.data?.root])
  if (!f) return <div className="file-node dim">Loading…</div>
  if (f.error) return <div className="file-node dim" style={{ padding: 14 }}>{f.error}</div>
  return <div className="file-node" onPointerDown={e => e.stopPropagation()}>{f.binary ? <div className="studio-empty">Binary file.</div> : <CodeView text={f.text} name={node.path} />}</div>
}
