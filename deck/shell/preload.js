// What the board page may ask the Electron shell for - nothing else is exposed.
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('deckShell', {
  electron: true,
  pickFolder: () => ipcRenderer.invoke('pick-folder'),
  dockDevTools: (target, host) => ipcRenderer.invoke('dock-devtools', target, host),
  closeDevTools: target => ipcRenderer.invoke('close-devtools', target),
  saveFile: opts => ipcRenderer.invoke('save-file', opts),
  printPdf: opts => ipcRenderer.invoke('print-pdf', opts),
})
