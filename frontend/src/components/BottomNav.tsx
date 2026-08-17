import React from 'react'

export type PrimaryTab = 'ticker' | 'tradingview' | 'angelone' | 'strategy' | 'indicators' | 'papertrading' | 'pnltracker' | 'profile'

interface NavItem {
  key: PrimaryTab
  label: string
  icon: React.ReactNode
}

interface Props {
  activeTab: PrimaryTab
  onTabChange: (tab: PrimaryTab) => void
  openPositionsCount?: number
  userName?: string
  userAvatar?: string
}

const ICONS: Record<string, React.ReactNode> = {
  charts:     <span className="material-symbols-outlined bottom-nav-icon">bar_chart</span>,
  smartapi:   <span className="material-symbols-outlined bottom-nav-icon">account_balance</span>,
  strategy:   <span className="material-symbols-outlined bottom-nav-icon">psychology</span>,
  indicators: <span className="material-symbols-outlined bottom-nav-icon">query_stats</span>,
  ticker:     <span className="material-symbols-outlined bottom-nav-icon" style={{ fontVariationSettings: "'FILL' 1" }}>show_chart</span>,
  orders:     <span className="material-symbols-outlined bottom-nav-icon">list_alt</span>,
  ledger:     <span className="material-symbols-outlined bottom-nav-icon">history</span>,
}

const NAV_ITEMS: NavItem[] = [
  { key: 'ticker',       label: 'Ticker',   icon: ICONS.ticker },
  { key: 'tradingview',  label: 'Charts',   icon: ICONS.charts },
  { key: 'angelone',     label: 'Broker',   icon: ICONS.smartapi },
  { key: 'strategy',     label: 'Strategy', icon: ICONS.strategy },
  { key: 'indicators',   label: 'Indicators', icon: ICONS.indicators },
  { key: 'papertrading', label: 'Orders',   icon: ICONS.orders },
  { key: 'pnltracker',   label: 'History',  icon: ICONS.ledger },
  { key: 'profile',      label: 'Profile',  icon: null } // icon rendered specially below (avatar/initials)
]

export default function BottomNav({ activeTab, onTabChange, openPositionsCount, userName, userAvatar }: Props) {
  const [avatarError, setAvatarError] = React.useState(false)

  const getInitials = (name?: string) => {
    if (!name) return 'U'
    const parts = name.trim().split(/\s+/)
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
    return name.slice(0, 2).toUpperCase()
  }

  return (
    <nav className="bottom-nav">
      {NAV_ITEMS.map(item => {
        const isActive = activeTab === item.key
        const isProfile = item.key === 'profile'
        return (
          <button
            key={item.key}
            type="button"
            className={`bottom-nav-item ${isActive ? 'active' : ''}`}
            onClick={() => onTabChange(item.key)}
          >
            <span className="bottom-nav-icon-wrap">
              {isProfile ? (
                userAvatar && !avatarError ? (
                  <img
                    src={userAvatar}
                    alt={userName || 'Profile'}
                    onError={() => setAvatarError(true)}
                    style={{ width: 22, height: 22, borderRadius: '50%', objectFit: 'cover' }}
                  />
                ) : (
                  <span style={{
                    width: 22, height: 22, borderRadius: '50%', display: 'flex',
                    alignItems: 'center', justifyContent: 'center', fontSize: 9, fontWeight: 700,
                    background: 'var(--gold)', color: '#fff'
                  }}>
                    {getInitials(userName)}
                  </span>
                )
              ) : item.icon}
              {item.key === 'papertrading' && !!openPositionsCount && (
                <span className="bottom-nav-badge">{openPositionsCount > 9 ? '9+' : openPositionsCount}</span>
              )}
            </span>
            <span className="bottom-nav-label">{item.label}</span>
          </button>
        )
      })}
    </nav>
  )
}