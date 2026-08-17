// src/utils/types.ts

// --- Authentication & Session Models ---
export interface UserProfile {
  email: string;
  name: string;
  avatar?: string;
  clearance_level?: string;
  created_at?: number;
}

export interface VaultNote {
  note_title: string;
  note_content: string;
  updated_at: number;
}

// --- Market Data Models ---
export interface Quote {
  symbol: string;
  shortName: string | null;
  longName: string | null;
  exchange: string | null;
  currency: string | null;
  currentPrice: number | null;
  previousClose: number | null;
  open: number | null;
  dayHigh: number | null;
  dayLow: number | null;
  volume: number | null;
  avgVolume: number | null;
  marketCap: number | null;
  fiftyTwoWeekHigh: number | null;
  fiftyTwoWeekLow: number | null;
  bid: number | null;
  ask: number | null;
  bidSize: number | null;
  askSize: number | null;
}

export interface Fundamentals {
  symbol: string;
  trailingPE: number | null;
  forwardPE: number | null;
  priceToBook: number | null;
  priceToSalesTrailing12Months: number | null;
  trailingEps: number | null;
  forwardEps: number | null;
  bookValue: number | null;
  revenuePerShare: number | null;
  returnOnEquity: number | null;
  returnOnAssets: number | null;
  operatingMargins: number | null;
  profitMargins: number | null;
  grossMargins: number | null;
  ebitdaMargins: number | null;
  debtToEquity: number | null;
  currentRatio: number | null;
  quickRatio: number | null;
  totalCash: number | null;
  totalDebt: number | null;
  totalRevenue: number | null;
  freeCashflow: number | null;
  operatingCashflow: number | null;
  earningsGrowth: number | null;
  revenueGrowth: number | null;
  dividendYield: number | null;
  payoutRatio: number | null;
  beta: number | null;
  enterpriseValue: number | null;
  enterpriseToRevenue: number | null;
  enterpriseToEbitda: number | null;
  sharesOutstanding: number | null;
  floatShares: number | null;
  heldPercentInsiders: number | null;
  heldPercentInstitutions: number | null;
  shortRatio: number | null;
  shortPercentOfFloat: number | null;
}

export interface Technicals {
  symbol: string;
  sma20: number | null;
  sma50: number | null;
  sma200: number | null;
  ema9: number | null;
  ema21: number | null;
  rsi: number | null;
  macd: number | null;
  macdSignal: number | null;
  macdHist: number | null;
  bbUpper: number | null;
  bbLower: number | null;
  atr: number | null;
  vwap: number | null;
  obv: number | null;
  stochK: number | null;
  stochD: number | null;
}

export interface BatchQuote {
  symbol: string;
  shortName: string | null;
  currentPrice: number | null;
  previousClose: number | null;
  change: number | null;
  changePct: number | null;
  volume: number | null;
  marketCap: number | null;
  error?: string;
}

// --- Option Chain Models ---
export interface OptionContract {
  contractSymbol: string;
  strike: number;
  lastPrice: number;
  bid: number;
  ask: number;
  volume: number;
  openInterest: number;
  impliedVolatility: number;
  inTheMoney: boolean;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
}

export interface OptionsChain {
  symbol: string;
  expiry: string;
  expirations: string[];
  calls: OptionContract[];
  puts: OptionContract[];
}

// --- Strategy and RMS Models ---

// Merged to keep both existing keys and new requested keys
export interface SentimentData {
  score: number;
  headlines: string[];
  verdict: 'positive' | 'neutral' | 'negative';
  // Existing fields
  symbol?: string;
  sentiment?: 'bullish' | 'bearish' | 'neutral';
  sentiment_score?: number;
  engine?: string;
  // --- Corporate-grade additions ---
  magnitude?: 'STRONGLY POSITIVE' | 'POSITIVE' | 'MILDLY POSITIVE' | 'NEUTRAL' | 'MILDLY NEGATIVE' | 'NEGATIVE' | 'STRONGLY NEGATIVE';
  confidence?: number;
  momentum?: 'IMPROVING' | 'STABLE' | 'DETERIORATING';
  article_count?: number;
  source_diversity?: number;
  sources?: string[];
}

// Regime-adjusted confidence read-out from services/signal_arbitration.py --
// weighs the active strategy's raw confidence against the same composite
// category-plane analysis the Signal Intelligence panel shows, instead of
// letting a single strategy's number stand unchallenged. Optional because
// older cached responses / a failed arbitration pass omit it gracefully.
// --- Six-feature arbitration extension (services/signal_arbitration.py) ---

