// src/utils/api.ts
import { 
  OrderPayload, 
  RiskCheckResult, 
  SentimentData, 
  StrategySignal, 
  VaultNote, 
  DailyRMSSummary 
} from './types';

// Fallback to localhost if 127.0.0.1 is blocked, or use environment variables if defined
const API_HOST = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';
const BASE_URL = `${API_HOST}/api`;
const WS_URL = 'ws://127.0.0.1:8000/ws/ticks';

const getHeaders = () => {
  // Synchronized to "thrice_token" to match the auth keys used in App.tsx
  const token = localStorage.getItem('thrice_token');
  return {
    'Content-Type': 'application/json',
    'Authorization': token ? `Bearer ${token}` : ''
  };
};

export const api = {
  // Authentication
  me: async () => {
    const res = await fetch(`${BASE_URL}/auth/me`, { headers: getHeaders() });
    if (!res.ok) throw new Error("Auth retrieval failed.");
    return await res.json();
  },

  // Quotes and History
  quote: async (symbol: string) => {
    const res = await fetch(`${BASE_URL}/quote/${encodeURIComponent(symbol)}`, { headers: getHeaders() });
    if (!res.ok) throw new Error("Quote retrieval failed.");
    return await res.json();
  },

  // Secrets Vault
  getVaultNotes: async (): Promise<VaultNote[]> => {
    const res = await fetch(`${BASE_URL}/auth/vault/list`, { headers: getHeaders() });
    if (!res.ok) throw new Error("Vault retrieval failed.");
    return await res.json();
  },

  saveVaultNote: async (payload: { title: string; content: string }) => {
    const res = await fetch(`${BASE_URL}/auth/vault/save`, {
      method: 'POST',
      headers: getHeaders(),
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error("Vault lock write failed.");
    return await res.json();
  },

  // ─── Programmatic Strategy, NLP, & RMS Calls ───────────────────────────────

  sentiment: async (symbol: string): Promise<SentimentData> => {
    const res = await fetch(`${BASE_URL}/active/sentiment?symbol=${encodeURIComponent(symbol)}`, { headers: getHeaders() });
    if (!res.ok) throw new Error("NLP Sentiment evaluation failed.");
    return await res.json();
  },

  strategySignal: async (horizon?: string, interval?: string): Promise<StrategySignal> => {
    const params = new URLSearchParams();
    if (horizon) params.set('horizon', horizon);
    if (interval) params.set('interval', interval);
    const qs = params.toString();
    const url = `${BASE_URL}/active/strategy-signal${qs ? `?${qs}` : ''}`;
    const res = await fetch(url, { headers: getHeaders() });
    if (!res.ok) throw new Error("Failed to evaluate decision matrix.");
    return await res.json();
  },

  riskCheck: async (qty: number, price: number): Promise<RiskCheckResult> => {
    const res = await fetch(`${BASE_URL}/active/risk-check?qty=${qty}&price=${price}`, { headers: getHeaders() });
    if (!res.ok) throw new Error("RMS clearance check rejected.");
    return await res.json();
  },

  getDailyRmsSummary: async (): Promise<DailyRMSSummary> => {
    const res = await fetch(`${BASE_URL}/risk/daily-summary`, { headers: getHeaders() });
    if (!res.ok) {
      // Return a sensible default or retry fallback
      return { trades_placed: 0, drawdown_consumed_pct: 0.0, remaining_capital: 1000000 };
    }
    return await res.json();
  },

  placeOrder: async (payload: OrderPayload) => {
    const res = await fetch(`${BASE_URL}/orders/place`, {
      method: 'POST',
      headers: getHeaders(),
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const errData = await res.json();
      throw new Error(errData.detail || "Transaction failed.");
    }
    return await res.json();
  },

  // ─── Native WebSockets Connection Helper ───
  connectTickSocket: (): WebSocket => {
    return new WebSocket(WS_URL);
  }
};