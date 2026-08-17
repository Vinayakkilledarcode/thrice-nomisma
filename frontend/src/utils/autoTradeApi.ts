// src/utils/autoTradeApi.ts
const API_HOST = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';
const BASE_URL = `${API_HOST}/api/autotrade`;

export interface AutoTradePosition {
  symbol: string;
  exchange: string;
  token: string;
  side: 'BUY' | 'SELL';
  qty: number;
  entry_price: number;
  sl_price: number;
  target_price: number;
  entry_time: string;
  entry_order_id?: string;
  entry_confidence: number;
  regime?: string;
}

export interface AutoTradeHistoryEntry extends AutoTradePosition {
  exit_time: string;
  exit_price: number | null;
  exit_reason: 'stop_loss' | 'target' | 'square_off_time' | 'manual_override';
  exit_order_id?: string;
  exit_failed: boolean;
}

export interface AutoTradeConfig {
  enabled: boolean;
  watchlist: { symbol: string; exchange: string }[];
  confidence_threshold: number;
  base_qty_per_trade: number;
  max_concurrent_positions: number;
  poll_interval_seconds: number;
  sl_pct: number;
  target_pct: number;
  market_open: string;
  market_close: string;
  square_off_time: string;
}

export interface AutoTradeStatus {
  config: AutoTradeConfig;
  open_positions: Record<string, AutoTradePosition>;
  recent_history: AutoTradeHistoryEntry[];
  within_market_hours: boolean;
}

export const autoTradeApi = {
  status: async (): Promise<AutoTradeStatus> => {
    const res = await fetch(`${BASE_URL}/status`);
    if (!res.ok) throw new Error('Failed to fetch autotrade status.');
    return await res.json();
  },

  enable: async () => {
    const res = await fetch(`${BASE_URL}/enable`, { method: 'POST' });
    if (!res.ok) throw new Error('Failed to enable autotrade.');
    return await res.json();
  },

  disable: async () => {
    const res = await fetch(`${BASE_URL}/disable`, { method: 'POST' });
    if (!res.ok) throw new Error('Failed to disable autotrade.');
    return await res.json();
  },

  updateConfig: async (patch: Partial<AutoTradeConfig>) => {
    const res = await fetch(`${BASE_URL}/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch)
    });
    if (!res.ok) throw new Error('Failed to update autotrade config.');
    return await res.json();
  },

  forceSquareOff: async (exchange: string, symbol: string) => {
    const res = await fetch(`${BASE_URL}/force-square-off/${encodeURIComponent(exchange)}/${encodeURIComponent(symbol)}`, {
      method: 'POST'
    });
    if (!res.ok) throw new Error('Failed to submit manual square-off.');
    return await res.json();
  }
};
