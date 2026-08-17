import React, { useState, useEffect } from 'react'
import BottomNav, { PrimaryTab } from './components/BottomNav'
import Sidebar from './components/Sidebar'
import TabbedPanel from './components/TabbedPanel'
import ProfilePanel from './components/ProfilePanel'
import { api } from './utils/api'
import { UserProfile, StrategySignal, LiveTick } from './utils/types'

import logoImg from './assets/logo.png'

const DEFAULT_SYMBOL = 'NASDAQ:AAPL'

declare global {
  interface Window {
    electron?: {
      isElectron: boolean;
      platform: string;
      onAuthSuccess: (callback: (data: any) => void) => () => void;
      // Optional — only present if main.js/preload.js expose it (see note in
      // the theme-sync effect below). Recolors the native window controls
      // (minimize/maximize/close) drawn by Chromium's titleBarOverlay, which
      // CSS cannot reach because those buttons live outside the DOM.
      setTitleBarOverlay?: (opts: { color: string; symbolColor: string }) => void;
    }
  }
}

export default function App() {
  // Initialized as blank string to keep the panel in standby on boot [1]
  const [tvSymbol, setTvSymbol] = useState("")

  // --- Secure Session Guards ---
  const [user, setUser] = useState<UserProfile | null>(null)
  const [isVerifying, setIsVerifying] = useState(true)
  const [authError, setAuthError] = useState('')

  // --- Dynamic Day & Light Theme State ---
  const [theme, setTheme] = useState(() => localStorage.getItem('thrice_theme') || 'dark')

  // --- Shared Ticker Timeframe (single source of truth) ---
  // Lifted up from ProfilePanel so TabbedPanel's Strategy Workspace can be
  // LOCKED to whatever timeframe the ticker/Live Telemetry panel is showing —
  // the two no longer drift independently of each other.
  const [tickerInterval, setTickerInterval] = useState<string>('1d')

  // --- Primary workspace tab (now driven by the bottom nav bar) ---
  const [primaryTab, setPrimaryTab] = useState<PrimaryTab>('strategy')

  // TabbedPanel's own Tab type doesn't include 'profile' — Profile is an
  // overlay, not a workspace tab. This remembers the last real workspace
  // tab so TabbedPanel keeps rendering it (and its TradingView/AngelOne
  // webviews stay mounted, never remounting) while Profile is shown on top.
  const [workspaceTab, setWorkspaceTab] = useState<Exclude<PrimaryTab, 'profile'>>('strategy')

  const handleTabChange = (tab: PrimaryTab) => {
    setPrimaryTab(tab)
    if (tab !== 'profile') setWorkspaceTab(tab)
  }

  // --- Strategy Signal and Live Tick States ---
  const [strategySignal, setStrategySignal] = useState<StrategySignal | null>(null)
  const [liveTick, setLiveTick] = useState<LiveTick | null>(null)

  // Sync class state with HTML document root node
  useEffect(() => {
    const root = document.documentElement
    if (theme === 'light') {
      root.classList.add('light-theme')
    } else {
      root.classList.remove('light-theme')
    }
    localStorage.setItem('thrice_theme', theme)

    // Recolor the native window controls (minimize/maximize/close) to match.
    // These are drawn by Chromium itself via BrowserWindow's titleBarOverlay
    // option, NOT by our CSS, which is why they were staying dark in light
    // mode — no amount of .app-header styling can reach them.
    //
    // window.electron.setTitleBarOverlay must be exposed from preload.js as:
    //   setTitleBarOverlay: (opts) => ipcRenderer.send('set-titlebar-overlay', opts)
    // and handled in main.js as:
    //   ipcMain.on('set-titlebar-overlay', (_e, opts) => mainWindow.setTitleBarOverlay(opts))
    // This call is a no-op (optional chaining) until that bridge exists.
    window.electron?.setTitleBarOverlay?.(
      theme === 'light'
        ? { color: '#ffffff', symbolColor: '#14161a' }
        : { color: '#101218', symbolColor: '#f1f5f9' }
    )
  }, [theme])

  // Toggle utility passed down to Sidebar
  const handleThemeToggle = () => {
    setTheme(prev => (prev === 'dark' ? 'light' : 'dark'))
  }

  // ─── Declarations Moved To Top To Prevent Hoisting/Block-Scope Errors ───

  const handleLogout = () => {
    localStorage.removeItem('thrice_token')
    localStorage.removeItem('thrice_user_email')
    localStorage.removeItem('thrice_user_name')
    localStorage.removeItem('thrice_user_avatar')
    setUser(null)

    // Purge any residual OAuth callback parameters in the query string and force reload
    if (window.location.search) {
      window.location.href = window.location.origin + window.location.pathname
    }
  }

  const handleAuthSuccess = (data: { token: string; email: string; name: string; avatar?: string }) => {
    localStorage.setItem('thrice_token', data.token)
    localStorage.setItem('thrice_user_email', data.email)
    localStorage.setItem('thrice_user_name', data.name)
    if (data.avatar) {
      localStorage.setItem('thrice_user_avatar', data.avatar)
    } else {
      localStorage.removeItem('thrice_user_avatar')
    }
    
    // Sync local context metrics with current SQLite values
    api.me()
      .then((profile) => {
        setUser({
          email: profile.email,
          name: profile.name,
          avatar: profile.avatar || undefined,
          clearance_level: profile.clearance_level,
          created_at: profile.created_at
        })
      })
      .catch(() => {
        setUser({ email: data.email, name: data.name, avatar: data.avatar || undefined })
      })
  }

  const refreshUserProfile = () => {
    api.me()
      .then((profile) => {
        setUser({
          email: profile.email,
          name: profile.name,
          avatar: profile.avatar || undefined,
          clearance_level: profile.clearance_level,
          created_at: profile.created_at
        })
      })
      .catch((err) => {
        console.error("Failed to sync system database credentials:", err)
      })
  }

  // ─── React Mounting & Event Binding Effects ───

  useEffect(() => {
    // 1. Check browser URL callback params
    const urlParams = new URLSearchParams(window.location.search)
    const tokenParam = urlParams.get('token')
    const emailParam = urlParams.get('email')
    const nameParam = urlParams.get('name')
    const avatarParam = urlParams.get('avatar')
    
    if (tokenParam && emailParam && nameParam) {
      handleAuthSuccess({
        token: tokenParam,
        email: decodeURIComponent(emailParam),
        name: decodeURIComponent(nameParam),
        avatar: avatarParam ? decodeURIComponent(avatarParam) : undefined
      })
      window.history.replaceState({}, document.title, window.location.pathname)
      setIsVerifying(false)
      return
    }

    // 2. Query /me to verify session cache integrity on boot
    const savedToken = localStorage.getItem('thrice_token')
    if (savedToken) {
      api.me()
        .then((profile) => {
          setUser({
            email: profile.email,
            name: profile.name,
            avatar: profile.avatar || undefined,
            clearance_level: profile.clearance_level,
            created_at: profile.created_at
          })
          setIsVerifying(false)
        })
        .catch(() => {
          handleLogout()
          setIsVerifying(false)
        })
    } else {
      setIsVerifying(false)
    }

    // 3. Register native Electron deep link receiver callbacks
    if (window.electron?.onAuthSuccess) {
      const unsubscribe = window.electron.onAuthSuccess((data) => {
        handleAuthSuccess(data)
      })
      return () => unsubscribe()
    }
  }, [])

  // 4. Listen to postMessage callbacks from the native Google Sign-In popup window
  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (event.data && event.data.type === 'AUTH_SUCCESS') {
        const payload = event.data.data;
        handleAuthSuccess({
          token: payload.token,
          email: decodeURIComponent(payload.email),
          name: decodeURIComponent(payload.name),
          avatar: payload.avatar ? decodeURIComponent(payload.avatar) : undefined
        });
      }
    };
    window.addEventListener('message', handleMessage);
    return () => {
      window.removeEventListener('message', handleMessage);
    };
  }, []);

  // ─── WebSocket Client Setup for Live Ticks ───
  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimeout: NodeJS.Timeout | null = null;

    const connectSocket = () => {
      try {
        socket = api.connectTickSocket();

        socket.onmessage = (event) => {
          try {
            const data: LiveTick = JSON.parse(event.data);
            setLiveTick(data);
          } catch (err) {
            console.error("Error parsing WebSocket tick data:", err);
          }
        };

        socket.onerror = (err) => {
          console.error("Tick WebSocket connection error:", err);
        };

        socket.onclose = () => {
          console.warn("Tick WebSocket disconnected. Reconnecting in 5 seconds...");
          reconnectTimeout = setTimeout(connectSocket, 5000);
        };
      } catch (err) {
        console.error("Tick WebSocket creation failed. Retrying in 5 seconds...", err);
        reconnectTimeout = setTimeout(connectSocket, 5000);
      }
    };

    connectSocket();

    return () => {
      if (reconnectTimeout) {
        clearTimeout(reconnectTimeout);
      }
      if (socket) {
        socket.onclose = null; // Prevent reconnect on intentional unmount
        socket.close();
      }
    };
  }, []);

  // ─── Polling System for Strategy Signal ───
  // Re-runs whenever tvSymbol OR the locked ticker timeframe changes, so the
  // Sidebar's BUY/SHORT/HOLD indicator dot always reflects the SAME timeframe
  // currently locked in the Strategy Workspace — it no longer silently polls
  // the daily default regardless of what's selected.
  //
  // NOTE ON POLL INTERVAL: this endpoint's response includes a Gemini AI
  // narrative (gemini_reasoning), and Gemini's free tier caps out at 20
  // requests/day *total*. The previous 20-second interval burned through
  // that quota in under 7 minutes of the app being open, which is why the
  // "GEMINI AI · PORTFOLIO DECISION BRIEF" card started showing a raw
  // RESOURCE_EXHAUSTED error dump shortly after launch.
  //
  // This interval is a stopgap, not a real fix: even at this slower cadence
  // a multi-hour session will still exceed 20 calls/day. The durable fix is
  // backend-side — cache the Gemini narrative for ~15-30 min per symbol
  // independent of how often the frontend polls for fresh technical
  // indicators, and/or move the Gemini API key off the free tier so the 20
  // RPD ceiling no longer applies.
  const STRATEGY_SIGNAL_POLL_MS = 180000; // 3 min (was 20s)

  useEffect(() => {
    if (!tvSymbol) {
      setStrategySignal(null);
      return;
    }

    const fetchSignal = async () => {
      try {
        const signal = await api.strategySignal(undefined, tickerInterval);
        setStrategySignal(signal);
      } catch (err) {
        console.error("Error polling strategy signal:", err);
      }
    };

    fetchSignal();
    const intervalId = setInterval(fetchSignal, STRATEGY_SIGNAL_POLL_MS);
    return () => { clearInterval(intervalId); };
  }, [tvSymbol, tickerInterval]);

  // Listen to search actions from the ProfilePanel and extended custom events
  useEffect(() => {
    const handleCustomSearch = (e: Event) => {
      const customEvent = e as CustomEvent;
      if (customEvent.detail) {
        const newSymbol = customEvent.detail;
        setTvSymbol(newSymbol);
        // Immediately pre-fetch signal for the new symbol at the currently locked timeframe
        api.strategySignal(undefined, tickerInterval)
          .then((signal) => { setStrategySignal(signal); })
          .catch((err) => { console.error("Pre-fetch of strategy signal failed:", err); });
      }
    };
    window.addEventListener('tv-symbol-change', handleCustomSearch);
    return () => { window.removeEventListener('tv-symbol-change', handleCustomSearch); };
  }, [tickerInterval]);

  const handleGoogleLogin = () => {
    setAuthError('');
    window.open('http://127.0.0.1:8000/api/auth/google?platform=electron', '_blank', 'width=500,height=600');
  }

  if (isVerifying) {
    return (
      <div className="login-overlay-page">
        <div className="overlay-animation-container">
          <div className="scan-line" />
          <div className="hud-container">
            <div className="hud-circle-outer" />
            <div className="hud-circle-inner" />
            <div className="hud-text">TN</div>
          </div>
          <p className="overlay-loading-text">Getting your workspace ready…</p>
        </div>
      </div>
    )
  }

  if (!user) {
    return (
      <div className="login-overlay-page">
        <div className="login-box">
          <div className="login-logo-circle">
            <img src={logoImg} alt="TN" className="login-logo-img" />
          </div>
          <h1 className="login-title">Welcome to Thrice Nomisma</h1>
          <p className="login-tagline">Sign in to pick up right where you left off</p>

          <div className="login-divider" style={{ margin: '24px 0' }}>
            <span>continue with</span>
          </div>

          <button className="login-google-btn" onClick={handleGoogleLogin}>
            <svg viewBox="0 0 24 24" className="google-icon-svg">
              <path fill="#ea4335" d="M12 5.04c1.66 0 3.2.57 4.38 1.69l3.27-3.27C17.67 1.48 14.98 0 12 0 7.35 0 3.38 2.67 1.43 6.56l3.86 3C6.2 6.84 8.89 5.04 12 5.04z" />
              <path fill="#4285f4" d="M23.49 12.27c0-.81-.07-1.59-.2-2.36H12v4.51h6.43c-.28 1.44-1.1 2.66-2.33 3.48l3.61 2.8c2.11-1.95 3.78-4.81 3.78-8.43z" />
              <path fill="#fbbc05" d="M5.29 14.44a7.1 7.1 0 0 1 0-4.88l-3.86-3a11.96 11.96 0 0 0 0 10.88l3.86-3z" />
              <path fill="#34a853" d="M12 24c3.24 0 5.97-1.07 7.96-2.91l-3.61-2.8c-1.1.74-2.52 1.18-4.35 1.18-3.11 0-5.8-1.8-6.71-4.52L1.43 17.5A11.97 11.97 0 0 0 12 24z" />
            </svg>
            Continue with Google
          </button>
          
          {authError && <p className="login-err-msg">{authError}</p>}
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="app-header" />
      <div className="app-body">
        <Sidebar
          activeTab={primaryTab}
          onTabChange={handleTabChange}
          user={user}
          theme={theme}
          onThemeToggle={handleThemeToggle}
          strategySignal={strategySignal}
        />
        <div style={{ flex: 1, height: '100%', overflow: 'hidden', display: primaryTab === 'profile' ? 'none' : 'block' }}>
          <TabbedPanel 
            activeSymbol={tvSymbol} 
            strategySignal={strategySignal} 
            liveTick={liveTick}
            lockedInterval={tickerInterval}
            onIntervalChange={setTickerInterval}
            tab={workspaceTab}
            theme={theme}
            onThemeToggle={handleThemeToggle}
          />
        </div>
        {primaryTab === 'profile' && (
          <div className="profile-tab-view">
            <div className="top-bar">
              <span className="top-bar-title">Profile</span>
              <div className="top-bar-meta" />
              <div className="top-bar-actions">
                <button
                  type="button"
                  className="top-bar-icon-btn"
                  onClick={handleThemeToggle}
                  title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
                >
                  {theme === 'dark' ? (
                    <svg viewBox="0 0 24 24" fill="none">
                      <circle cx="12" cy="12" r="4.5" fill="currentColor" />
                      <g stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                        <line x1="12" y1="1.5" x2="12" y2="4" />
                        <line x1="12" y1="20" x2="12" y2="22.5" />
                        <line x1="1.5" y1="12" x2="4" y2="12" />
                        <line x1="20" y1="12" x2="22.5" y2="12" />
                        <line x1="4.4" y1="4.4" x2="6.2" y2="6.2" />
                        <line x1="17.8" y1="17.8" x2="19.6" y2="19.6" />
                        <line x1="4.4" y1="19.6" x2="6.2" y2="17.8" />
                        <line x1="17.8" y1="6.2" x2="19.6" y2="4.4" />
                      </g>
                    </svg>
                  ) : (
                    <svg viewBox="0 0 24 24"><path fill="currentColor" d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" /></svg>
                  )}
                </button>
              </div>
            </div>
            <ProfilePanel 
              user={user} 
              onLogout={handleLogout} 
              onRefresh={refreshUserProfile} 
            />
          </div>
        )}
      </div>
      <BottomNav
        activeTab={primaryTab}
        onTabChange={handleTabChange}
        userName={user?.name}
        userAvatar={user?.avatar}
      />
    </div>
  );
}