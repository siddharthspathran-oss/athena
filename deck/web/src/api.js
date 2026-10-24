// Talking to deck/server.py. The token arrives once in the link (?t=), then lives in storage.
const q = new URLSearchParams(location.search)
if (q.get('t')) {
  try { localStorage.setItem('deck_token', q.get('t')) } catch {}
  history.replaceState({}, '', '/')
}
export const TOKEN = (() => { try { return localStorage.getItem('deck_token') || '' } catch { return '' } })()

export async function api(path, { method = 'GET', body } = {}) {
  const r = await fetch(path, {
    method,
    headers: { 'X-Token': TOKEN, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(j.error || r.statusText)
  return j
}

export const wsURL = path => `ws://${location.host}${path}${path.includes('?') ? '&' : '?'}t=${encodeURIComponent(TOKEN)}`

// Running in the Electron shell (deck/shell): browser tiles can be real browsers there.
export const ELECTRON = !!window.deckShell?.electron

// The window's native folder picker - Electron's or pywebview's; empty in a plain browser.
export async function pickFolder() {
  try { return (await (window.deckShell?.pickFolder?.() ?? window.pywebview?.api?.pick_folder?.())) || '' } catch { return '' }
}

// What you type in a browser tile's bar: an address ("google.com", "localhost:5173") or a search.
export function toUrl(v) {
  v = String(v || '').trim()
  if (/^https?:\/\//i.test(v)) return v
  if (/^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(\/.*)?$/i.test(v)) return 'http://' + v
  if (/^[\w-]+(\.[\w-]+)+(:\d+)?(\/\S*)?$/.test(v)) return 'https://' + v
  return 'https://www.google.com/search?q=' + encodeURIComponent(v)
}

export const KINDS = {
  claude: { label: 'Claude Code', short: 'CLAUDE' },
  gemini: { label: 'Antigravity', short: 'AGY' },
  codex: { label: 'Codex', short: 'CODEX' },
  shell: { label: 'PowerShell', short: 'SHELL' },
  custom: { label: 'Custom', short: 'CMD' },
  note: { label: 'Note', short: 'NOTE' },
  web: { label: 'Web', short: 'WEB' },
  text: { label: 'Text', short: 'TEXT' },
  checklist: { label: 'Checklist', short: 'TODO' },
  kanban: { label: 'Kanban', short: 'KANBAN' },
  image: { label: 'Image', short: 'IMG' },
  git: { label: 'Git', short: 'GIT' },
  file: { label: 'File', short: 'FILE' },
  diagram: { label: 'Diagram', short: 'DIAGRAM' },
  agent: { label: 'Agent step', short: 'AGENT' },
}

// An image file or blob onto the server; returns its /media/ address.
export async function uploadImage(blob) {
  const r = await fetch('/api/media', { method: 'POST', headers: { 'X-Token': TOKEN, 'Content-Type': blob.type }, body: blob })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(j.error || r.statusText)
  return j.src
}

// Save something the board made: the Electron shell's Save dialog, else an ordinary download.
export async function saveFile(name, data, { base64 = false, mime = 'application/octet-stream', filters } = {}) {
  if (window.deckShell?.saveFile) return window.deckShell.saveFile({ name, data, base64, filters })
  const bytes = base64 ? Uint8Array.from(atob(data), c => c.charCodeAt(0)) : data
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(new Blob([bytes], { type: mime })), download: name })
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000)
  return name
}

export const uid = () => Math.random().toString(36).slice(2, 10)

export const STATE_LABEL = { working: 'working', resting: 'resting', needs: 'needs you', starting: 'starting', exited: 'exited' }

export function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts)
  return s < 60 ? `${Math.round(s)}s` : s < 3600 ? `${Math.round(s / 60)}m` : s < 86400 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`
}

export const money = v => '$' + (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2))