// Feature 1: did the strategy's own indicator inputs actually populate
// before it emitted a confidence number (catches e.g. the EMA-null bug).
export interface DataIntegrityCheck {
  complete: boolean;
  completeness_ratio: number;
  missing_keys: string[];
  reasoning: string;
}

// Feature 4: is price walking outside a Bollinger band across several
// consecutive candles, vs. a single overshoot -- mean-reversion only.
export interface BandWalkCheck {
  walking: boolean;
  consecutive_candles: number;
  direction: 'upper' | 'lower' | 'none';
  discount_factor: number;
  reasoning: string;
}

// Feature 2: does this signal's direction agree with the trend plane on
// other timeframes (e.g. 15m/1d/1wk), not just the active interval.
export interface TimeframeAlignmentCheck {
  alignment_pct: number;
  agreeing: string[];
  disagreeing: string[];
  reasoning: string;
}

export interface ArbitrationResult {
  strategy_style: 'mean_reversion' | 'trend_following';
  action: string;
  raw_confidence: number;
  adjusted_confidence: number;
  discount_factor: number;
  regime: 'TRENDING' | 'RANGING' | 'VOLATILE' | 'BREAKOUT' | 'CONFLICTED' | 'LOW_LIQUIDITY_DATA';
  regime_confidence: number;
  regime_reasoning: string;
  trend_composite: number;
  conflict_pct: number;
  conflict_cause: string;
  reasoning: string;
  // Feature 3: adjusted_confidence turned into a 0.1–1.0 scalar routers/orders.py
  // can multiply proposed order quantity by.
  position_size_multiplier: number;
  data_integrity: DataIntegrityCheck | null;
  band_walk: BandWalkCheck | null;
  timeframe_alignment: TimeframeAlignmentCheck | null;
}

// Feature 6: backs GET /active/regime-alerts -- a symbol's regime flipping
// (e.g. RANGING -> TRENDING), surfaced via polling since no WS push exists yet.
export interface RegimeTransitionEvent {
  symbol: string;
  interval: string;
  from_regime: string;
  to_regime: string;
  regime_confidence: number;
  ts: number;
}

// Merged to keep both existing keys and new requested keys
export interface StrategySignal {
  signal: string;
  sentiment_score: number;
  is_bullish_trend: boolean;
  horizon: 'intraday' | 'swing' | 'investment';
  // Existing fields
  action?: 'BUY' | 'SELL' | 'HOLD';
  confidence?: number;
  reasoning?: string;
  // --- Signal arbitration layer ---
  arbitration?: ArbitrationResult;
}

// Merged to keep both existing keys and new requested keys
export interface RiskCheckResult {
  cleared: boolean;
  breach_reason?: string;
  max_qty_allowed: number;
  // Existing fields
  passed?: boolean;
  reason?: string;
  // --- Corporate-grade additions ---
  allocated_pct?: number;
  risk_rating?: 'LOW' | 'MODERATE' | 'ELEVATED' | 'HIGH' | 'CRITICAL';
  recommended_position_pct?: number;
  value_at_risk?: {
    var_95_1d: number;
    var_99_1d: number;
    volatility_used_pct: number;
    volatility_estimated: boolean;
  };
  // --- Feature 3: confidence-based position sizing (routers/orders.py) ---
  // Present only when a recent (<15min) arbitration result was cached for
  // this symbol; absent = the sizing gate never engaged, behaves as before.
  signal_confidence_multiplier?: number;
  signal_regime?: string;
  confidence_adjusted_max_qty?: number;
}

export interface DailyRMSSummary {
  trades_placed: number;
  drawdown_consumed_pct: number;
  remaining_capital: number;
}

// Merged to keep both existing keys and new requested keys
export interface OrderPayload {
  symbol: string;
  token: string;
  exchange: string;
  qty: number;
  order_type: 'MARKET' | 'LIMIT';
  price?: number;
  // Existing fields
  side?: 'BUY' | 'SELL';
  quantity?: number;
  type?: 'MARKET' | 'LIMIT';
}

// New LiveTick interface
export interface LiveTick {
  token: string;
  ltp: number;
  change_pct: number;
  timestamp: string;
}

// --- Navigation & UI Types ---
export type SidebarTab = 'markets' | 'technicals' | 'fundamentals' | 'options' | 'strategy';