import { useEffect, useRef } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { wsURL } from './api.js'

const THEME = {
  background: '#0b0a12', foreground: '#e6e4f0', cursor: '#a48bff', cursorAccent: '#0b0a12',
  selectionBackground: 'rgba(164,139,255,.35)',
  black: '#1b1a24', red: '#ff6b7a', green: '#3ddc97', yellow: '#f7b84b', blue: '#6aa7ff', magenta: '#c792ea', cyan: '#59d3ff', white: '#d7d5e3',
  brightBlack: '#55536b', brightRed: '#ff8a96', brightGreen: '#6ee7b0', brightYellow: '#ffd27a', brightBlue: '#93c0ff',
  brightMagenta: '#dcb0ff', brightCyan: '#8fe4ff', brightWhite: '#ffffff',
}

// Every mounted terminal, so the server can ask what a console really shows (deck_read).
export const TERMS = new Map()
export function snapshot(id, rows = 80) {
  const t = TERMS.get(id)
  if (!t) return ''
  const b = t.buffer.active, out = []
  for (let i = Math.max(0, b.length - rows); i < b.length; i++) out.push(b.getLine(i)?.translateToString(true) || '')
  return out.join('\n').replace(/\s+$/, '')
}

// One live console: xterm.js on a websocket to its ConPTY (deck/server.py /ws/term/<id>).
// Ctrl+C goes to the program, like any terminal; copy is Ctrl+Shift+C, paste Ctrl+V.
export default function Terminal({ id, focusRef }) {
  const box = useRef(null)
  useEffect(() => {
    const term = new XTerm({
      fontFamily: '"Cascadia Code","Cascadia Mono","JetBrains Mono",Consolas,monospace', fontSize: 13, lineHeight: 1.15,
      cursorBlink: true, scrollback: 8000, theme: THEME, allowProposedApi: true,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(box.current)
    TERMS.set(id, term)
    if (focusRef) focusRef.current = () => term.focus()
    let ws, alive = true, retry, replaying = false
    const send = m => ws && ws.readyState === 1 && ws.send(JSON.stringify(m))
    const resize = () => {
      if (!box.current || !box.current.offsetWidth) return
      try { fit.fit(); send({ t: 'r', c: term.cols, r: term.rows }) } catch {}
    }
    const connect = () => {
      ws = new WebSocket(wsURL(`/ws/term/${id}`))
      let first = true
      ws.onopen = () => { term.reset(); resize() }
      ws.onmessage = e => {
        const m = JSON.parse(e.data)
        if (m.t !== 'o') return
        // The first frame replays the console's history. xterm answers the terminal queries in it
        // ("who are you?", "where's the cursor?") - answers to a program that asked long ago, which
        // arrived as typed text ("[?1;2c" at a PowerShell prompt, 20 Oct 2026). Drop them.
        if (first) { first = false; replaying = true; term.write(m.d, () => { replaying = false }) }
        else term.write(m.d)
      }
      ws.onclose = () => { if (alive) retry = setTimeout(connect, 1500) }
    }
    connect()
    const dataSub = term.onData(d => { if (!replaying) send({ t: 'i', d }) })
    term.attachCustomKeyEventHandler(e => {
      if (e.type !== 'keydown') return true
      if (e.ctrlKey && e.shiftKey && e.code === 'KeyC') { navigator.clipboard?.writeText(term.getSelection()).catch(() => {}); return false }
      // Not ^V to the program: the browser's own paste event reaches xterm. Pasting here as well
      // doubled every paste - a pasted Gemini API key went in twice and was refused (20 Oct 2026).
      if (e.ctrlKey && !e.shiftKey && e.code === 'KeyV') return false
      return true
    })
    const ro = new ResizeObserver(() => requestAnimationFrame(resize))
    ro.observe(box.current)
    return () => { alive = false; TERMS.delete(id); clearTimeout(retry); ro.disconnect(); dataSub.dispose(); ws && ws.close(); term.dispose() }
  }, [id])
  // the board pans on drags and wheels outside terminals only
  return <div className="term" ref={box} onPointerDown={e => e.stopPropagation()} onWheel={e => { if (!e.ctrlKey) e.stopPropagation() }} />
}
