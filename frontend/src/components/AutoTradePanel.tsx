// components/AutoTradePanel.tsx
//
// Pure presentational control surface. All the actual state (config, open
// autotrade positions, activity log) and all the actual logic (entry on
// Strategy Workspace signal change, exit on SL/target/square-off) live in
// TabbedPanel.tsx, right next to the existing paper-trading engine
// (executeCoreOrder / handleSquareOff) it fires into -- so autotrade
// entries land in the SAME balance/positions the Orders tab already shows.
//
// This intentionally does NOT call any backend endpoint. An earlier version
// of this file polled a FastAPI autotrade service that placed LIVE broker
// orders through Angel One -- that's a different system from the paper
// trading engine in the Orders tab, which is why trades placed by that
// version never showed up there. This version only ever touches the local
// paper engine, so what you see here IS what you'll see in Orders.
import React from 'react'

// Minimal local shape of the fields this panel actually reads off a paper
// Position (avoids importing TabbedPanel's internal, unexported Position
// interface and creating a circular/coupling dependency).
interface PaperPositionSlice {
  qty: number
  entryPrice: number
  currentPrice: number
}

interface AutoAckPosition {
  symbol: string
  meta: { slPrice: number; targetPrice: number; entryTime: number; side: 'LONG' | 'SHORT' }
  pos: PaperPositionSlice | null
}

interface Props {
  enabled: boolean
  onToggle: () => void
  currentSymbol: string
  currentSignal?: string
  qty: number
  onQtyChange: (n: number) => void
  lotSize: number
  onLotSizeChange: (n: number) => void
  leverage: number
  onLeverageChange: (n: number) => void
  slPct: number
  onSlPctChange: (n: number) => void
  targetPct: number
  onTargetPctChange: (n: number) => void
  squareOffTime: string
  onSquareOffTimeChange: (s: string) => void
  autoPositions: AutoAckPosition[]
  log: { time: number; message: string }[]
  onManualSquareOff: (symbol: string) => void
}

