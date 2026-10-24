// Athena Deck's window (Electron). The board itself is the React page that
// deck/server.py serves; this shell exists so browser tiles can be real
// browsers (<webview>) - Google, YouTube, GitHub and logins refuse iframes.
// Started by deck/__main__.py; closing the window tells the server to quit.
const { app, BrowserWindow, dialog, ipcMain, webContents } = require('electron')
const fs = require('fs')
const http = require('http')
const path = require('path')

const DATA = path.join(__dirname, '..', 'data')
const PORT = (() => { try { return fs.readFileSync(path.join(DATA, 'port.txt'), 'utf8').trim() } catch { return '8430' } })()
const TOKEN = fs.readFileSync(path.join(DATA, 'token.txt'), 'utf8').trim()
const BOARD = `http://127.0.0.1:${PORT}`

// For checking the real window from a script (screenshots, clicks): only when asked for, and only
// on 127.0.0.1 - DECK_DEBUG_PORT is never set in normal use.
if (/^\d{4,5}$/.test(process.env.DECK_DEBUG_PORT || '')) {
  app.commandLine.appendSwitch('remote-debugging-port', process.env.DECK_DEBUG_PORT)
  app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1')
}

// Sites see an ordinary Chrome, not "Electron" - Google refuses sign-in to browsers it doesn't know.
app.userAgentFallback = app.userAgentFallback.replace(/\s(?:Electron|athena-deck-shell)\/\S+/g, '')
app.setPath('userData', path.join(DATA, 'shell'))

if (!app.requestSingleInstanceLock()) app.quit()
let win = null

function post(route) {
  return new Promise(done => {
    const req = http.request(`${BOARD}${route}`, { method: 'POST', headers: { 'X-Token': TOKEN, 'Content-Type': 'application/json' }, timeout: 4000 },
      res => { res.resume(); res.on('end', done) })
    req.on('error', done); req.on('timeout', () => { req.destroy(); done() })
    req.end('{}')
  })
}

// Where the window was last time. Saved on close, not on every drag: losing
// the position of a session that crashed matters less than writing constantly.
const STATE = path.join(DATA, 'window.json')

function savedState() {
  try {
    const s = JSON.parse(fs.readFileSync(STATE, 'utf8'))
    if (!(s.width > 0 && s.height > 0)) return null
    // A window saved on a monitor that is now unplugged would open off-screen
    // where it cannot be dragged back, so only restore if it still overlaps one.
    const area = require('electron').screen.getDisplayMatching(s).workArea
    const onScreen = s.x < area.x + area.width && s.x + s.width > area.x &&
                     s.y < area.y + area.height && s.y + s.height > area.y
    return onScreen ? s : null
  } catch { return null }
}

function create() {
  const saved = savedState()
  win = new BrowserWindow({
    ...(saved ? { x: saved.x, y: saved.y, width: saved.width, height: saved.height }
              : { width: 1600, height: 980 }),
    minWidth: 900, minHeight: 600, title: 'Athena Deck', backgroundColor: '#07070b',
    autoHideMenuBar: true, show: false,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, webviewTag: true },
  })
  win.on('page-title-updated', e => e.preventDefault())            // the server finds the window by this title
  // First run maximised: the board's Fit scales content to the window, so a
  // 1600x980 window on a 1080p screen fits the same tiles at ~61% and labels
  // go mushy. After that, whatever the window was left as.
  win.once('ready-to-show', () => { if (!saved || saved.maximized) win.maximize(); win.show() })
  // getNormalBounds, not getBounds: otherwise closing maximised saves the
  // full-screen size as the restore size and un-maximising does nothing.
  win.on('close', () => {
    try { fs.writeFileSync(STATE, JSON.stringify({ ...win.getNormalBounds(), maximized: win.isMaximized() })) } catch {}
  })
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))   // the board never opens windows of its own
  win.webContents.on('will-navigate', (e, url) => { if (!url.startsWith(BOARD)) e.preventDefault() })
  win.loadURL(`${BOARD}/?t=${encodeURIComponent(TOKEN)}`)
  win.on('closed', () => { win = null })
}

// Browser tiles: no Node, no preload, one saved profile (your sign-ins stay), pop-ups open in the same tile.
app.on('web-contents-created', (_e, contents) => {
  contents.on('will-attach-webview', (_ev, prefs, params) => {
    delete prefs.preload
    prefs.nodeIntegration = false
    prefs.contextIsolation = true
    params.partition = 'persist:deck-browser'
  })
  if (contents.getType() === 'webview') {
    contents.setWindowOpenHandler(({ url }) => { contents.loadURL(url); return { action: 'deny' } })
  }
})

ipcMain.handle('pick-folder', async () => {
  const r = await dialog.showOpenDialog(win, { properties: ['openDirectory'] })
  return r.canceled ? '' : r.filePaths[0]
})

// The Web Inspector, docked: a browser tile's DevTools drawn inside a second <webview> under it.
const isTile = c => c && !c.isDestroyed() && c.getType() === 'webview'
ipcMain.handle('dock-devtools', (_e, target, host) => {
  const t = webContents.fromId(target), h = webContents.fromId(host)
  if (!isTile(t) || !isTile(h)) return false
  t.setDevToolsWebContents(h)
  t.openDevTools({ mode: 'detach', activate: false })
  return true
})
ipcMain.handle('close-devtools', (_e, target) => {
  const t = webContents.fromId(target)
  if (isTile(t)) t.closeDevTools()
})

// Saving what the studios make: a Save dialog, then the bytes (base64) or text.
ipcMain.handle('save-file', async (_e, { name, data, base64, filters }) => {
  const r = await dialog.showSaveDialog(win, { defaultPath: name, filters: filters || [] })
  if (r.canceled || !r.filePath) return ''
  fs.writeFileSync(r.filePath, base64 ? Buffer.from(data, 'base64') : String(data))
  return r.filePath
})

// A paginated PDF of a document: printed by a hidden window, page numbers in the footer.
ipcMain.handle('print-pdf', async (_e, { html, name }) => {
  const r = await dialog.showSaveDialog(win, { defaultPath: name, filters: [{ name: 'PDF', extensions: ['pdf'] }] })
  if (r.canceled || !r.filePath) return ''
  const pdfWin = new BrowserWindow({ show: false, webPreferences: { javascript: false, contextIsolation: true } })
  try {
    await pdfWin.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html))
    const pdf = await pdfWin.webContents.printToPDF({
      pageSize: 'A4', printBackground: true, displayHeaderFooter: true, margins: { marginType: 'default' },
      headerTemplate: '<span></span>',
      footerTemplate: '<div style="width:100%;font:9px Segoe UI,sans-serif;color:#888;text-align:center"><span class="pageNumber"></span> / <span class="totalPages"></span></div>',
    })
    fs.writeFileSync(r.filePath, pdf)
    return r.filePath
  } finally { pdfWin.destroy() }
})

app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.focus() } })
app.whenReady().then(create)
app.on('window-all-closed', async () => {                           // window closed: the consoles stop, the board is saved
  await post('/api/quit')
  app.quit()
})
