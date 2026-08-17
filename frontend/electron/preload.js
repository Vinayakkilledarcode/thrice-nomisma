// preload.js
const { ipcRenderer } = require('electron')

// Declare the API interface
const electronAPI = {
  isElectron: true,
  platform: process.platform,
  
  // Register receiver listener to capture browser loopback callbacks
  onAuthSuccess: (callback) => {
    const subscription = (event, data) => callback(data)
    ipcRenderer.on('auth-success', subscription)
    return () => ipcRenderer.removeListener('auth-success', subscription)
  },

  // Secure order routing bridge
  sendOrder: (payload) => {
    return ipcRenderer.invoke('place-order', payload)
  },

  // Recolors the native window controls (minimize/maximize/close) to match
  // the in-app theme. Called from App.tsx's theme-sync effect whenever the
  // user toggles light/dark mode.
  setTitleBarOverlay: (opts) => {
    ipcRenderer.send('set-titlebar-overlay', opts)
  }
}

// Resilient injection mapping
try {
  const { contextBridge } = require('electron')
  // Expose securely if contextIsolation is on
  contextBridge.exposeInMainWorld('electron', electronAPI)
} catch (e) {
  // Gracefully fall back to direct window binding if contextIsolation is off
  window.electron = electronAPI
}