export default function AutoTradePanel({
  enabled, onToggle, currentSymbol, currentSignal,
  qty, onQtyChange, lotSize, onLotSizeChange, leverage, onLeverageChange,
  slPct, onSlPctChange, targetPct, onTargetPctChange,
  squareOffTime, onSquareOffTimeChange,
  autoPositions, log, onManualSquareOff
}: Props) {
  const signalUpper = (currentSignal || 'HOLD').toUpperCase()
  const signalColor = signalUpper === 'BUY' ? 'var(--positive)' : (signalUpper === 'SELL' || signalUpper === 'SHORT') ? 'var(--negative)' : 'var(--silver-dim)'

  const inputStyle: React.CSSProperties = {
    background: 'var(--bg-base)', border: '1px solid var(--border)',
    borderRadius: '4px', color: 'var(--silver-bright)', fontSize: '11px',
    padding: '6px 8px', fontFamily: 'var(--font-data)', outline: 'none', width: '100%'
  }
  const labelStyle: React.CSSProperties = { fontSize: '10px', color: 'var(--silver-dim)', marginBottom: '4px', display: 'block' }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        <div>
          <h3 style={{ margin: 0, fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: 14, letterSpacing: '0.05em' }}>
            AUTONOMOUS EXECUTION
          </h3>
          <span style={{ fontSize: 11, color: 'var(--silver-dim)' }}>
            {currentSymbol || 'No symbol'} · signal{' '}
            <span style={{ color: signalColor, fontWeight: 700 }}>{signalUpper}</span>
            {' '}· fires into the Orders tab paper engine
          </span>
        </div>
        <button
          type="button"
          onClick={onToggle}
          style={{
            padding: '8px 16px', borderRadius: 6, border: '1px solid var(--gold)',
            background: enabled ? 'var(--gold)' : 'transparent',
            color: enabled ? '#0c0e12' : 'var(--gold)', fontWeight: 700, cursor: 'pointer'
          }}
        >
          {enabled ? 'AUTOTRADE: ON' : 'AUTOTRADE: OFF'}
        </button>
      </div>

      {/* ── config ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 10 }}>
        <div>
          <label style={labelStyle}>Qty</label>
          <input type="number" min={1} value={qty} onChange={e => onQtyChange(Math.max(1, parseInt(e.target.value) || 1))} style={inputStyle} />
        </div>
        <div>
          <label style={labelStyle}>Lot size</label>
          <input type="number" min={1} value={lotSize} onChange={e => onLotSizeChange(Math.max(1, parseInt(e.target.value) || 1))} style={inputStyle} />
        </div>
        <div>
          <label style={labelStyle}>Leverage</label>
          <select value={leverage} onChange={e => onLeverageChange(parseInt(e.target.value))} style={inputStyle}>
            <option value={1}>1x Delivery</option>
            <option value={5}>5x Intraday</option>
            <option value={10}>10x Intraday+</option>
          </select>
        </div>
        <div>
          <label style={labelStyle}>Stop-loss %</label>
          <input type="number" step="0.1" min={0.1} value={(slPct * 100).toFixed(1)} onChange={e => onSlPctChange(Math.max(0.001, (parseFloat(e.target.value) || 1) / 100))} style={inputStyle} />
        </div>
        <div>
          <label style={labelStyle}>Target %</label>
          <input type="number" step="0.1" min={0.1} value={(targetPct * 100).toFixed(1)} onChange={e => onTargetPctChange(Math.max(0.001, (parseFloat(e.target.value) || 2) / 100))} style={inputStyle} />
        </div>
        <div>
          <label style={labelStyle}>Square-off time</label>
          <input type="time" value={squareOffTime} onChange={e => onSquareOffTimeChange(e.target.value)} style={inputStyle} />
        </div>
      </div>

      {/* ── open autotrade positions ── */}
      <div>
        <h4 style={{ marginBottom: 8, fontSize: 12, color: 'var(--silver-dim)' }}>Open Positions ({autoPositions.length})</h4>
        {autoPositions.length === 0 && <p style={{ fontSize: 12, color: 'var(--silver-dim)' }}>No open autotrade positions. They'll appear here AND in the Orders tab.</p>}
        {autoPositions.map(({ symbol, meta, pos }) => (
          <div
            key={symbol}
            style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '10px 14px', border: '1px solid rgba(201,168,76,0.2)', borderRadius: 6, marginBottom: 8
            }}
          >
            <div>
              <strong>{symbol}</strong>{' '}
              <span style={{ color: meta.side === 'LONG' ? 'var(--positive)' : 'var(--negative)' }}>{meta.side}</span>
              {pos && <> × {pos.qty}</>}
              <div style={{ fontSize: 11, color: 'var(--silver-dim)' }}>
                {pos ? `LTP ₹${pos.currentPrice.toFixed(2)} · Entry ₹${pos.entryPrice.toFixed(2)} · ` : ''}
                SL ₹{meta.slPrice.toFixed(2)} · TGT ₹{meta.targetPrice.toFixed(2)}
              </div>
            </div>
            <button
              type="button"
              onClick={() => onManualSquareOff(symbol)}
              style={{ padding: '6px 10px', borderRadius: 4, border: '1px solid var(--negative)', background: 'transparent', color: 'var(--negative)', cursor: 'pointer' }}
            >
              Square Off
            </button>
          </div>
        ))}
      </div>

      {/* ── activity log ── */}
      <div>
        <h4 style={{ marginBottom: 8, fontSize: 12, color: 'var(--silver-dim)' }}>Activity</h4>
        {log.length === 0 && <p style={{ fontSize: 12, color: 'var(--silver-dim)' }}>No autotrade activity yet.</p>}
        {log.slice(0, 10).map((entry, i) => (
          <div key={i} style={{ fontSize: 11, color: 'var(--silver-dim)', padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
            {new Date(entry.time).toLocaleTimeString()} — {entry.message}
          </div>
        ))}
      </div>
    </div>
  )
}