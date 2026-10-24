import { useContext, useEffect, useMemo, useRef, useState } from 'react'
import { api, ago, saveFile } from '../api.js'
import { layout, toExcalidraw, toPNG, toSVG } from '../lib/diagram.js'
import { markdown } from '../lib/md.js'
import { DeckCtx } from '../nodes.jsx'

const AGENTS = [['claude', 'Claude Code'], ['codex', 'Codex']]
const slug = s => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 50) || 'document'

// One headless agent run: start it, then follow it until it's done or failed.
function useJob() {
  const [job, setJob] = useState(null)
  const timer = useRef(0)
  useEffect(() => () => clearTimeout(timer.current), [])
  const follow = j => {
    setJob(j)
    if (j.status !== 'running') return
    timer.current = setTimeout(() => api(`/api/studio/jobs/${j.id}`).then(follow).catch(e => setJob({ ...j, status: 'failed', error: e.message })), 2000)
  }
  const start = body => api('/api/studio/run', { method: 'POST', body }).then(follow)
  return [job, start, setJob]
}

function Running({ job }) {
  const [, tick] = useState(0)
  useEffect(() => { const t = setInterval(() => tick(x => x + 1), 1000); return () => clearInterval(t) }, [])
  return <div className="studio-empty"><span className="web-spin big" />
    <b>{job.agent === 'codex' ? 'Codex' : 'Claude'} is reading the repo…</b><span className="dim">{ago(job.started)} so far · this uses your {job.agent === 'codex' ? 'ChatGPT' : 'Claude'} limits</span></div>
}

function Picker({ kinds, kind, setKind, custom, setCustom, agent, setAgent, busy, onGo, what }) {
  return (
    <aside className="tree make scrolls">
      <b className="lbl">{what}</b>
      {kinds.map(([k, label, sub]) => (
        <button key={k} className={`tree-row tall${kind === k ? ' on' : ''}`} onClick={() => setKind(k)}><span><b>{label}</b><small>{sub}</small></span></button>))}
      {kind === 'custom' && <textarea className="tree-filter" rows={4} value={custom} onChange={e => setCustom(e.target.value)} placeholder="Describe it…" />}
      <b className="lbl">Agent</b>
      <div className="seg mini-seg">{AGENTS.map(([k, l]) => <button key={k} className={agent === k ? 'on' : ''} onClick={() => setAgent(k)}>{l}</button>)}</div>
      <button className="btn primary wide-btn" disabled={busy || (kind === 'custom' && !custom.trim())} onClick={onGo}>{busy ? 'Working…' : 'Generate'}</button>
      <small className="dim">Read-only: the agent can look at the repo but not change it.</small>
    </aside>
  )
}

const DOCS = [['readme', 'README', 'what it is, setup, layout'], ['architecture', 'Architecture', 'components and how they fit'],
  ['api', 'API reference', 'endpoints, commands, functions'], ['onboarding', 'Onboarding guide', 'for a new developer'], ['custom', 'Custom…', 'describe your own']]

const PRINT_CSS = `@page{size:A4;margin:20mm 18mm}body{font:10.5pt/1.6 "Segoe UI",system-ui,sans-serif;color:#1d1b26;margin:0}
h1{font-size:22pt;margin:0 0 12pt;letter-spacing:-.3pt}h2{font-size:15pt;margin:18pt 0 6pt;border-bottom:1px solid #e4e1ef;padding-bottom:3pt}
h3{font-size:12pt;margin:14pt 0 4pt}h1,h2,h3{break-after:avoid}p,li{orphans:3;widows:3}
pre{background:#f6f5fb;border:1px solid #e9e6f3;padding:8pt 10pt;border-radius:6px;white-space:pre-wrap;font:8.5pt/1.5 Consolas,monospace;break-inside:avoid}
code{font:9pt Consolas,monospace;background:#f1eff8;padding:0 3px;border-radius:3px}pre code{background:none;padding:0}
table{border-collapse:collapse;width:100%;break-inside:avoid;font-size:9.5pt}th,td{border:1px solid #dcd8e8;padding:4pt 7pt;text-align:left}th{background:#f3f1fa}
blockquote{border-left:3px solid #a48bff;margin:8pt 0;padding:2pt 12pt;color:#55536b}a{color:#5b3fd1}
.h-k{color:#7a3fd1;font-weight:600}.h-s{color:#1f7a4d}.h-c{color:#8d8ba6;font-style:italic}.h-n{color:#b4561a}.h-f{color:#2a5db0}`

