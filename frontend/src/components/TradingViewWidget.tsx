// components/TradingViewWidget.tsx
import React, { useEffect, useRef } from 'react'

declare global {
  namespace JSX {
    interface IntrinsicElements {
      webview: React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement> & {
        src?: string
        allowpopups?: string
        partition?: string
        webpreferences?: string
        style?: React.CSSProperties
      }, HTMLElement>
    }
  }
}

interface Props {
  tvSymbol: string
  onSymbolChange?: (symbol: string) => void // Handler to communicate symbol back to App.tsx
}

const isElectron = (): boolean =>
  typeof window !== 'undefined' &&
  !!(window as unknown as { electron?: { isElectron: boolean } }).electron?.isElectron

// Script executed inside TradingView's DOM context to retrieve the active ticker
const scrapeTradingViewSymbol = `
  (() => {
    // 1. Try the header toolbar symbol search button first
    let el = document.getElementById('header-toolbar-symbol-search') || document.querySelector('[id*="symbol-search"]');
    if (el) {
      let txt = el.innerText || el.textContent;
      if (txt) {
        let clean = txt.trim();
        if (clean && clean.length < 15) return clean;
      }
    }
    
    // 2. Fallback to main chart legend element
    el = document.querySelector('.legend-item-symbol') || document.querySelector('[class*="legend-item-symbol"]');
    if (el) {
      let txt = el.innerText || el.textContent;
      if (txt) {
        let clean = txt.trim();
        if (clean && clean.length < 15) return clean;
      }
    }
    
    // 3. Fallback to any active ticker text elements
    el = document.querySelector('[class*="symbol-text"]') || document.querySelector('[class*="ticker"]');
    if (el) {
      let txt = el.innerText || el.textContent;
      if (txt) {
        let clean = txt.trim();
        if (clean && clean.length < 15) return clean;
      }
    }
    
    return null;
  })()
`;

export default function TradingViewWidget({ tvSymbol, onSymbolChange }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const webviewRef = useRef<any>(null)
  const currentSymbolRef = useRef<string>(tvSymbol)

  // 1. Initial Webview creation (Executes strictly ONCE on Mount to prevent panel resetting)
  useEffect(() => {
    if (!isElectron() || !containerRef.current) return

    containerRef.current.innerHTML = ''

    const webview = document.createElement('webview') as any
    const initialUrl = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(currentSymbolRef.current)}`
    
    webview.setAttribute('src', initialUrl)
    webview.setAttribute('partition', 'persist:tv')
    webview.setAttribute('allowpopups', 'true')
    webview.setAttribute('webpreferences', 'contextIsolation=false')
    
    webview.style.width = '100%'
    webview.style.height = '100%'
    webview.style.border = 'none'

    containerRef.current.appendChild(webview)
    webviewRef.current = webview

    // 2. Poll the DOM inside the webview to capture real-time symbol updates
    let pollIntervalId: any = null;

    const startPolling = () => {
      if (pollIntervalId) clearInterval(pollIntervalId);
      
      pollIntervalId = setInterval(() => {
        if (!webview) return;
        
        webview.executeJavaScript(scrapeTradingViewSymbol)
          .then((scrapedSymbol: string | null) => {
            if (scrapedSymbol) {
              const cleanSymbol = scrapedSymbol.trim().toUpperCase();
              
              if (cleanSymbol && cleanSymbol !== currentSymbolRef.current) {
                currentSymbolRef.current = cleanSymbol;
                if (onSymbolChange) {
                  onSymbolChange(cleanSymbol); // Update parent state in App.tsx
                }
              }
            }
          })
          .catch(() => {
            // Silently swallow errors during initial page load/transitions
          });
      }, 1500); // Polling frequency of 1.5 seconds
    };

    // Begin polling once the page structure is loaded
    webview.addEventListener('dom-ready', startPolling)

    return () => {
      if (pollIntervalId) clearInterval(pollIntervalId);
      if (containerRef.current) {
        containerRef.current.innerHTML = ''
      }
    }
  }, [])

  // 3. Process programmatic updates to the Symbol state (e.g. on boot)
  useEffect(() => {
    if (webviewRef.current && tvSymbol !== currentSymbolRef.current) {
      currentSymbolRef.current = tvSymbol
      const newUrl = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tvSymbol)}`
      webviewRef.current.loadURL(newUrl)
    }
  }, [tvSymbol])

  if (isElectron()) {
    return (
      <div 
        ref={containerRef} 
        style={{ width: '100%', height: '100%', background: '#0c0e12' }} 
      />
    )
  }

  return (
    <div className="tv-fallback">
      <div className="tv-fallback-inner">
        <p className="tv-fallback-title">Electron Required</p>
        <p className="tv-fallback-sub">
          Run <code>npm run electron:dev</code> to load TradingView
        </p>
      </div>
    </div>
  )
}