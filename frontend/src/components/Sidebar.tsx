// components/Sidebar.tsx
//
// Desktop left icon-rail nav ("Aero Terminal" layout). Renders at widths
// ≥861px; BottomNav takes over below that (see the matching media queries
// in global.css: .sidebar / .bottom-nav). Shares the same PrimaryTab set
// and the same onTabChange handler as BottomNav so both can drive App.tsx's
// single source of truth for the active workspace tab.
import React, { useState } from 'react'
import { PrimaryTab } from './BottomNav'
import { UserProfile, StrategySignal } from '../utils/types'
import logoImg from '../assets/logo.png'

interface NavItem {
  key: PrimaryTab
  label: string
  icon: React.ReactNode
}

interface Props {
  activeTab: PrimaryTab
  onTabChange: (tab: PrimaryTab) => void
  user: UserProfile | null
  theme: string
  onThemeToggle: () => void
  strategySignal?: StrategySignal | null
  openPositionsCount?: number
}

const ICONS: Record<string, React.ReactNode> = {
  ticker:     <span className="material-symbols-outlined sidebar-nav-icon" style={{ fontVariationSettings: "'FILL' 1" }}>show_chart</span>,
  charts:     <span className="material-symbols-outlined sidebar-nav-icon">bar_chart</span>,
  smartapi:   <span className="material-symbols-outlined sidebar-nav-icon">account_balance</span>,
  strategy:   <span className="material-symbols-outlined sidebar-nav-icon">psychology</span>,
  indicators: <span className="material-symbols-outlined sidebar-nav-icon">query_stats</span>,
  orderbook:  <span className="material-symbols-outlined sidebar-nav-icon">receipt_long</span>,
  orders:     <span className="material-symbols-outlined sidebar-nav-icon">list_alt</span>,
  ledger:     <span className="material-symbols-outlined sidebar-nav-icon">history</span>,
}

const NAV_ITEMS: NavItem[] = [
  { key: 'ticker',       label: 'Ticker',     icon: ICONS.ticker },
  { key: 'orderbook',    label: 'Order Book', icon: ICONS.orderbook },
  { key: 'tradingview',  label: 'Charts',     icon: ICONS.charts },
  { key: 'angelone',     label: 'Broker',     icon: ICONS.smartapi },
  { key: 'strategy',     label: 'Strategy',   icon: ICONS.strategy },
  { key: 'indicators',   label: 'Indicators', icon: ICONS.indicators },
  { key: 'papertrading', label: 'Orders',     icon: ICONS.orders },
  { key: 'pnltracker',   label: 'History',    icon: ICONS.ledger },
]

export default function Sidebar({
  activeTab,
  onTabChange,
  user,
  theme,
  onThemeToggle,
  strategySignal,
  openPositionsCount
}: Props) {
  const [imgError, setImgError] = useState(false)

  const getInitials = (name?: string) => {
    if (!name) return 'U'
    const parts = name.trim().split(/\s+/)
    if (parts.length >= 2) {
      return (parts[0][0] + parts[1][0]).toUpperCase()
    }
    return name.slice(0, 2).toUpperCase()
  }

  // Gold = BUY, Red = SHORT, Grey = HOLD/neutral — same read as the old
  // sidebar's status dot, now shown against the Strategy nav item.
  const getSignalColor = () => {
    if (!strategySignal || !strategySignal.signal) return null
    const sig = strategySignal.signal.toUpperCase()
    if (sig === 'BUY') return 'var(--gold)'
    if (sig === 'SHORT' || sig === 'SELL') return 'var(--negative)'
    return 'var(--silver-dim)'
  }
  const signalColor = getSignalColor()

  return (
    <aside className="sidebar">
      <div className="sidebar-brand-wrapper">
        <div className="logo-ring-outer">
          <div className="logo-ring-inner">
            <img src={logoImg} alt="TN" className="sidebar-logo" />
          </div>
        </div>
      </div>

      <nav className="sidebar-nav">
        {NAV_ITEMS.map(item => {
          const isActive = activeTab === item.key
          return (
            <button
              key={item.key}
              type="button"
              className={`sidebar-nav-item ${isActive ? 'active' : ''}`}
              onClick={() => onTabChange(item.key)}
              title={item.label}
            >
              {item.icon}
              <span>{item.label}</span>
              {item.key === 'strategy' && signalColor && (
                <span
                  className="status-pulse-dot"
                  style={{ position: 'absolute', top: 8, right: 12, background: signalColor, boxShadow: `0 0 6px ${signalColor}` }}
                />
              )}
              {item.key === 'papertrading' && !!openPositionsCount && (
                <span className="sidebar-nav-badge">{openPositionsCount > 9 ? '9+' : openPositionsCount}</span>
              )}
            </button>
          )
        })}
      </nav>

      <div className="sidebar-footer-indicator" style={{ display: 'flex', flexDirection: 'column', gap: '16px', alignItems: 'center' }}>
        <div className="sidebar-profile-area" onClick={() => onTabChange('profile')} title="VIEW PROFILE">
          {user?.avatar && !imgError ? (
            <img
              src={user.avatar}
              alt={user.name}
              className="sidebar-avatar-img"
              onError={() => setImgError(true)}
            />
          ) : (
            <div className="sidebar-avatar-placeholder">
              {user?.name ? getInitials(user.name) : 'U'}
            </div>
          )}
          <span className="sidebar-profile-glow" />
        </div>
      </div>
    </aside>
  )
}