export function DocStudio({ root }) {
  const { toast } = useContext(DeckCtx)
  const [kind, setKind] = useState('readme')
  const [custom, setCustom] = useState('')
  const [agent, setAgent] = useState('claude')
  const [job, start, setJob] = useJob()
  const [text, setText] = useState('')
  const [editing, setEditing] = useState(false)
  useEffect(() => { if (job?.status === 'done') setText(job.out) }, [job?.status])          // eslint-disable-line
  const title = (text.match(/^#\s+(.+)$/m) || [])[1] || DOCS.find(d => d[0] === kind)[1]
  const html = useMemo(() => markdown(text), [text])
  const go = () => { setText(''); start({ kind: 'doc', agent, root, type: kind, custom }).catch(e => toast(e.message)) }
  const save = async () => {
    const path = `docs/${slug(title)}.md`
    try { await api('/api/fs/file', { method: 'PUT', body: { root, path, text } }); toast(`Saved ${path}`) } catch (e) { toast(e.message) }
  }
  const pdf = async () => {
    const page = `<!doctype html><html><head><meta charset="utf-8"><title>${title.replace(/</g, '&lt;')}</title><style>${PRINT_CSS}</style></head><body>${html}</body></html>`
    try { const where = await window.deckShell.printPdf({ html: page, name: `${slug(title)}.pdf` }); if (where) toast(`PDF saved: ${where}`) } catch (e) { toast(e.message) }
  }
  return (
    <div className="studio-split">
      <Picker what="Document" kinds={DOCS} kind={kind} setKind={setKind} custom={custom} setCustom={setCustom} agent={agent} setAgent={setAgent}
        busy={job?.status === 'running'} onGo={go} />
      <section className="viewer">
        {job?.status === 'running' ? <Running job={job} /> : job?.status === 'failed' ? <div className="studio-empty err"><b>That didn't work.</b><span>{job.error}</span><button className="btn" onClick={() => setJob(null)}>OK</button></div>
          : !text ? <div className="studio-empty">Pick a document and an agent. It reads the repo, writes the document here, and you can edit it, save it to docs/ or export a paginated PDF.</div> : <>
            <div className="viewer-h"><b>{title}</b><span className="grow" />
              <button className="btn" onClick={() => setEditing(e => !e)}>{editing ? 'Preview' : 'Edit'}</button>
              <button className="btn" onClick={save}>Save to docs/</button>
              <button className="btn" onClick={() => saveFile(`${slug(title)}.md`, text, { filters: [{ name: 'Markdown', extensions: ['md'] }] })}>.md</button>
              <button className="btn primary" disabled={!window.deckShell?.printPdf} title={window.deckShell?.printPdf ? '' : 'PDF export needs the Deck window'} onClick={pdf}>Export PDF</button></div>
            {editing ? <textarea className="md-edit scrolls" value={text} onChange={e => setText(e.target.value)} spellCheck={false} />
              : <div className="md-view scrolls doc"><div className="md" dangerouslySetInnerHTML={{ __html: html }} /></div>}
          </>}
      </section>
    </div>
  )
}

const DIAGRAMS = [['architecture', 'Architecture', 'components and dependencies'], ['modules', 'Modules', 'which code uses which'],
  ['dataflow', 'Data flow', 'inputs → storage → outputs'], ['flow', 'Main flow', 'the user path, step by step'], ['custom', 'Custom…', 'describe your own']]

export function DiagramStudio({ root, ws }) {
  const { toast } = useContext(DeckCtx)
  const [kind, setKind] = useState('architecture')
  const [custom, setCustom] = useState('')
  const [agent, setAgent] = useState('claude')
  const [job, start, setJob] = useJob()
  const d = useMemo(() => { try { return job?.status === 'done' ? JSON.parse(job.out) : null } catch { return null } }, [job])
  const L = useMemo(() => (d ? layout(d) : null), [d])
  const go = () => start({ kind: 'diagram', agent, root, type: kind, custom }).catch(e => toast(e.message))
  const place = async () => {
    try {
      await api('/api/nodes', { method: 'POST', body: { type: 'diagram', ws, title: d.title, data: d, w: Math.min(1400, L.width + 2), h: Math.min(1000, L.height + 40), focus: true } })
      toast('Diagram on the board')
    } catch (e) { toast(e.message) }
  }
  return (
    <div className="studio-split">
      <Picker what="Diagram" kinds={DIAGRAMS} kind={kind} setKind={setKind} custom={custom} setCustom={setCustom} agent={agent} setAgent={setAgent}
        busy={job?.status === 'running'} onGo={go} />
      <section className="viewer">
        {job?.status === 'running' ? <Running job={job} /> : job?.status === 'failed' ? <div className="studio-empty err"><b>That didn't work.</b><span>{job.error}</span><button className="btn" onClick={() => setJob(null)}>OK</button></div>
          : !L ? <div className="studio-empty">Pick a diagram and an agent. It maps the repo; the diagram lands on the board as ink, or exports as PNG, SVG or .excalidraw.</div> : <>
            <div className="viewer-h"><b>{d.title}</b><span className="dim small">{d.nodes.length} boxes · {d.edges.length} arrows</span><span className="grow" />
              <DiagramExports d={d} L={L} /><button className="btn primary" onClick={place}>Put on board</button></div>
            <div className="diagram-view scrolls" dangerouslySetInnerHTML={{ __html: toSVG(L, true) }} />
          </>}
      </section>
    </div>
  )
}

function DiagramExports({ d, L }) {
  const { toast } = useContext(DeckCtx)
  const name = slug(d.title)
  const run = async fn => { try { const w = await fn(); if (w) toast(`Saved ${w}`) } catch (e) { toast(e.message) } }
  return <>
    <button className="btn" onClick={() => run(async () => saveFile(`${name}.png`, await toPNG(L, false), { base64: true, mime: 'image/png', filters: [{ name: 'PNG', extensions: ['png'] }] }))}>PNG</button>
    <button className="btn" onClick={() => run(() => saveFile(`${name}.svg`, toSVG(L, false), { mime: 'image/svg+xml', filters: [{ name: 'SVG', extensions: ['svg'] }] }))}>SVG</button>
    <button className="btn" onClick={() => run(() => saveFile(`${name}.excalidraw`, toExcalidraw(L), { mime: 'application/json', filters: [{ name: 'Excalidraw', extensions: ['excalidraw'] }] }))}>.excalidraw</button>
  </>
}

// A diagram on the board: drawn in ink, exportable from its corner.
export function DiagramNode({ node }) {
  const L = useMemo(() => (node.data?.nodes ? layout(node.data) : null), [node.data])
  if (!L) return <div className="file-node dim" style={{ padding: 14 }}>No diagram data.</div>
  return (
    <div className="diagram-node" onPointerDown={e => e.stopPropagation()}>
      <div className="diagram-tools"><DiagramExports d={node.data} L={L} /></div>
      <div className="diagram-view scrolls" dangerouslySetInnerHTML={{ __html: toSVG(L, true) }} />
    </div>
  )
}
