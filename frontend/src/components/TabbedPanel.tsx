import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { api } from '../utils/api';
import { StrategySignal, LiveTick, SentimentData, DailyRMSSummary } from '../utils/types';
import IndicatorWaveform3D, { WaveformPoint } from './IndicatorWaveform3D';
import PlaneOverlayAnalysis from './PlaneOverlayAnalysis';
import SignalIntelligencePanel from './SignalIntelligencePanel';

type Tab = 'ticker' | 'orderbook' | 'angelone' | 'tradingview' | 'papertrading' | 'pnltracker' | 'strategy' | 'indicators';

interface ActiveSymbol {
  token: string;
  symbol: string;
  exchange: string;
  name: string;
}

interface Position {
  id: string;            // Unique transaction reference ID
  symbol: string;
  qty: number;
  lotSize: number;       // Multiplier size (e.g., options multipliers)
  entryPrice: number;
  currentPrice: number;
  type: 'LONG' | 'SHORT';
  leverage: number;      // 1 for delivery, 5+ for intraday leverage margins
  timestamp: number;
}

interface OrderHistoryItem {
  timestamp: number;
  symbol: string;
  type: 'BUY' | 'SELL';
  qty: number;
  lotSize: number;
  price: number;
  orderType: 'MARKET' | 'LIMIT';
  status: 'FILLED' | 'REJECTED';
  rejectionReason?: string;
}

interface RealizedTrade {
  id: string;            // Retained reference ID from open state
  timestamp: number;
  symbol: string;
  entryPrice: number;
  exitPrice: number;
  qty: number;
  lotSize: number;
  type: 'LONG' | 'SHORT';
  points: number;
  pnl: number;
  status: 'CLOSED';
}

interface ToastMessage {
  id: string;
  text: string;
  error: boolean;
}

const BACKEND = 'http://127.0.0.1:8000';

const generateRefId = () => {
  return 'TXN-' + Math.random().toString(36).substring(2, 8).toUpperCase();
};

// Groups a timestamp into "Today" / "Yesterday" / "N days ago" / calendar date,
// mirroring the reference app's History list section headers.
const dayBucketLabel = (timestamp: number): string => {
  const now = new Date();
  const then = new Date(timestamp);
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diffDays = Math.round((startOfDay(now) - startOfDay(then)) / 86400000);
  if (diffDays <= 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  if (diffDays <= 6) return `${diffDays} days ago`;
  return then.toLocaleDateString('en-GB');
};

// ─── Indicator Engine helpers (backend: services/indicator_engine.py) ──────
// Category keys must match services/indicators/*.py's CATEGORY constants.
const INDICATOR_CATEGORY_ORDER = ['trend', 'momentum', 'volatility', 'volume', 'statistical', 'price_action', 'candlestick'] as const;

const INDICATOR_CATEGORY_LABELS: Record<string, string> = {
  trend: 'Trend',
  momentum: 'Momentum',
  volatility: 'Volatility',
  volume: 'Volume',
  statistical: 'Statistical',
  price_action: 'Price Action',
  candlestick: 'Candlestick',
};

// Every exact Ticker timeframe, individually selectable/lockable — mirrors
// the Live Telemetry panel's own SECONDS/MINUTES/HOURS/DAYS <optgroup> list
// exactly (same 20 values), so "all timeframes" in Indicators really does
// mean all of them, not just the 3 backend-bucket approximations.
const TICKER_INTERVAL_GROUPS: { groupLabel: string; options: { value: string; label: string }[] }[] = [
  {
    groupLabel: 'SECONDS',
    options: [
      { value: '15s', label: '15s' },
      { value: '30s', label: '30s' },
      { value: '45s', label: '45s' },
    ],
  },
  {
    groupLabel: 'MINUTES',
    options: [
      { value: '1m',  label: '1m' },
      { value: '2m',  label: '2m' },
      { value: '3m',  label: '3m' },
      { value: '4m',  label: '4m' },
      { value: '5m',  label: '5m' },
      { value: '10m', label: '10m' },
      { value: '15m', label: '15m' },
      { value: '30m', label: '30m' },
      { value: '75m', label: '75m' },
      { value: '125m', label: '125m' },
    ],
  },
  {
    groupLabel: 'HOURS',
    options: [
      { value: '1h', label: '1h' },
      { value: '2h', label: '2h' },
      { value: '3h', label: '3h' },
      { value: '4h', label: '4h' },
    ],
  },
  {
    groupLabel: 'DAYS',
    options: [
      { value: '1d',  label: '1d' },
      { value: '1w',  label: '1w' },
      { value: '1mo', label: '1mo' },
    ],
  },
];

// Flat lookup of every interval's display label, used wherever a single
// value (not the whole grouped list) needs a friendly label.
const INDICATOR_TIMEFRAME_LABELS: Record<string, string> = Object.fromEntries(
  TICKER_INTERVAL_GROUPS.flatMap(g => g.options.map(o => [o.value, o.label]))
);

// The 3 sub-minute values (15s/30s/45s) have no true native data upstream —
// see services/market.py's get_ohlcv_dataframe_for_ticker_interval() for
// why — so the UI labels them as an approximation rather than implying
// second-level precision that doesn't exist.
const INDICATOR_APPROXIMATED_INTERVALS = new Set(['15s', '30s', '45s']);

// Deliberately conservative: only signals with an unambiguous directional
// read get colored green/red. Context-dependent ones (RANGING, TRENDING,
// MOMENTUM, MEAN_REVERTING, EXPANDING/CONTRACTING, etc.) stay gold/neutral
// rather than guessing a direction the backend didn't actually assert.
const INDICATOR_POSITIVE_SIGNALS = new Set(['BULLISH', 'OVERSOLD', 'GAP_UP', 'NEAR_LOW', 'BROKE_STRUCTURE_UP']);
const INDICATOR_NEGATIVE_SIGNALS = new Set(['BEARISH', 'OVERBOUGHT', 'GAP_DOWN', 'NEAR_HIGH', 'BROKE_STRUCTURE_DOWN']);
const INDICATOR_DIM_SIGNALS = new Set(['NOT_PRESENT', 'ERROR', 'INSUFFICIENT_DATA']);

const indicatorSignalColor = (signal?: string): string => {
  if (!signal) return 'var(--silver-dim)';
  if (INDICATOR_POSITIVE_SIGNALS.has(signal)) return 'var(--positive)';
  if (INDICATOR_NEGATIVE_SIGNALS.has(signal)) return 'var(--negative)';
  if (INDICATOR_DIM_SIGNALS.has(signal)) return 'var(--silver-dim)';
  return 'var(--gold)';
};

const formatIndicatorName = (name: string): string =>
  name.split('_').map(w => w.toUpperCase()).join(' ');

const formatIndicatorNumber = (value: number): string => {
  if (Number.isNaN(value)) return '—';
  const abs = Math.abs(value);
  if (abs === 0) return '0';
  if (abs >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return value.toFixed(abs < 1 ? 4 : 2);
};

const formatIndicatorValue = (value: any): string => {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? 'YES' : 'NO';
  if (typeof value === 'number') return formatIndicatorNumber(value);
  if (Array.isArray(value)) return value.length ? value.join(', ') : '—';
  if (typeof value === 'object') {
    const entries = Object.entries(value).filter(([k]) => k !== 'category');
    if (!entries.length) return '—';
    return entries
      .map(([k, v]) => `${k}: ${typeof v === 'number' ? formatIndicatorNumber(v as number) : v}`)
      .join('   ·   ');
  }
  return String(value);
};

// Maps a registry indicator name (e.g. "rsi_14", "macd_12_26_9") to its
// adjustable-settings type + current default params, matching
// services/indicator_engine.py's CONFIGURABLE_INDICATOR_SCHEMA. Returns
// null for indicators that don't have a configurable counterpart (most
// statistical/price-action/candlestick ones — those are structural reads,
// not period-based).
interface ConfigurableSpec {
  type: string;
  params: Record<string, number>;
}

const getConfigurableSpec = (name: string): ConfigurableSpec | null => {
  let m: RegExpMatchArray | null;
  if ((m = name.match(/^sma_(\d+)$/)))        return { type: 'sma', params: { period: +m[1] } };
  if ((m = name.match(/^ema_(\d+)$/)))        return { type: 'ema', params: { period: +m[1] } };
  if ((m = name.match(/^wma_(\d+)$/)))        return { type: 'wma', params: { period: +m[1] } };
  if ((m = name.match(/^hma_(\d+)$/)))        return { type: 'hma', params: { period: +m[1] } };
  if ((m = name.match(/^rsi_(\d+)$/)))        return { type: 'rsi', params: { period: +m[1] } };
  if ((m = name.match(/^cci_(\d+)$/)))        return { type: 'cci', params: { period: +m[1] } };
  if ((m = name.match(/^williams_r_(\d+)$/))) return { type: 'williams_r', params: { period: +m[1] } };
  if (name === 'atr_14')                      return { type: 'atr', params: { period: 14 } };
  if ((m = name.match(/^macd_(\d+)_(\d+)_(\d+)$/)))
    return { type: 'macd', params: { fast: +m[1], slow: +m[2], signal: +m[3] } };
  if ((m = name.match(/^bollinger_bands_(\d+)_(\d+)$/)))
    return { type: 'bollinger', params: { period: +m[1], stddev: +m[2] } };
  if ((m = name.match(/^stochastic_(\d+)_(\d+)$/)))
    return { type: 'stochastic', params: { k_period: +m[1], d_period: +m[2] } };
  return null;
};

interface CategorySentiment {
  category: string;
  bulls: number;
  bears: number;
  neutrals: number;
  total: number;
}

// Buckets every indicator in a timeframe's payload into per-category
// bullish/bearish/neutral counts, in INDICATOR_CATEGORY_ORDER. Purely for
// the pie/bar chart rendered below the table — doesn't touch or recompute
// any of the actual indicator analysis.
const buildCategorySentimentBreakdown = (allIndicators: Record<string, any>): CategorySentiment[] => {
  return INDICATOR_CATEGORY_ORDER.map(category => {
    let bulls = 0, bears = 0, neutrals = 0;
    Object.values(allIndicators).forEach((v: any) => {
      if (!v?.category) return;
      const normalizedCat = v.category.toLowerCase().trim().replace(/[\s_-]+/g, '_');
      const targetCat = category.toLowerCase().trim().replace(/[\s_-]+/g, '_');
      if (normalizedCat !== targetCat && !(normalizedCat === 'priceaction' && targetCat === 'price_action')) return;
      if (INDICATOR_POSITIVE_SIGNALS.has(v?.signal)) bulls++;
      else if (INDICATOR_NEGATIVE_SIGNALS.has(v?.signal)) bears++;
      else neutrals++;
    });
    return { category, bulls, bears, neutrals, total: bulls + bears + neutrals };
  });
};

interface TabbedPanelProps {
  activeSymbol?: string;
  strategySignal?: StrategySignal | null;
  liveTick?: LiveTick | null;
  // The timeframe currently selected in the Ticker tab's Live Telemetry panel.
  // When provided, the Strategy Workspace is LOCKED to this timeframe: only the matching
  // strategy option stays selectable and all others are disabled, so the strategy shown
  // can never disagree with the timeframe the ticker itself is displaying.
  lockedInterval?: string;
  // Which workspace is showing — now controlled by the bottom nav bar in App.tsx
  // instead of an internal tab strip.
  tab: Tab;
  theme?: string;
  onThemeToggle?: () => void;
  // Setter for the shared ticker timeframe (same value as lockedInterval).
  // Only the Ticker tab actually changes it; every other tab just reads
  // lockedInterval so they all stay in sync with whatever's selected here.
  onIntervalChange?: (interval: string) => void;
}

const TAB_TITLES: Record<Tab, string> = {
  ticker:       'Ticker',
  tradingview:  'Charts',
  angelone:     'SmartAPI Console',
  strategy:     'Strategy Workspace',
  indicators:   'Indicator Engine',
  papertrading: 'Orders',
  pnltracker:   'Ledger'
};

export default function TabbedPanel({ activeSymbol, strategySignal, liveTick, lockedInterval, tab, theme, onThemeToggle, onIntervalChange }: TabbedPanelProps) {
  // ─── 1. REFS DECLARATIONS ──────────────────────────────────────────────────
  const angelRef  = useRef<any>(null);
  const tvRef     = useRef<any>(null);
  const lastToken = useRef<string | null>(null);

  // Tracks whether the user has manually picked a strategy/interval from the
  // dropdown. While true, a stale/late-arriving `strategySignal` prop from
  // the parent (e.g. its own default-interval poll) will NOT be allowed to
  // silently override the user's selection and desync the banner from the
  // dropdown. It resets whenever the active symbol changes.
  const userOverrideRef = useRef<boolean>(false);

  // Tracks the most recently REQUESTED interval (not yet necessarily resolved).
  // Used to discard stale strategy-signal responses that resolve out of order
  // after the user has already switched to a different timeframe.
  const latestRequestedIntervalRef = useRef<string>('1d');

  // ─── 2. STATE DECLARATIONS ─────────────────────────────────────────────────
  const [active, setActive] = useState<ActiveSymbol | null>(null);

  // Ticker tab: search + live broker telemetry (moved out of the profile
  // drawer so the logo/avatar opens a pure Profile tab, and the ticker
  // workspace lives in its own dedicated bottom-nav tab instead).
  const [tickerSearchInput, setTickerSearchInput] = useState('');
  const [tickerQuote, setTickerQuote] = useState<any>(null);
  const [tickerLoadingQuote, setTickerLoadingQuote] = useState(false);
  const [tickerFullData, setTickerFullData] = useState<any>(null);
  const [tickerAnalysisResult, setTickerAnalysisResult] = useState<any>(null);
  const [tickerRunningAnalysis, setTickerRunningAnalysis] = useState(false);
  const [tickerRmsSummary, setTickerRmsSummary] = useState<DailyRMSSummary | null>(null);
  const [tickerLoadingRms, setTickerLoadingRms] = useState(false);
  // Execution ticket state for the Aero-style dashboard grid on the Ticker tab
  const [execOrderType, setExecOrderType] = useState<'LIMIT' | 'MARKET' | 'STOP'>('LIMIT');
  const [execPrice, setExecPrice] = useState('');
  const [execSize, setExecSize] = useState('');

  // Timeframe / Interval State — drives auto-strategy detection on the backend
  const [localStrategySignal, setLocalStrategySignal] = useState<StrategySignal | null>(null);
  const [selectedInterval, setSelectedInterval] = useState<string>('1d'); // default: daily → swing model
  const [strategyLoading, setStrategyLoading] = useState<boolean>(false);

  // Paper Portfolio Engine States
  const [balance, setBalance] = useState<number>(() => {
    const saved = localStorage.getItem('nomisma_paper_balance');
    return saved ? parseFloat(saved) : 10000.00;
  });

  const [positions, setPositions] = useState<Position[]>(() => {
    const saved = localStorage.getItem('nomisma_paper_positions');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed)) {
          return parsed.map((p: any) => ({
            ...p,
            id: p.id || generateRefId()
          }));
        }
      } catch (e) {
        console.error("[POSITION CACHE] Parse error: ", e);
      }
    }
    return [];
  });

  const [orders, setOrders] = useState<OrderHistoryItem[]>(() => {
    const saved = localStorage.getItem('nomisma_paper_orders');
    return saved ? JSON.parse(saved) : [];
  });

  const [realizedTrades, setRealizedTrades] = useState<RealizedTrade[]>(() => {
    try {
      const saved = localStorage.getItem('nomisma_paper_realized');
      if (saved) {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed)) {
          const cleaned = parsed.filter((t: any) => !t.symbol.startsWith('NIFTY'));
          return cleaned.map((t: any) => ({
            ...t,
            id: t.id || generateRefId()
          }));
        }
      }
    } catch (e) {
      console.error("[LEDGER CACHE] Parse error: ", e);
    }
    return [] as RealizedTrade[];
  });

  // ─── Autonomous Execution State (drives the EXISTING paper portfolio ──────
  // engine below via executeCoreOrder/handleSquareOff — it places trades
  // into the SAME balance/positions/orders the Orders tab already shows).
  // No dedicated UI panel — this runs silently. There's still a code-level
  // kill switch: run `localStorage.setItem('nomisma_autotrade_enabled','false')`
  // in devtools and reload if you ever need to stop it without a redeploy.
  //
  // ENTRY requires BOTH panels to agree on direction:
  //   1. Strategy Workspace's own signal  (localStrategySignal.signal)
  //   2. Indicator panel's aggregate read (bulls vs bears across the same
  //      indicator sweep the Indicators tab shows, for the SAME interval
  //      the strategy is running on -- fetched in the background below,
  //      independent of which tab is actually open)
  // No confidence threshold anywhere in this.
  //
  // EXIT is strategy-driven: the position is squared off the moment the
  // live strategy signal for this symbol no longer agrees with the
  // direction it was entered in (per your instruction: "cutoff when
  // strategy tell to do so"). A flat hard stop-loss and an end-of-day
  // square-off are kept underneath that as a safety net, not as the
  // primary exit trigger -- flagged clearly, remove AUTO_TRADE_HARD_SL_PCT's
  // effect below if you want the strategy signal to be the ONLY exit path.
  // HARD KILL SWITCH — autotrade is force-disabled at the code level and
  // does NOT read localStorage anymore. A stale 'nomisma_autotrade_enabled'
  // = 'true' value left over from before was silently re-arming it even
  // after the default was flipped, which is why trades kept firing.
  // To re-enable, change this constant back to a localStorage-driven check.
  const AUTOTRADE_HARD_DISABLED = true;
  const [autoTradeEnabled] = useState<boolean>(() => {
    if (AUTOTRADE_HARD_DISABLED) return false;
    const saved = localStorage.getItem('nomisma_autotrade_enabled');
    return saved === null ? false : saved === 'true';
  });
  const [autoTradeQty] = useState<number>(() => parseInt(localStorage.getItem('nomisma_autotrade_qty') || '1', 10));
  const [autoTradeLotSize] = useState<number>(() => parseInt(localStorage.getItem('nomisma_autotrade_lot') || '1', 10));
  const [autoTradeLeverage] = useState<number>(() => parseInt(localStorage.getItem('nomisma_autotrade_lev') || '1', 10));
  // The loss limit: position is held through normal signal noise and stays
  // open until price moves against it by this %, no matter what. This is
  // now the PRIMARY exit trigger (see the exit effect below) -- an earlier
  // version exited the instant the strategy signal itself changed, which
  // re-polls every 20s and can flicker, causing enter/exit thrash within
  // the same minute. That's gone now.
  const [autoTradeLossLimitPct] = useState<number>(() => parseFloat(localStorage.getItem('nomisma_autotrade_loss_limit') || '0.015'));
  // The profit target: position is squared off once it's UP by this % --
  // the other half of "hold until a real limit is hit", not signal noise.
  const [autoTradeProfitTargetPct] = useState<number>(() => parseFloat(localStorage.getItem('nomisma_autotrade_profit_target') || '0.03'));
  const [autoTradeSquareOffTime] = useState<string>(() => localStorage.getItem('nomisma_autotrade_sqoff') || '15:15');
  // Minimum time after an exit before autotrade will consider re-entering
  // the SAME symbol -- a hard backstop against rapid-fire thrash loops
  // regardless of what's driving them.
  const AUTO_TRADE_REENTRY_COOLDOWN_MS = 5 * 60 * 1000; // 5 min
  const lastExitTimeRef = useRef<Record<string, number>>({});
  // Keyed by symbol -- one autotrade-managed position per symbol at a time.
  const [autoTradeMeta, setAutoTradeMeta] = useState<Record<string, { lossLimitPrice: number; profitTargetPrice: number; entryTime: number; side: 'LONG' | 'SHORT' }>>(() => {
    try {
      const saved = localStorage.getItem('nomisma_autotrade_meta');
      return saved ? JSON.parse(saved) : {};
    } catch {
      return {};
    }
  });
  // Background-fetched indicator sweep, keyed by interval, used ONLY to
  // decide autotrade agreement -- independent of the Indicators tab's own
  // indicatorsData/indicatorInterval so it keeps working even if that tab
  // is never opened.
  const [autoIndicatorSweep, setAutoIndicatorSweep] = useState<Record<string, any>>({});
  // Prevents re-firing an entry every poll while the signal direction
  // hasn't changed for a symbol we're already flat on.
  const lastHandledSignalRef = useRef<Record<string, string>>({});

  // UI Toast State
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  // Router Form States
  const [tradeSymbol, setTradeSymbol] = useState('');
  const [orderQty, setOrderQty] = useState<number>(1);
  const [lotMultiplier, setLotMultiplier] = useState<number>(1); 
  const [leverage, setLeverage] = useState<number>(1); 
  const [orderPrice, setOrderPrice] = useState<string>('');
  const [orderType, setOrderType] = useState<'MARKET' | 'LIMIT'>('MARKET');
  const [orderSide, setOrderSide] = useState<'BUY' | 'SELL'>('BUY');
  const [topupAmount, setTopupAmount] = useState<string>('5000');

  // Quotes Fallback State
  const [restPrice, setRestPrice] = useState<number | null>(null);

  // Strategy Analysis States
  const [sentimentData, setSentimentData] = useState<SentimentData | null>(null);
  const [sentimentLoading, setSentimentLoading] = useState<boolean>(false);

  // Indicator Engine States — own tab, own fetch, does not touch Strategy's state
  const [indicatorsData, setIndicatorsData] = useState<any>(null);
  const [indicatorsLoading, setIndicatorsLoading] = useState<boolean>(false);
  const [indicatorsError, setIndicatorsError] = useState<string>('');
  // Exact Ticker timeframe for the Indicators tab (e.g. '5m', '75m', '4h',
  // '1mo') — every one of the 20 Ticker intervals is its own separately
  // selectable/lockable value now, not just one of 3 buckets. See the
  // lock-sync effect near fetchStrategySignalOverride's for the Strategy tab
  // for the identical exact-match locking pattern applied to this state.
  const [indicatorInterval, setIndicatorInterval] = useState<string>('1d');
  const [indicatorCategory, setIndicatorCategory] = useState<string>('trend');

  // Per-indicator adjustable settings — keyed by indicator registry name
  // (e.g. 'rsi_14'). Holds the currently-open settings panel, the params
  // being edited, the last applied custom result, and its own loading flag.
  const [openSettingsFor, setOpenSettingsFor] = useState<string | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<Record<string, any>>({});
  const [customIndicatorResults, setCustomIndicatorResults] = useState<Record<string, any>>({});
  const [customIndicatorLoading, setCustomIndicatorLoading] = useState<Record<string, boolean>>({});

  // Calculates total unrealized PnL across all open positions
  const calculateOpenPnL = useCallback(() => {
    return positions.reduce((acc, pos) => {
      const isLong = pos.type === 'LONG';
      const points = isLong
        ? pos.currentPrice - pos.entryPrice
        : pos.entryPrice - pos.currentPrice;
      return acc + points * pos.qty * pos.lotSize;
    }, 0);
  }, [positions]);

  // ─── 3. RESILIENT CALLBACK DECLARATIONS ────────────────────────────────────

  // Sliding dynamic toast notifications handler
  const triggerToast = useCallback((text: string, error: boolean) => {
    const id = Math.random().toString(36).slice(2, 9);
    setToasts(prev => [...prev, { id, text, error }]);
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 4000);
  }, []);

  // Fetch strategy signal for a given interval — backend auto-maps interval → strategy model
  // Resiliently includes the active 'tradeSymbol' in the query to handle hot-reloads
  const fetchStrategySignalOverride = useCallback(async (intervalStr: string) => {
    setStrategyLoading(true);
    latestRequestedIntervalRef.current = intervalStr;
    try {
      const symbolParam = tradeSymbol ? `&symbol=${encodeURIComponent(tradeSymbol)}` : '';
      const response = await fetch(`${BACKEND}/api/active/strategy-signal?interval=${intervalStr}${symbolParam}`);
      if (response.ok) {
        const data = await response.json();
        // Guard against a slower, older request for a timeframe the user has since
        // moved away from landing AFTER a newer one and silently showing mismatched
        // (wrong-timeframe) analysis under the currently-selected dropdown value.
        if (intervalStr !== latestRequestedIntervalRef.current) {
          return;
        }
        // Extra safety: only accept payloads that actually confirm the timeframe
        // they were computed for matches what was requested.
        if (data?.interval && data.interval !== intervalStr) {
          return;
        }
        setLocalStrategySignal(data);
      }
    } catch (err) {
      console.warn("[STRATEGY FETCH ERROR]", err);
    } finally {
      if (intervalStr === latestRequestedIntervalRef.current) {
        setStrategyLoading(false);
      }
    }
  }, [tradeSymbol]);

  // Interval change handler — auto-routes to the right model.
  // Marks this as a user-driven override so incoming parent `strategySignal`
  // prop updates (which may reflect a different, stale interval) don't clobber it.
  const handleIntervalChange = useCallback((intervalStr: string) => {
    userOverrideRef.current = true;
    setSelectedInterval(intervalStr);
    fetchStrategySignalOverride(intervalStr);
  }, [fetchStrategySignalOverride]);

  // Paper portfolio execution core flow
  const executeCoreOrder = useCallback(async (params: {
    symbol: string;
    side: 'BUY' | 'SELL';
    price: number;
    qty: number;
    lotSize: number;
    levValue: number;
    oType: 'MARKET' | 'LIMIT';
  }) => {
    const { symbol, side, price, qty, lotSize, levValue, oType } = params;
    const totalExposure = price * qty * lotSize;
    const requiredMargin = totalExposure / levValue;

    if (side === 'BUY') {
      if (balance < requiredMargin) {
        setOrders(prev => [{
          timestamp: Date.now(),
          symbol,
          type: 'BUY',
          qty,
          lotSize,
          price,
          orderType: oType,
          status: 'REJECTED',
          rejectionReason: 'MARGIN LIMIT EXCEEDED'
        }, ...prev]);
        triggerToast(`Execution Failed: Margin of $${requiredMargin.toFixed(2)} exceeds available capital.`, true);
        return;
      }

      setBalance(prev => prev - requiredMargin);
      setPositions(prev => {
        const existingIdx = prev.findIndex(p => p.symbol === symbol && p.type === 'LONG');
        if (existingIdx > -1) {
          const updated = [...prev];
          const currentPos = updated[existingIdx];
          const combinedQty = currentPos.qty + qty;
          const weightedEntry = ((currentPos.qty * currentPos.entryPrice) + (qty * price)) / combinedQty;
          
          updated[existingIdx] = {
            ...currentPos,
            qty: combinedQty,
            entryPrice: weightedEntry,
            currentPrice: price
          };
          return updated;
        } else {
          return [...prev, {
            id: generateRefId(),
            symbol,
            qty,
            lotSize,
            entryPrice: price,
            currentPrice: price,
            type: 'LONG',
            leverage: levValue,
            timestamp: Date.now()
          }];
        }
      });

      setOrders(prev => [{
        timestamp: Date.now(),
        symbol,
        type: 'BUY',
        qty,
        lotSize,
        price,
        orderType: oType,
        status: 'FILLED'
      }, ...prev]);

      triggerToast(`Filled BUY: ${qty} Lots of ${symbol} executed at $${price.toFixed(2)}.`, false);
    } else {
      const longIdx = positions.findIndex(p => p.symbol === symbol && p.type === 'LONG');

      if (longIdx > -1) {
        const currentLong = positions[longIdx];
        if (currentLong.qty < qty) {
          const excessQty = qty - currentLong.qty;
          const refundedMargin = (currentLong.qty * currentLong.entryPrice * currentLong.lotSize) / currentLong.leverage;
          const excessMarginReq = (excessQty * price * lotSize) / levValue;

          if (balance + refundedMargin < excessMarginReq) {
            triggerToast(`Short Flip Rejected: Short margin of $${excessMarginReq.toFixed(2)} exceeds balance.`, true);
            return;
          }

          setBalance(prev => prev + refundedMargin - excessMarginReq);
          setPositions(prev => {
            const filtered = prev.filter((_, i) => i !== longIdx);
            return [...filtered, {
              id: generateRefId(),
              symbol,
              qty: excessQty,
              lotSize,
              entryPrice: price,
              currentPrice: price,
              type: 'SHORT',
              leverage: levValue,
              timestamp: Date.now()
            }];
          });
        } else if (currentLong.qty === qty) {
          const refundedMargin = (qty * currentLong.entryPrice * currentLong.lotSize) / currentLong.leverage;
          const pointsGained = price - currentLong.entryPrice;
          const profitAndLoss = pointsGained * qty * currentLong.lotSize;
          
          setBalance(prev => prev + refundedMargin + profitAndLoss);
          setPositions(prev => prev.filter((_, i) => i !== longIdx));

          setRealizedTrades(prev => [
            {
              id: currentLong.id,
              timestamp: Date.now(),
              symbol,
              entryPrice: currentLong.entryPrice,
              exitPrice: price,
              qty: qty,
              lotSize: currentLong.lotSize,
              type: 'LONG',
              points: parseFloat(pointsGained.toFixed(2)),
              pnl: parseFloat(profitAndLoss.toFixed(2)),
              status: 'CLOSED'
            },
            ...prev
          ]);
        } else {
          const scaledRatio = qty / currentLong.qty;
          const proportionalMargin = ((currentLong.qty * currentLong.entryPrice * currentLong.lotSize) / currentLong.leverage) * scaledRatio;
          const pointsGained = price - currentLong.entryPrice;
          const profitAndLoss = pointsGained * qty * currentLong.lotSize;
          
          setBalance(prev => prev + proportionalMargin + profitAndLoss);
          setPositions(prev => {
            const updated = [...prev];
            updated[longIdx] = {
              ...currentLong,
              qty: currentLong.qty - qty
            };
            return updated;
          });

          setRealizedTrades(prev => [
            {
              id: currentLong.id,
              timestamp: Date.now(),
              symbol,
              entryPrice: currentLong.entryPrice,
              exitPrice: price,
              qty: qty,
              lotSize: currentLong.lotSize,
              type: 'LONG',
              points: parseFloat(pointsGained.toFixed(2)),
              pnl: parseFloat(profitAndLoss.toFixed(2)),
              status: 'CLOSED'
            },
            ...prev
          ]);
        }
      } else {
        if (balance < requiredMargin) {
          setOrders(prev => [{
            timestamp: Date.now(),
            symbol,
            type: 'SELL',
            qty,
            lotSize,
            price,
            orderType: oType,
            status: 'REJECTED',
            rejectionReason: 'MARGIN LIMIT EXCEEDED'
          }, ...prev]);
          triggerToast(`Short Execution Rejected: Requires $${requiredMargin.toFixed(2)} margin allocation.`, true);
          return;
        }

        setBalance(prev => prev - requiredMargin);
        setPositions(prev => {
          const existingShortIdx = prev.findIndex(p => p.symbol === symbol && p.type === 'SHORT');
          if (existingShortIdx > -1) {
            const updated = [...prev];
            const currentShort = updated[existingShortIdx];
            const combinedQty = currentShort.qty + qty;
            const weightedEntry = ((currentShort.qty * currentShort.entryPrice) + (qty * price)) / combinedQty;
            
            updated[existingShortIdx] = {
              ...currentShort,
              qty: combinedQty,
              entryPrice: weightedEntry,
              currentPrice: price
            };
            return updated;
          } else {
            return [...prev, {
              id: generateRefId(),
              symbol,
              qty,
              lotSize,
              entryPrice: price,
              currentPrice: price,
              type: 'SHORT',
              leverage: levValue,
              timestamp: Date.now()
            }];
          }
        });
      }

      setOrders(prev => [{
        timestamp: Date.now(),
        symbol,
        type: 'SELL',
        qty,
        lotSize,
        price,
        orderType: oType,
        status: 'FILLED'
      }, ...prev]);

      triggerToast(`Filled SELL: ${qty} Lots of ${symbol} executed at $${price.toFixed(2)}.`, false);
    }
  }, [balance, positions, triggerToast]);

  const onSymbolChange = useCallback(async (data: ActiveSymbol) => {
    if (!data.token || data.token === lastToken.current) return;
    lastToken.current = data.token;
    setActive(data);

    fetch(`${BACKEND}/api/active-symbol`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }).catch(err => console.error('[TABBED-PANEL] Backend post failed:', err));

    const cleanTicker = data.symbol.replace(/-EQ$/i, '').replace(/-BE$/i);
    const tvSymbol = `${data.exchange}:${cleanTicker}`;
    window.dispatchEvent(new CustomEvent('tv-symbol-change', { detail: tvSymbol }));

    tvRef.current?.loadURL(
      `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tvSymbol)}`
    );
  }, []);

  const handleTopup = (e: React.FormEvent) => {
    e.preventDefault();
    const amount = parseFloat(topupAmount);
    if (isNaN(amount) || amount <= 0) {
      triggerToast("Top-up validation rejected. Input valid amount.", true);
      return;
    }
    const targetBalance = balance + amount;
    if (targetBalance > 100000.00) {
      triggerToast("System Limit: Maximum top-up balance capped at $100,000.", true);
      return;
    }
    setBalance(targetBalance);
    triggerToast("Account Balance successfully topped up.", false);
  };

  const handleExecuteTradeForm = async (e: React.FormEvent) => {
    e.preventDefault();

    const targetSymbol = tradeSymbol.trim().toUpperCase();
    if (!targetSymbol) {
      triggerToast("Execution rejected. Target ticker asset missing.", true);
      return;
    }

    let tradePrice = parseFloat(orderPrice);
    if (orderType === 'MARKET') {
      try {
        const quote = await api.quote(targetSymbol);
        if (quote && quote.currentPrice) {
          tradePrice = quote.currentPrice;
        } else {
          triggerToast("Real-time pricing feed missing. Select Limit order fallback.", true);
          return;
        }
      } catch {
        triggerToast("Price synchronization error. Order rejected.", true);
        return;
      }
    } else {
      if (isNaN(tradePrice) || tradePrice <= 0) {
        triggerToast("Specify dynamic limit target values.", true);
        return;
      }
    }

    await executeCoreOrder({
      symbol: targetSymbol,
      side: orderSide,
      price: tradePrice,
      qty: orderQty,
      lotSize: lotMultiplier,
      levValue: leverage,
      oType: orderType
    });
  };

  const handleSquareOff = (pos: Position) => {
    const originalMarginValue = (pos.qty * pos.entryPrice * pos.lotSize) / pos.leverage;
    let profitAndLoss = 0;
    let pointsGained = 0;

    if (pos.type === 'LONG') {
      pointsGained = pos.currentPrice - pos.entryPrice;
    } else {
      pointsGained = pos.entryPrice - pos.currentPrice;
    }
    profitAndLoss = pointsGained * pos.qty * pos.lotSize;

    setBalance(prev => prev + originalMarginValue + profitAndLoss);
    setPositions(prev => prev.filter(p => !(p.symbol === pos.symbol && p.type === pos.type)));
    
    setOrders(prev => [{
      timestamp: Date.now(),
      symbol: pos.symbol,
      type: pos.type === 'LONG' ? 'SELL' : 'BUY',
      qty: pos.qty,
      lotSize: pos.lotSize,
      price: pos.currentPrice,
      orderType: 'MARKET',
      status: 'FILLED'
    }, ...prev]);

    setRealizedTrades(prev => [
      {
        id: pos.id,
        timestamp: Date.now(),
        symbol: pos.symbol,
        entryPrice: pos.entryPrice,
        exitPrice: pos.currentPrice,
        qty: pos.qty,
        lotSize: pos.lotSize,
        type: pos.type,
        points: parseFloat(pointsGained.toFixed(2)),
        pnl: parseFloat(profitAndLoss.toFixed(2)),
        status: 'CLOSED'
      },
      ...prev
    ]);

    triggerToast(`Squared Off: Realized profit/loss closed out at $${profitAndLoss.toFixed(2)}`, false);
  };

  // Refs so intervals/effects below always read the LATEST values without
  // needing them in a dependency array (matches the existing "High-frequency
  // telemetry sync" effect's pattern of not re-subscribing on every tick).
  const positionsRef = useRef<Position[]>(positions);
  useEffect(() => { positionsRef.current = positions; }, [positions]);
  const liveTickRef = useRef(liveTick);
  useEffect(() => { liveTickRef.current = liveTick; }, [liveTick]);
  const activeRef = useRef(active);
  useEffect(() => { activeRef.current = active; }, [active]);
  const tradeSymbolRef = useRef(tradeSymbol);
  useEffect(() => { tradeSymbolRef.current = tradeSymbol; }, [tradeSymbol]);
  const handleSquareOffRef = useRef(handleSquareOff);
  useEffect(() => { handleSquareOffRef.current = handleSquareOff; });

  // ─── AUTONOMOUS EXECUTION: dedicated strategy-signal engine ───────────────
  // App.tsx's `strategySignal` prop (→ localStrategySignal) is deliberately
  // throttled to a 3-min poll to stay under a 20-req/day Gemini free-tier
  // cap on that endpoint's narrative generation (see App.tsx's
  // STRATEGY_SIGNAL_POLL_MS comment) -- that throttle is right for the UI,
  // but too slow for autotrade to react to a changing market.
  //
  // This is a SEPARATE engine: its own state, its own interval, calling the
  // same GET /active/strategy-signal endpoint independently of App.tsx's
  // poll -- so speeding this up never touches the UI's cadence or the
  // "Portfolio Decision Brief" card's polling at all.
  //
  // IMPORTANT CAVEAT I can't verify from the frontend alone: if
  // /active/strategy-signal still triggers a Gemini call server-side per
  // request (routers/stocks.py isn't something I have visibility into),
  // running a second, faster poller against the SAME endpoint adds to the
  // SAME daily quota rather than avoiding it -- the two pollers don't share
  // a budget, they both spend from it. If you hit quota errors, either
  // raise AUTO_STRATEGY_POLL_MS below, or (better) add a
  // `?narrative=false`-style flag on the backend so this engine's calls
  // skip Gemini entirely and only the UI's poll pays for the narrative.
  const [autoStrategySignal, setAutoStrategySignal] = useState<StrategySignal | null>(null);
  useEffect(() => {
    if (!autoTradeEnabled || !tradeSymbol) return;

    let cancelled = false;
    const AUTO_STRATEGY_POLL_MS = 20000; // 20s -- independent of App.tsx's 3-min UI poll

    const poll = async () => {
      try {
        const signal = await api.strategySignal(undefined, selectedInterval);
        if (!cancelled) setAutoStrategySignal(signal);
      } catch (e) {
        console.warn('[AUTOTRADE] dedicated strategy-signal poll failed:', e);
      }
    };

    poll();
    const id = setInterval(poll, AUTO_STRATEGY_POLL_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, [autoTradeEnabled, tradeSymbol, selectedInterval]);

  // ─── AUTONOMOUS EXECUTION: background indicator sweep ─────────────────────
  // Keeps an indicator sweep loaded for the SAME interval the Strategy
  // Workspace is running on, independent of whether the Indicators tab is
  // even open -- this is what lets entry-agreement checking run purely in
  // the background. Reuses the same GET /api/active/indicators/by-interval
  // endpoint the Indicators tab calls, cached separately so it never
  // clobbers whatever timeframe you're manually browsing there.
  useEffect(() => {
    if (!autoTradeEnabled || !tradeSymbol) return;

    let cancelled = false;
    const poll = async (force: boolean) => {
      try {
        const params = new URLSearchParams({ symbol: tradeSymbol, interval: selectedInterval });
        const res = await fetch(`${BACKEND}/api/active/indicators/by-interval?${params.toString()}`);
        if (!res.ok || cancelled) return;
        const data = await res.json();
        if (!cancelled) {
          setAutoIndicatorSweep(prev => ({ ...prev, [selectedInterval]: data }));
        }
      } catch (e) {
        console.warn('[AUTOTRADE] background indicator sweep fetch failed:', e);
      }
    };

    poll(false);
    const id = setInterval(() => poll(true), 60000); // refresh every 60s to stay live
    return () => { cancelled = true; clearInterval(id); };
  }, [autoTradeEnabled, tradeSymbol, selectedInterval]);

  // ─── AUTONOMOUS EXECUTION: entry ──────────────────────────────────────────
  // Fires when BOTH decision sources agree on direction:
  //   1. Strategy engine's signal          (autoStrategySignal.signal --
  //      the dedicated 20s-poll engine above, NOT the UI's throttled one)
  //   2. Indicator panel's aggregate read  (bulls vs bears, same interval)
  //
  // The backend's arbitration layer (data integrity, regime, timeframe
  // alignment) now feeds SIZING, not a hard block. An earlier version
  // required data_integrity.complete AND timeframe_alignment>=50% AND
  // regime-not-CONFLICTED AND panel-agreement to ALL pass simultaneously --
  // that's five independent conditions ANDed together, which in practice
  // almost never all held true at once, so nothing ever fired. A real desk
  // doesn't halt trading because one input is imperfect -- it trusts the
  // signal less and sizes smaller. Only two things still hard-block:
  //   - CONFLICTED regime (the strategy itself says its inputs are fighting
  //     each other -- that's not "lower confidence", that's "don't know")
  //   - the two panels actually disagreeing on direction
  // Everything else (incomplete data, weak timeframe alignment, low
  // liquidity) discounts positionSizeMultiplier instead of blocking.
  useEffect(() => {
    if (!autoTradeEnabled) return;
    const symbol = tradeSymbol.trim().toUpperCase();
    if (!symbol) return;

    // Ticker must be live -- need an actual tradeable price.
    const price = (liveTick && active && liveTick.token === active.token) ? liveTick.ltp : restPrice;

    const signalObj = autoStrategySignal as any;
    const strategyUpper = (signalObj?.signal || 'HOLD').toUpperCase();
    const arb = signalObj?.arbitration;

    // Full diagnostic snapshot every cycle, pass or fail, so you can watch
    // exactly what it's seeing without guessing from screenshots.
    console.log(`[AUTOTRADE] ${symbol || '(no symbol)'} check: price=${price ?? 'none'} strategy=${strategyUpper} regime=${arb?.regime ?? 'n/a'} dataComplete=${arb?.data_integrity?.complete ?? 'n/a'} tfAlign=${arb?.timeframe_alignment?.alignment_pct ?? 'n/a'}% sizeMult=${arb?.position_size_multiplier ?? 'n/a'} holding=${!!autoTradeMeta[symbol]}`);

    if (!price) { console.log(`[AUTOTRADE] ${symbol}: standing down -- no live price yet.`); return; }

    if (strategyUpper !== 'BUY' && strategyUpper !== 'SELL' && strategyUpper !== 'SHORT') {
      lastHandledSignalRef.current[symbol] = strategyUpper;
      console.log(`[AUTOTRADE] ${symbol}: standing down -- strategy signal is ${strategyUpper}, not directional.`);
      return;
    }

    // Already holding an autotrade-managed position on this symbol -- exits
    // are owned entirely by the watcher effect below; entries don't stack.
    if (autoTradeMeta[symbol]) return;

    // Hard backstop against thrash loops: don't even consider a fresh entry
    // until the cooldown since the last exit on this symbol has elapsed.
    const lastExit = lastExitTimeRef.current[symbol];
    if (lastExit && Date.now() - lastExit < AUTO_TRADE_REENTRY_COOLDOWN_MS) {
      console.log(`[AUTOTRADE] ${symbol}: standing down -- re-entry cooldown active (${Math.ceil((AUTO_TRADE_REENTRY_COOLDOWN_MS - (Date.now() - lastExit)) / 1000)}s left).`);
      return;
    }

    // Only re-enter once the strategy direction has actually CHANGED since
    // the last time we acted on it for this symbol.
    if (lastHandledSignalRef.current[symbol] === strategyUpper) {
      console.log(`[AUTOTRADE] ${symbol}: standing down -- already acted on ${strategyUpper}, waiting for a direction change.`);
      return;
    }

    const strategyDir: 'BUY' | 'SELL' = strategyUpper === 'BUY' ? 'BUY' : 'SELL';

    // ── Conviction scaling from the backend's arbitration layer ──
    // Starts at 1.0 (full base size) and gets discounted -- never hard-
    // blocked -- by imperfect inputs. Only CONFLICTED regime is a real
    // stop, because that specifically means the strategy's own inputs are
    // contradicting each other, not just "less confident than usual".
    let positionSizeMultiplier = 1;
    if (arb) {
      if (arb.regime === 'CONFLICTED') {
        console.log(`[AUTOTRADE] ${symbol}: standing down -- regime is CONFLICTED (strategy's own inputs are contradicting each other).`);
        return;
      }
      if (arb.data_integrity && arb.data_integrity.complete === false) {
        const ratio = typeof arb.data_integrity.completeness_ratio === 'number' ? arb.data_integrity.completeness_ratio : 0.5;
        positionSizeMultiplier *= Math.max(0.1, ratio);
      }
      if (arb.regime === 'LOW_LIQUIDITY_DATA') {
        positionSizeMultiplier *= 0.5;
      }
      if (arb.timeframe_alignment && typeof arb.timeframe_alignment.alignment_pct === 'number') {
        // Scale continuously instead of a hard >=50% cutoff -- e.g. 30%
        // alignment sizes the trade down hard rather than refusing it.
        positionSizeMultiplier *= Math.max(0.2, arb.timeframe_alignment.alignment_pct / 100);
      }
      if (typeof arb.position_size_multiplier === 'number' && arb.position_size_multiplier > 0) {
        positionSizeMultiplier *= arb.position_size_multiplier;
      }
    }

    // Second panel: indicator sweep verdict for the SAME interval the
    // strategy is running on -- bulls vs bears across every indicator.
    const sweep = autoIndicatorSweep?.[selectedInterval];
    const allInd: Record<string, any> = sweep?.indicators || {};
    const breakdown = buildCategorySentimentBreakdown(allInd);
    const totalBulls = breakdown.reduce((a, c) => a + c.bulls, 0);
    const totalBears = breakdown.reduce((a, c) => a + c.bears, 0);
    const indicatorUpper = totalBulls > totalBears ? 'BUY' : totalBears > totalBulls ? 'SELL' : 'HOLD';

    // The two panels must match -- otherwise stand down and wait for the
    // next signal change (don't mark this as "handled" so it re-checks
    // as soon as either panel's data updates).
    if (indicatorUpper !== strategyDir) {
      console.log(`[AUTOTRADE] ${symbol}: Strategy says ${strategyDir}, Indicators say ${indicatorUpper} (${totalBulls} bulls vs ${totalBears} bears) -- no agreement, standing down.`);
      return;
    }

    // ── Sizing: vote-weighted by the indicator panel's own bull/bear count,
    // combined with the discounted arbitration multiplier above.
    const directionalTotal = totalBulls + totalBears;
    if (directionalTotal === 0) {
      console.log(`[AUTOTRADE] ${symbol}: standing down -- no directional indicators evaluated (all neutral).`);
      return;
    }
    const indicatorConviction = strategyDir === 'BUY' ? totalBulls / directionalTotal : totalBears / directionalTotal;
    const combinedMultiplier = indicatorConviction * positionSizeMultiplier;
    // Once both panels agree, floor to a minimum of 1 share rather than
    // mathematically rounding a genuine, agreed-upon signal down to a
    // no-op trade -- round (not floor) above that so stronger conviction
    // still sizes up on a larger base qty.
    const sizedQty = Math.max(1, Math.round(autoTradeQty * combinedMultiplier));

    lastHandledSignalRef.current[symbol] = strategyUpper;

    executeCoreOrder({
      symbol,
      side: strategyDir,
      price,
      qty: sizedQty,
      lotSize: autoTradeLotSize,
      levValue: autoTradeLeverage,
      oType: 'MARKET'
    }).then(() => {
      const side = strategyDir === 'BUY' ? 'LONG' : 'SHORT';
      const lossLimitPrice = side === 'LONG' ? price * (1 - autoTradeLossLimitPct) : price * (1 + autoTradeLossLimitPct);
      const profitTargetPrice = side === 'LONG' ? price * (1 + autoTradeProfitTargetPct) : price * (1 - autoTradeProfitTargetPct);
      setAutoTradeMeta(prev => ({ ...prev, [symbol]: { lossLimitPrice, profitTargetPrice, entryTime: Date.now(), side } }));
      console.log(`[AUTOTRADE] ENTERED ${strategyDir} ${sizedQty}×${autoTradeLotSize} ${symbol} @ ₹${price.toFixed(2)} (indicator vote ${totalBulls}v${totalBears} = ${(indicatorConviction * 100).toFixed(0)}% × arbitration-adjusted ${(positionSizeMultiplier * 100).toFixed(0)}% = ${(combinedMultiplier * 100).toFixed(0)}% of base ${autoTradeQty}).`);
    });
  }, [autoTradeEnabled, autoStrategySignal, tradeSymbol, autoTradeMeta, autoTradeQty, autoTradeLotSize, autoTradeLeverage, autoTradeLossLimitPct, autoTradeProfitTargetPct, liveTick, active, restPrice, executeCoreOrder, autoIndicatorSweep, selectedInterval]);

  // ─── AUTONOMOUS EXECUTION: exit ────────────────────────────────────────────
  // Exit triggers, in priority order:
  //   1. Loss limit hit   -- price moved against the position by
  //      autoTradeLossLimitPct. This is the floor: the position is held
  //      through ordinary signal noise until this is actually breached.
  //   2. Profit target hit -- price moved in favor by autoTradeProfitTargetPct.
  //   3. End-of-day square-off time.
  // Signal-driven exit was REMOVED: it re-checked every 20s and any
  // momentary flicker in the strategy signal closed the position instantly,
  // which is what caused the enter/exit thrash loop (repeated trades within
  // the same minute, each a small loss). Loss-limit/profit-target are the
  // only things that close a position now, besides the EOD safety net.
  useEffect(() => {
    if (Object.keys(autoTradeMeta).length === 0) return;

    const watcher = setInterval(() => {
      const [hh, mm] = autoTradeSquareOffTime.split(':').map(Number);
      const now = new Date();
      const pastSquareOff = now.getHours() > hh || (now.getHours() === hh && now.getMinutes() >= mm);

      Object.entries(autoTradeMeta).forEach(([symbol, meta]) => {
        const pos = positionsRef.current.find(p => p.symbol === symbol && p.type === meta.side);
        if (!pos) {
          // Closed some other way already (manual square-off etc.) -- drop stale metadata.
          setAutoTradeMeta(prev => {
            const next = { ...prev };
            delete next[symbol];
            return next;
          });
          return;
        }

        // Prefer the SAME feed the entry price came from (the live
        // WebSocket tick) for this decision. pos.currentPrice is
        // maintained by a completely separate 6s REST poll (api.quote)
        // plus a small synthetic random-walk simulator -- comparing THAT
        // against a limit set from the live tick compares two different
        // data sources, and any steady-state offset between them can trip
        // a limit that the real live price never actually reached.
        const isLiveSymbol = liveTickRef.current && activeRef.current && liveTickRef.current.token === activeRef.current.token && tradeSymbolRef.current.trim().toUpperCase() === symbol;
        const decisionPrice = isLiveSymbol ? liveTickRef.current!.ltp : pos.currentPrice;

        const lossLimitHit = meta.side === 'LONG' ? decisionPrice <= meta.lossLimitPrice : decisionPrice >= meta.lossLimitPrice;
        const profitTargetHit = meta.side === 'LONG' ? decisionPrice >= meta.profitTargetPrice : decisionPrice <= meta.profitTargetPrice;

        let reason: string | null = null;
        if (pastSquareOff) {
          reason = 'square-off time';
        } else if (lossLimitHit) {
          reason = 'loss limit';
        } else if (profitTargetHit) {
          reason = 'profit target';
        }

        if (reason) {
          handleSquareOffRef.current(pos);
          setAutoTradeMeta(prev => {
            const next = { ...prev };
            delete next[symbol];
            return next;
          });
          // Clear the re-entry guard so a future signal change CAN trigger
          // a fresh entry -- but the cooldown timestamp below still makes
          // AUTO_TRADE_REENTRY_COOLDOWN_MS elapse first, so this alone
          // can't cause another instant re-entry.
          delete lastHandledSignalRef.current[symbol];
          lastExitTimeRef.current[symbol] = Date.now();
          console.log(`[AUTOTRADE] Squared off ${symbol} @ ₹${pos.currentPrice.toFixed(2)} (${reason})`);
        }
      });
    }, 2000);

    return () => clearInterval(watcher);
  }, [autoTradeMeta, autoTradeSquareOffTime]);

  // ─── 4. EVALUATED MEMO DECLARATIONS ────────────────────────────────────────

  // ─── Local "Agentic" Reasoning Engine ──────────────────────────────────────
  // Synthesizes the SAME kind of narrative Gemini used to write, entirely
  // client-side from the numbers we already have (EMA/MACD/RSI/Bollinger,
  // trend, sentiment score, per-model confidence). Zero API calls, zero
  // rate limits, zero cost, instant — this is what now powers the
  // "Portfolio Decision Brief" card instead of the Gemini call.
  const strategyExplanationText = useMemo(() => {
    const signalObj = localStrategySignal as any;
    if (!signalObj) return "Querying strategy indicators...";
    const run = signalObj.detailed_runs;
    if (!run) return "Awaiting granular mathematical metrics from backend engine...";

    const activeHorizon = signalObj?.horizon || signalObj?.style || '';
    const finalAction = (signalObj?.signal || 'HOLD').toUpperCase();

    const trendPhrase = signalObj?.is_bullish_trend
      ? "a bullish long-term trend"
      : "a bearish long-term trend";

    const sentimentPct = signalObj?.sentiment_score;
    const sentimentPhrase = sentimentPct === undefined
      ? ""
      : sentimentPct >= 60
        ? " amid clearly positive news sentiment"
        : sentimentPct <= 40
          ? " amid soft news sentiment"
          : " with roughly neutral news sentiment";

    const confidencePhrase = (conf: number | undefined) => {
      if (conf === undefined) return "";
      const pct = Math.round(conf * 100);
      if (pct >= 70) return ` The model carries high confidence (${pct}%) in this read.`;
      if (pct >= 45) return ` Confidence sits at a moderate ${pct}%, so treat this as directional rather than definitive.`;
      return ` Confidence is low (${pct}%) — signals are conflicting here, so weigh this call cautiously.`;
    };

    let body = "";

    if (activeHorizon === 'intraday') {
      const runData = run.intraday_mean_reversion || run.active_run || {};
      const ind = runData?.indicators || {};
      const posRel = (ind?.last_price ?? 0) > (ind?.upper_band ?? Infinity)
        ? "pressing the upper Bollinger Band, a stretched/overbought zone"
        : (ind?.last_price ?? 0) < (ind?.lower_band ?? -Infinity)
          ? "testing the lower Bollinger Band, a stretched/oversold zone"
          : "trading inside its Bollinger range without an extreme reading";
      body = `Mean Reversion (15m) is the active model. Price (₹${ind?.last_price?.toFixed(2) ?? '—'}) is ${posRel}, with VWAP at ₹${ind?.vwap?.toFixed(2) ?? '—'}.`;
      body += confidencePhrase(runData?.confidence);
    } else if (activeHorizon === 'swing') {
      const runData = run.swing_momentum || run.active_run || {};
      const ind = runData?.indicators || {};
      // Only compare when both sides are actual numbers — previously this
      // defaulted missing values to 0 via `?? 0`, so 0 > 0 evaluated to
      // false and silently reported "a bearish EMA crossover" any time the
      // backend simply hadn't returned ema_fast/ema_slow/macd/signal_line,
      // regardless of the real signal direction.
      const hasEma = typeof ind?.ema_fast === 'number' && typeof ind?.ema_slow === 'number';
      const hasMacd = typeof ind?.macd === 'number' && typeof ind?.signal_line === 'number';

      const crossRel = hasEma
        ? (ind.ema_fast > ind.ema_slow ? "a bullish EMA crossover" : "a bearish EMA crossover")
        : "an EMA crossover it hasn't reported readings for";
      const emaLine = hasEma
        ? ` — EMA-12 at ₹${ind.ema_fast.toFixed(2)} vs EMA-26 at ₹${ind.ema_slow.toFixed(2)}`
        : " — EMA-12/EMA-26 values weren't returned for this run";
      const macdLine = hasMacd
        ? ` MACD (${ind.macd.toFixed(3)}) sits ${ind.macd > ind.signal_line ? "above" : "below"} its Signal line (${ind.signal_line.toFixed(3)}).`
        : " MACD/Signal line data is unavailable for this run.";

      body = `Swing Momentum (1D) is the active model, currently showing ${crossRel}${emaLine}.${macdLine}`;
      body += confidencePhrase(runData?.confidence);
    } else if (activeHorizon === 'investment') {
      const runData = run.long_term_value || run.active_run || {};
      const ind = runData?.indicators || {};
      const hasWeeklyTrend = typeof ind?.last_price === 'number' && typeof ind?.weekly_ema_200 === 'number';
      const trendRel = hasWeeklyTrend
        ? (ind.last_price > ind.weekly_ema_200 ? "above" : "below")
        : "at an unconfirmed position relative to";
      const weeklyEmaStr = typeof ind?.weekly_ema_200 === 'number' ? `₹${ind.weekly_ema_200.toFixed(2)}` : "an unreported level";
      body = `Long-Term Value (1W) governs structural allocation. Price sits ${trendRel} its Weekly EMA-200 (${weeklyEmaStr}), with Weekly RSI-14 at ${ind?.weekly_rsi_14?.toFixed(2) ?? '—'}. Allocation cap: ${((runData?.allocation_ratio ?? 0) * 100).toFixed(0)}%.`;
      body += confidencePhrase(runData?.confidence);
    } else {
      return "The system is holding positions while validating structural trend thresholds.";
    }

    return `${body} This sits against ${trendPhrase}${sentimentPhrase}, giving a consensus call of ${finalAction}.`;
  }, [localStrategySignal]);

  // Pricing multiplexer (LTP vs Web REST fallback)
  const displayedPrice = useMemo(() => {
    if (liveTick && active && liveTick.token === active.token) {
      return liveTick.ltp;
    }
    return restPrice;
  }, [liveTick, active, restPrice]);

  // Unified ledger compiler
  const allTrades = useMemo(() => {
    const openTrades = positions.map(pos => {
      const isLong = pos.type === 'LONG';
      const points = isLong ? (pos.currentPrice - pos.entryPrice) : (pos.entryPrice - pos.currentPrice);
      const pnl = points * pos.qty * pos.lotSize;
      return {
        id: pos.id,
        timestamp: pos.timestamp,
        symbol: pos.symbol,
        entryPrice: pos.entryPrice,
        qty: pos.qty,
        status: 'OPEN' as const,
        exitPrice: pos.currentPrice,
        points: parseFloat(points.toFixed(2)),
        pnl: parseFloat(pnl.toFixed(2)),
        type: pos.type
      };
    });

    const closedTrades = realizedTrades.map(t => ({
      id: t.id,
      timestamp: t.timestamp,
      symbol: t.symbol,
      entryPrice: t.entryPrice,
      qty: t.qty,
      status: 'CLOSED' as const,
      exitPrice: t.exitPrice,
      points: t.points,
      pnl: t.pnl,
      type: t.type
    }));

    return [...openTrades, ...closedTrades].sort((a, b) => b.timestamp - a.timestamp);
  }, [positions, realizedTrades]);

  // Accuracy matrix metrics compiler
  const summaryStats = useMemo(() => {
    const total = allTrades.length;
    const wins = allTrades.filter(t => t.pnl > 0).length;
    const losses = allTrades.filter(t => t.pnl < 0).length;
    const totalPnl = allTrades.reduce((acc, t) => acc + t.pnl, 0);
    const avgPnl = total > 0 ? totalPnl / total : 0;
    
    return { total, wins, losses, totalPnl, avgPnl };
  }, [allTrades]);

  // ─── 5. REACT LIFECYCLE EFFECTS ────────────────────────────────────────────

  // Synchronize dynamic balances & transaction archives
  useEffect(() => {
    localStorage.setItem('nomisma_paper_balance', balance.toString());
  }, [balance]);

  useEffect(() => {
    localStorage.setItem('nomisma_paper_positions', JSON.stringify(positions));
  }, [positions]);

  useEffect(() => {
    localStorage.setItem('nomisma_paper_orders', JSON.stringify(orders));
  }, [orders]);

  useEffect(() => {
    localStorage.setItem('nomisma_paper_realized', JSON.stringify(realizedTrades));
  }, [realizedTrades]);

  useEffect(() => { localStorage.setItem('nomisma_autotrade_meta', JSON.stringify(autoTradeMeta)); }, [autoTradeMeta]);

  // Synchronize UI symbol with selected asset changes
  useEffect(() => {
    if (active) {
      const cleanTicker = active.symbol.replace(/-EQ$/i, '').replace(/-BE$/i);
      setTradeSymbol(`${active.exchange}:${cleanTicker}`);
    }
  }, [active]);

  // Sync prop activeSymbol down to local tradeSymbol
  useEffect(() => {
    if (activeSymbol) {
      setTradeSymbol(activeSymbol);
    }
  }, [activeSymbol]);

  // Sync incoming strategySignal prop updates from parent.
  // Guard: if the user has manually chosen an interval (userOverrideRef),
  // only accept the prop update when it actually matches what the user
  // selected — otherwise a stale/default poll from the parent would
  // silently flip the banner back while the dropdown still shows the
  // user's choice.
  useEffect(() => {
    if (!strategySignal) return;
    const incoming = strategySignal as any;

    if (!userOverrideRef.current) {
      setLocalStrategySignal(strategySignal);
      return;
    }

    if (incoming?.interval && incoming.interval === selectedInterval) {
      setLocalStrategySignal(strategySignal);
    }
    // Otherwise: ignore this prop update, it doesn't match the user's
    // current selection and would desync the dropdown from the banner.
  }, [strategySignal, selectedInterval]);

  // Reset/Initialize slider to parent consensus only when activeSymbol shifts.
  // A new active symbol clears any prior manual override so the panel goes
  // back to following the parent's default interval for that new symbol.
  useEffect(() => {
    userOverrideRef.current = false;
    const signalObj = strategySignal as any;
    if (signalObj?.interval) {
      setSelectedInterval(signalObj.interval);
    }
  }, [activeSymbol]);

  // Maintain active strategy lock when symbol or interval changes
  useEffect(() => {
    if (tradeSymbol && selectedInterval) {
      fetchStrategySignalOverride(selectedInterval);
    }
  }, [tradeSymbol, selectedInterval, fetchStrategySignalOverride]);

  // ─── Timeframe Lock: Strategy Workspace always follows the ticker's TIMEFRAME ───
  // Whenever the ticker's selected timeframe (lockedInterval) changes, force the
  // strategy dropdown to that exact same interval. This is a hard override — it
  // always wins over any earlier manual pick — because analysis for a mismatched
  // timeframe is actively misleading (e.g. a 3-minute chart paired with a daily
  // strategy). The dropdown itself also disables every non-matching option so
  // there's no way to pick a mismatched pair in the first place.
  useEffect(() => {
    if (!lockedInterval) return;
    userOverrideRef.current = true;
    if (lockedInterval !== selectedInterval) {
      setSelectedInterval(lockedInterval);
    }
  }, [lockedInterval]);

  // ─── Timeframe Lock: Indicators tab always follows the ticker's TIMEFRAME ───
  // Identical hard-override pattern to the Strategy lock effect just above,
  // applied to the Indicators tab's now-individually-selectable interval.
  // Whenever the ticker's timeframe changes, the Indicator Engine snaps to
  // that exact same one of the 20 intervals — no more "only 3 buckets, and
  // they don't even match what Ticker is showing."
  useEffect(() => {
    if (!lockedInterval) return;
    if (lockedInterval !== indicatorInterval) {
      setIndicatorInterval(lockedInterval);
    }
  }, [lockedInterval]);

  // Query updated prices when changing target trading asset
  useEffect(() => {
    if (tradeSymbol) {
      api.quote(tradeSymbol)
        .then(q => {
          if (q && q.currentPrice) {
            setOrderPrice(q.currentPrice.toString());
            setRestPrice(q.currentPrice);
          }
        })
        .catch(() => {});
    }
  }, [tradeSymbol]);

  // High-frequency telemetry sync
  useEffect(() => {
    const apiSyncInterval = setInterval(async () => {
      if (positions.length === 0) return;
      setPositions(prev => {
        Promise.all(
          prev.map(async (pos) => {
            try {
              const quoteData = await api.quote(pos.symbol);
              if (quoteData && quoteData.currentPrice) {
                return { ...pos, currentPrice: quoteData.currentPrice };
              }
            } catch {
              // Retain local price on fetch failure
            }
            return pos;
          })
        ).then(updated => {
          setPositions(updated);
        });
        return prev;
      });
    }, 6000);

    const tickSimulationInterval = setInterval(() => {
      setPositions(prev => {
        if (prev.length === 0) return prev;
        return prev.map(pos => {
          const tickFluctuation = 1 + (Math.random() * 0.001 - 0.0005);
          const simulatedLTP = pos.currentPrice * tickFluctuation;
          return {
            ...pos,
            currentPrice: Number(simulatedLTP.toFixed(2))
          };
        });
      });
    }, 1500);

    return () => {
      clearInterval(apiSyncInterval);
      clearInterval(tickSimulationInterval);
    };
  }, [positions.length]);

  // Fetch Sentiment on Tab or Symbol Change
  useEffect(() => {
    if (tab === 'strategy' && tradeSymbol) {
      setSentimentLoading(true);
      api.sentiment(tradeSymbol)
        .then(data => {
          setSentimentData(data);
          setSentimentLoading(false);
        })
        .catch(err => {
          console.error("Failed to load sentiment indexes:", err);
          setSentimentLoading(false);
        });
    }
  }, [tab, tradeSymbol]);

  // Fetch the Indicator Engine sweep (~100 indicators across 7 categories)
  // for ONE exact Ticker timeframe at a time via
  // GET /api/active/indicators/by-interval — every one of the 20 Ticker
  // intervals is a real, separately-fetchable dataset now (see
  // services/market.py's get_ohlcv_dataframe_for_ticker_interval), not just
  // 3 fixed buckets. Results are cached client-side per interval in
  // indicatorsData (keyed by the exact interval string), so re-visiting a
  // timeframe you've already loaded is instant and switching timeframes
  // doesn't stomp on data you fetched for another one. Pass `force: true`
  // (from the RUN SWEEP button) to bypass the cache and refetch.
  //
  // Surfaces the ACTUAL failure reason (HTTP status + backend error detail,
  // or "network/CORS" if the request never reached the server) into
  // indicatorsError instead of silently showing an empty-looking panel —
  // that silent failure was the #1 cause of "why isn't this running".
  const fetchIndicatorSweep = useCallback(async (interval?: string, force?: boolean) => {
    const targetInterval = interval || indicatorInterval;
    if (!tradeSymbol) {
      setIndicatorsError('No active symbol yet — select one in Ticker or SmartAPI first.');
      return;
    }
    if (!force && indicatorsData?.[targetInterval]) {
      // Already cached for this exact timeframe — no need to hit the backend again.
      return;
    }
    setIndicatorsLoading(true);
    setIndicatorsError('');
    try {
      const params = new URLSearchParams({ symbol: tradeSymbol, interval: targetInterval });
      const res = await fetch(`${BACKEND}/api/active/indicators/by-interval?${params.toString()}`);
      if (!res.ok) {
        let detail = '';
        try { detail = (await res.json())?.detail || ''; } catch { /* body wasn't JSON */ }
        throw new Error(`HTTP ${res.status}${detail ? ` — ${detail}` : ''}`);
      }
      const data = await res.json();
      setIndicatorsData((prev: any) => ({ ...(prev || {}), [targetInterval]: data }));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn("[INDICATOR ENGINE FETCH ERROR]", err);
      setIndicatorsError(
        msg.includes('HTTP')
          ? msg
          : `Could not reach the backend at ${BACKEND} — is it running, and is routers/indicators_router.py registered in main.py? (${msg})`
      );
    } finally {
      setIndicatorsLoading(false);
    }
  }, [tradeSymbol, indicatorInterval, indicatorsData]);

  useEffect(() => {
    if (tab === 'indicators' && tradeSymbol) {
      fetchIndicatorSweep(indicatorInterval);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, tradeSymbol, indicatorInterval]);

  // Applies a custom-settings recompute for one indicator (e.g. RSI at
  // period=21 instead of the registry default of 14) via
  // POST /api/indicators/configurable. Result is stored per indicator name
  // and rendered instead of the default row until reset. Passes
  // ticker_interval so the recompute stays on the exact locked/selected
  // timeframe instead of drifting back to a fixed period/interval pair.
  const applyCustomIndicatorSettings = useCallback(async (name: string, type: string, params: Record<string, any>) => {
    if (!tradeSymbol) return;
    setCustomIndicatorLoading(prev => ({ ...prev, [name]: true }));
    try {
      const res = await fetch(`${BACKEND}/api/indicators/configurable`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: tradeSymbol,
          indicator: type,
          params,
          ticker_interval: indicatorInterval,
        }),
      });
      const data = await res.json();
      setCustomIndicatorResults(prev => ({ ...prev, [name]: data }));
    } catch (err) {
      setCustomIndicatorResults(prev => ({
        ...prev,
        [name]: { value: null, signal: 'ERROR', error: err instanceof Error ? err.message : String(err) },
      }));
    } finally {
      setCustomIndicatorLoading(prev => ({ ...prev, [name]: false }));
      setOpenSettingsFor(null);
    }
  }, [tradeSymbol, indicatorInterval]);

  // Handle Event-Driven Quick Orders from Left Profile Telemetry Panel
  useEffect(() => {
    const handleQuickOrderEvent = async (e: Event) => {
      const customEvent = e as CustomEvent;
      if (!customEvent.detail) return;

      const { symbol, side, price, qty } = customEvent.detail;
      const cleanTicker = symbol.trim().toUpperCase();

      await executeCoreOrder({
        symbol: cleanTicker,
        side,
        price,
        qty,
        lotSize: 1,      
        levValue: 1,     
        oType: 'MARKET'
      });
    };

    window.addEventListener('nomisma-quick-trade', handleQuickOrderEvent);
    return () => {
      window.removeEventListener('nomisma-quick-trade', handleQuickOrderEvent);
    };
  }, [executeCoreOrder]);

  // ─── Ticker tab: live broker telemetry (formerly lived inside the
  // profile drawer — now its own tab, decoupled from the Profile tab) ────────

  // Fetch live broker quote AND market depth together, in lockstep, so the
  // depth ladder can never drift out of sync with the LTP shown above it.
  // Previously these were two separate effects — the depth fetch only
  // depended on [activeSymbol], while the quote fetch also depended on
  // [lockedInterval]. Switching the Timeframe dropdown re-fetched a fresh
  // LTP but left the bid/ask depth frozen at whatever it was when the
  // symbol first loaded, which is why the two would visibly disagree
  // (bid/ask sitting well away from LTP) after any interval change or
  // simply after enough time passed with no refresh at all. Fetching both
  // from the same trigger — and re-polling both on the same timer — keeps
  // them describing the same instant.
  useEffect(() => {
    if (!activeSymbol) {
      setTickerQuote(null);
      setTickerFullData(null);
      return;
    }

    const cleanSym = activeSymbol.split('?')[0];
    let cancelled = false;

    const fetchSnapshot = (showLoadingState: boolean) => {
      if (showLoadingState) {
        setTickerLoadingQuote(true);
        setTickerQuote(null);
      }

      Promise.all([
        api.quote(`${activeSymbol}?interval=${lockedInterval || '1d'}`),
        api.quote(`${cleanSym}?full=true`).catch(() => null),
      ])
        .then(([quoteData, fullData]) => {
          if (cancelled) return;
          setTickerQuote(quoteData);
          setTickerFullData(fullData);
          if (showLoadingState) setTickerLoadingQuote(false);
        })
        .catch(err => {
          if (cancelled) return;
          console.error("[TELEMETRY] Error fetching live quote/depth:", err);
          if (showLoadingState) setTickerLoadingQuote(false);
        });
    };

    fetchSnapshot(true);

    // Keep both readings live rather than freezing at the initial snapshot.
    const pollId = setInterval(() => fetchSnapshot(false), 5000);

    return () => {
      cancelled = true;
      clearInterval(pollId);
    };
  }, [activeSymbol, lockedInterval]);

  // Fetch daily RMS summary on mount and whenever the symbol changes
  useEffect(() => {
    setTickerLoadingRms(true);
    api.getDailyRmsSummary()
      .then(data => {
        setTickerRmsSummary(data);
        setTickerLoadingRms(false);
      })
      .catch(err => {
        console.error("[RMS SUMMARY] Error retrieving risk indicators:", err);
        setTickerLoadingRms(false);
      });
  }, [activeSymbol]);

  // Reset strategy audit panel when the ticker changes
  useEffect(() => {
    setTickerAnalysisResult(null);
  }, [activeSymbol]);

  const handleTickerSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (tickerSearchInput.trim()) {
      const cleanSymbol = tickerSearchInput.trim().toUpperCase();
      const updateEvent = new CustomEvent('tv-symbol-change', { detail: cleanSymbol });
      window.dispatchEvent(updateEvent);
      setTickerSearchInput('');
    }
  };

  const runTickerTechnicalAudit = () => {
    if (!activeSymbol) return;
    setTickerRunningAnalysis(true);
    setTickerAnalysisResult(null);

    fetch(`${BACKEND}/api/active/multi-horizon-analysis`)
      .then(res => {
        if (!res.ok) throw new Error("Analysis failed");
        return res.json();
      })
      .then(resData => {
        if (resData && resData.status === 'success') {
          setTickerAnalysisResult(resData.analysis);
        }
        setTickerRunningAnalysis(false);
      })
      .catch(err => {
        console.error("Strategy audit failed:", err);
        setTickerRunningAnalysis(false);
      });
  };

  // Reuses the same 'nomisma-quick-trade' event the order-execution effect
  // above already listens for, so Ticker-tab quick trades place real paper orders.
  // priceOverride/qtyOverride let the Aero execution ticket (LIMIT/MARKET/STOP +
  // Price/Size fields) submit a chosen price and size instead of the LTP @ 1 default.
  const dispatchTickerQuickTrade = (side: 'BUY' | 'SELL', priceOverride?: number, qtyOverride?: number) => {
    if (!activeSymbol || !tickerQuote || !tickerQuote.currentPrice) return;
    const price = priceOverride && !isNaN(priceOverride) ? priceOverride : tickerQuote.currentPrice;
    const qty = qtyOverride && !isNaN(qtyOverride) && qtyOverride > 0 ? qtyOverride : 1;
    const tradeEvent = new CustomEvent('nomisma-quick-trade', {
      detail: { symbol: activeSymbol, side, price, qty }
    });
    window.dispatchEvent(tradeEvent);
  };

  const DEPTH_LEVELS = 5;

  const getTickerDepth = () => {
    if (tickerFullData?.depth?.buy && tickerFullData?.depth?.sell && tickerFullData.depth.buy.length > 0) {
      return { buy: tickerFullData.depth.buy.slice(0, DEPTH_LEVELS), sell: tickerFullData.depth.sell.slice(0, DEPTH_LEVELS) };
    }
    if (tickerQuote && tickerQuote.currentPrice) {
      const ltp = tickerQuote.currentPrice;
      const isIndian = activeSymbol?.toUpperCase().startsWith('NSE') || activeSymbol?.toUpperCase().startsWith('BSE');
      const stepValue = isIndian ? 0.05 : 0.01;
      const buyQtys = [1240, 3820, 4500, 8210, 12400];
      const sellQtys = [940, 2150, 5120, 6800, 10500];
      const mockBuy = buyQtys.map((qty, idx) => ({ price: ltp - stepValue * (idx + 1), quantity: qty, orders: Math.max(1, Math.round(qty / 620)) }));
      const mockSell = sellQtys.map((qty, idx) => ({ price: ltp + stepValue * (idx + 1), quantity: qty, orders: Math.max(1, Math.round(qty / 620)) }));
      return { buy: mockBuy, sell: mockSell };
    }
    const defaultBlankRows = Array(DEPTH_LEVELS).fill({ price: 0, quantity: 0, orders: 0 });
    return { buy: defaultBlankRows, sell: defaultBlankRows };
  };

  const tickerDepth = getTickerDepth();
  const tickerBuyDepth = tickerDepth.buy;
  const tickerSellDepth = tickerDepth.sell;

  const tickerIsUp = tickerQuote && tickerQuote.currentPrice >= tickerQuote.previousClose;
  const tickerNetChange = tickerQuote ? (tickerQuote.currentPrice - tickerQuote.previousClose) : 0;
  const tickerPctChange = tickerQuote ? ((tickerNetChange / tickerQuote.previousClose) * 100) : 0;

  const tickerIsIntraday = (lockedInterval || '1d') !== '1d' && lockedInterval !== '1w' && lockedInterval !== '1mo';
  const tickerIsMacro = lockedInterval === '1w' || lockedInterval === '1mo';

  // ─── 6. RENDER LOGIC ───────────────────────────────────────────────────────

  const openPnLVal = calculateOpenPnL();
  const portfolioValue = balance + openPnLVal;

  const winRate = summaryStats.total > 0
    ? (summaryStats.wins / summaryStats.total) * 100
    : 0;

  const currentNotionalExposure = parseFloat(orderPrice || '0') * orderQty * lotMultiplier;
  const estimatedRequiredMargin = leverage > 0 ? currentNotionalExposure / leverage : currentNotionalExposure;

  const signalObj = localStrategySignal as any;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%', background: 'transparent', position: 'relative' }}>

      {/* ── STYLISH CSS FOR TOAST ANIMATION ── */}
      <style>{`
        @keyframes slideIn {
          0% { transform: translateX(120%) translateY(0); opacity: 0; }
          100% { transform: translateX(0) translateY(0); opacity: 1; }
        }
      `}</style>

      {/* ── FLOATING TOAST NOTIFICATION STACK ── */}
      <div style={{
        position: 'absolute', top: '16px', right: '16px', zIndex: 99999,
        display: 'flex', flexDirection: 'column', gap: '8px', pointerEvents: 'none'
      }}>
        {toasts.map(t => (
          <div key={t.id} style={{
            pointerEvents: 'auto', minWidth: '320px', maxWidth: '400px',
            background: 'var(--bg-raised)',
            borderLeft: `4px solid ${t.error ? 'var(--negative)' : 'var(--positive)'}`,
            borderRadius: '4px', boxShadow: '0 8px 24px rgba(0,0,0,0.6)',
            padding: '12px 16px', display: 'flex', alignItems: 'center', gap: '12px',
            color: 'var(--silver-bright)', fontFamily: 'var(--font-data)', fontSize: '11px',
            animation: 'slideIn 0.35s cubic-bezier(0.16, 1, 0.3, 1) forwards'
          }}>
            <span style={{ 
              display: 'flex', alignItems: 'center', justifySelf: 'center',
              width: '18px', height: '18px', borderRadius: '50%',
              background: t.error ? 'rgba(244, 63, 94, 0.15)' : 'rgba(16, 185, 129, 0.15)',
              color: t.error ? 'var(--negative)' : 'var(--positive)',
              fontSize: '10px', fontWeight: 'bold', justifyContent: 'center'
            }}>
              {t.error ? '✕' : '✓'}
            </span>
            <div style={{ flex: 1, lineHeight: '1.4' }}>{t.text}</div>
          </div>
        ))}
      </div>

      {/* ── Top bar: page title (driven by the bottom nav) + theme toggle + live meta ── */}
      <div className="top-bar">
        <span className="top-bar-title">{TAB_TITLES[tab]}</span>

        <div className="top-bar-meta">
          {active && (
            <>
              <span>{active.name}</span>
              <span style={{ color: 'var(--silver-dim)' }}>{active.exchange}:{active.symbol}</span>
              {displayedPrice !== null && (
                <span className={`list-row-value ${liveTick && liveTick.change_pct < 0 ? 'negative' : 'positive'}`}>
                  ₹{displayedPrice.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  {liveTick && liveTick.token === active.token && (
                    <span style={{ fontSize: '10px', marginLeft: '6px' }}>
                      ({liveTick.change_pct >= 0 ? '+' : ''}{liveTick.change_pct.toFixed(2)}%)
                    </span>
                  )}
                </span>
              )}
            </>
          )}
        </div>

        <div className="top-bar-actions">
          {strategySignal?.signal && (
            <span
              className="status-chip"
              style={{
                background: strategySignal.signal.toUpperCase() === 'BUY' ? 'rgba(16,185,129,0.12)'
                  : (strategySignal.signal.toUpperCase() === 'SHORT' || strategySignal.signal.toUpperCase() === 'SELL') ? 'rgba(244,63,94,0.12)'
                  : 'var(--bg-raised)',
                color: strategySignal.signal.toUpperCase() === 'BUY' ? 'var(--positive)'
                  : (strategySignal.signal.toUpperCase() === 'SHORT' || strategySignal.signal.toUpperCase() === 'SELL') ? 'var(--negative)'
                  : 'var(--silver-dim)'
              }}
            >
              {strategySignal.signal}
            </span>
          )}
          {onThemeToggle && (
            <button
              type="button"
              className="top-bar-icon-btn"
              onClick={onThemeToggle}
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
          )}
        </div>
      </div>

      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>

        <webview
          ref={angelRef}
          src="https://trade.angelone.in"
          partition="persist:angelone"
          useragent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
          style={{
            position: 'absolute', inset: 0, width: '100%', height: '100%',
            opacity: tab === 'angelone' ? 1 : 0,
            pointerEvents: tab === 'angelone' ? 'auto' : 'none',
            zIndex: tab === 'angelone' ? 1 : -1
          }}
        />

        <webview
          ref={tvRef}
          src="https://www.tradingview.com/chart/"
          partition="persist:tradingview"
          useragent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
          style={{
            position: 'absolute', inset: 0, width: '100%', height: '100%',
            opacity: tab === 'tradingview' ? 1 : 0,
            pointerEvents: tab === 'tradingview' ? 'auto' : 'none',
            zIndex: tab === 'tradingview' ? 1 : -1
          }}
        />

        {/* ── TAB: TICKER (search + live broker telemetry) ── */}
        {tab === 'ticker' && (
          <div style={{
            position: 'absolute', inset: 0, zIndex: 2,
            display: 'flex', flexDirection: 'column', gap: '14px',
            padding: '20px', overflowY: 'auto', background: 'transparent'
          }}>
            <div style={{ width: '100%', maxWidth: '1100px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '14px' }}>

              <form onSubmit={handleTickerSearchSubmit} style={{ display: 'flex', gap: '8px', maxWidth: '560px', width: '100%', margin: '0 auto' }}>
                <input
                  type="text"
                  placeholder="SEARCH TICKER (e.g. SBIN, TCS, AAPL)"
                  value={tickerSearchInput}
                  onChange={e => setTickerSearchInput(e.target.value)}
                  style={{
                    flex: 1,
                    background: 'var(--bg-surface)',
                    border: '1px solid var(--border)',
                    borderRadius: '4px',
                    color: 'var(--gold-bright)',
                    fontSize: '11px',
                    padding: '10px 12px',
                    outline: 'none',
                    fontFamily: 'var(--font-data)'
                  }}
                />
                <button
                  type="submit"
                  className="login-submit-btn"
                  style={{ margin: 0, padding: '10px 16px', width: 'auto', fontSize: '11px' }}
                >
                  LOAD
                </button>
              </form>

              <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', alignItems: 'stretch' }}>
                <div style={{ flex: '1 1 460px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
              {activeSymbol ? (
                <div className="feature-slot" style={{ border: '1px solid var(--border)', background: 'var(--bg-raised)', textAlign: 'left', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border)', paddingBottom: '6px' }}>
                    <span style={{ fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: '13px', letterSpacing: '0.05em' }}>
                      LIVE TELEMETRY: {activeSymbol}
                    </span>
                    <span className="status-pulse-dot" style={{ background: tickerLoadingQuote ? 'var(--neutral)' : 'var(--positive)', boxShadow: tickerLoadingQuote ? '0 0 8px var(--neutral)' : '0 0 8px var(--positive)' }} />
                  </div>

                  <div style={{ display: 'flex', justifySelf: 'stretch', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                    <span style={{ fontSize: '11px', color: 'var(--silver-dim)', fontFamily: 'var(--font-data)' }}>Timeframe:</span>
                    <select
                      value={lockedInterval || '1d'}
                      onChange={e => onIntervalChange && onIntervalChange(e.target.value)}
                      style={{
                        background: 'var(--bg-base)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: '4px',
                        color: 'var(--gold-bright)',
                        fontSize: '11px',
                        padding: '2px 4px',
                        outline: 'none',
                        cursor: 'pointer',
                        fontFamily: 'var(--font-data)',
                        width: '100px',
                        maxWidth: '100px',
                        textOverflow: 'ellipsis',
                        overflow: 'hidden'
                      }}
                    >
                      <optgroup label="SECONDS" style={{ background: 'var(--bg-surface)' }}>
                        <option value="15s">15s</option>
                        <option value="30s">30s</option>
                        <option value="45s">45s</option>
                      </optgroup>
                      <optgroup label="MINUTES" style={{ background: 'var(--bg-surface)' }}>
                        <option value="1m">1m</option>
                        <option value="2m">2m</option>
                        <option value="3m">3m</option>
                        <option value="4m">4m</option>
                        <option value="5m">5m</option>
                        <option value="10m">10m</option>
                        <option value="15m">15m</option>
                        <option value="30m">30m</option>
                        <option value="75m">75m</option>
                        <option value="125m">125m</option>
                      </optgroup>
                      <optgroup label="HOURS" style={{ background: 'var(--bg-surface)' }}>
                        <option value="1h">1h</option>
                        <option value="2h">2h</option>
                        <option value="3h">3h</option>
                        <option value="4h">4h</option>
                      </optgroup>
                      <optgroup label="DAYS" style={{ background: 'var(--bg-surface)' }}>
                        <option value="1d">1d</option>
                        <option value="1w">1w</option>
                        <option value="1mo">1mo</option>
                      </optgroup>
                    </select>
                  </div>

                  {tickerLoadingQuote && !tickerQuote ? (
                    <p style={{ fontFamily: 'var(--font-ui)', fontSize: '11px', color: 'var(--silver-dim)' }}>Fetching the latest price…</p>
                  ) : tickerQuote && tickerQuote.currentPrice ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', fontFamily: 'var(--font-data)', fontSize: '11px', color: 'var(--silver-bright)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span>NAME:</span>
                        <span style={{ fontWeight: 'bold' }}>{tickerQuote.shortName || 'N/A'}</span>
                      </div>

                      <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border-subtle)', paddingBottom: '4px', marginBottom: '2px' }}>
                        <span>LTP (LAST PRICE):</span>
                        <div style={{ textAlign: 'right' }}>
                          <span style={{ color: 'var(--gold-bright)', fontWeight: 'bold' }}>{tickerQuote.currency === "INR" ? "₹" : "$"}{tickerQuote.currentPrice ? tickerQuote.currentPrice.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : 'N/A'}</span>
                          <div style={{ fontSize: '9px', fontWeight: 'bold', color: tickerIsUp ? 'var(--positive)' : 'var(--negative)', marginTop: '2px' }}>
                            {tickerIsUp ? '▲' : '▼'} {tickerQuote.currency === "INR" ? "₹" : "$"}{Math.abs(tickerNetChange).toLocaleString('en-IN', { minimumFractionDigits: 2 })} ({tickerIsUp ? '+' : ''}{tickerPctChange.toFixed(2)}%)
                          </div>
                        </div>
                      </div>

                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span>Prev close:</span>
                        <span>{tickerQuote.currency === "INR" ? "₹" : "$"}{tickerQuote.previousClose ? tickerQuote.previousClose.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : 'N/A'}</span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span>OPEN:</span>
                        <span>{tickerQuote.currency === "INR" ? "₹" : "$"}{tickerQuote.open ? tickerQuote.open.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : 'N/A'}</span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span>High / low:</span>
                        <div>
                          <span style={{ color: 'var(--positive)' }}>{tickerQuote.currency === "INR" ? "₹" : "$"}{tickerQuote.dayHigh ? tickerQuote.dayHigh.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : 'N/A'}</span>
                          <span style={{ color: 'var(--silver-dim)', margin: '0 4px' }}>/</span>
                          <span style={{ color: 'var(--negative)' }}>{tickerQuote.currency === "INR" ? "₹" : "$"}{tickerQuote.dayLow ? tickerQuote.dayLow.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : 'N/A'}</span>
                        </div>
                      </div>

                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span>Session volume:</span>
                        <span>{tickerQuote.volume ? tickerQuote.volume.toLocaleString('en-IN') : 'N/A'}</span>
                      </div>

                      {tickerQuote.fiftyTwoWeekHigh && (
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span>52W HIGH / LOW:</span>
                          <div>
                            <span style={{ color: 'var(--positive)' }}>{tickerQuote.currency === "INR" ? "₹" : "$"}{tickerQuote.fiftyTwoWeekHigh.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span>
                            <span style={{ color: 'var(--silver-dim)', margin: '0 4px' }}>/</span>
                            <span style={{ color: 'var(--negative)' }}>{tickerQuote.currency === "INR" ? "₹" : "$"}{tickerQuote.fiftyTwoWeekLow.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span>
                          </div>
                        </div>
                      )}

                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', borderTop: '1px solid var(--border-subtle)', paddingTop: '6px', marginTop: '4px' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span>Lower circuit:</span>
                          <span style={{ color: 'var(--negative)' }}>{tickerFullData?.lowerCircuit ? `₹${tickerFullData.lowerCircuit.toLocaleString('en-IN', { minimumFractionDigits: 2 })}` : 'N/A'}</span>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span>Upper circuit:</span>
                          <span style={{ color: 'var(--positive)' }}>{tickerFullData?.upperCircuit ? `₹${tickerFullData.upperCircuit.toLocaleString('en-IN', { minimumFractionDigits: 2 })}` : 'N/A'}</span>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span>Avg price (ATP):</span>
                          <span>{tickerFullData?.avgPrice ? `₹${tickerFullData.avgPrice.toLocaleString('en-IN', { minimumFractionDigits: 2 })}` : 'N/A'}</span>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span>Order book qty (B/S):</span>
                          <div>
                            <span style={{ color: 'var(--positive)' }}>{tickerFullData?.totBuyQty ? tickerFullData.totBuyQty.toLocaleString('en-IN') : 0}</span>
                            <span style={{ color: 'var(--silver-dim)', margin: '0 4px' }}>/</span>
                            <span style={{ color: 'var(--negative)' }}>{tickerFullData?.totSellQty ? tickerFullData.totSellQty.toLocaleString('en-IN') : 0}</span>
                          </div>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span>Open interest (OI):</span>
                          <span style={{ color: 'var(--gold-bright)' }}>{tickerFullData?.opnInterest ? tickerFullData.opnInterest.toLocaleString('en-IN') : 'N/A'}</span>
                        </div>
                      </div>

                      {/* Operational Quick-Trading execution buttons connected to the paper ledger */}
                      <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                        <button
                          type="button"
                          className="rgb-btn"
                          style={{
                            flex: 1, background: 'rgba(0, 176, 155, 0.1)', border: '1px solid var(--positive)',
                            color: 'var(--positive)', fontWeight: 'bold', padding: '8px', borderRadius: '4px',
                            cursor: 'pointer', fontFamily: 'var(--font-display)', fontSize: '13px', letterSpacing: '0.05em'
                          }}
                          onClick={() => dispatchTickerQuickTrade('BUY')}
                        >
                          BUY @ {tickerQuote?.currentPrice ? `₹${tickerQuote.currentPrice.toLocaleString('en-IN', { minimumFractionDigits: 2 })}` : 'N/A'}
                        </button>
                        <button
                          type="button"
                          className="rgb-btn"
                          style={{
                            flex: 1, background: 'rgba(255, 74, 90, 0.1)', border: '1px solid var(--negative)',
                            color: 'var(--negative)', fontWeight: 'bold', padding: '8px', borderRadius: '4px',
                            cursor: 'pointer', fontFamily: 'var(--font-display)', fontSize: '13px', letterSpacing: '0.05em'
                          }}
                          onClick={() => dispatchTickerQuickTrade('SELL')}
                        >
                          SELL @ {tickerQuote?.currentPrice ? `₹${tickerQuote.currentPrice.toLocaleString('en-IN', { minimumFractionDigits: 2 })}` : 'N/A'}
                        </button>
                      </div>

                      <button
                        type="button"
                        className="login-submit-btn"
                        onClick={runTickerTechnicalAudit}
                        disabled={tickerRunningAnalysis}
                        style={{ marginTop: '12px', padding: '10px', fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.05em' }}
                      >
                        {tickerRunningAnalysis ? 'Executing Strategy...' : 'Run Strategy Audit'}
                      </button>
                    </div>
                  ) : (
                    <p style={{ fontFamily: 'var(--font-ui)', fontSize: '11px', color: 'var(--silver-dim)' }}>No live price right now — we'll pick it back up shortly.</p>
                  )}
                </div>
              ) : (
                <div className="feature-slot" style={{ flex: 1, display: 'flex', alignItems: 'center', justifySelf: 'center' }}>
                  <p style={{ color: 'var(--silver-dim)', fontSize: '11px', textAlign: 'center' }}>
                    Search for a ticker above to begin live telemetry — powered by Angel One SmartAPI.
                  </p>
                </div>
              )}

              {/* Dynamic Technical Strategy Report Section */}
              {tickerAnalysisResult && (
                <div className="feature-slot" style={{ border: '1px solid var(--border)', background: 'var(--bg-raised)', textAlign: 'left', display: 'flex', flexDirection: 'column', gap: '8px', padding: '12px' }}>
                  <div style={{ borderBottom: '1px solid var(--border)', paddingBottom: '6px' }}>
                    <span style={{ fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: '12px', letterSpacing: '0.05em' }}>
                      {tickerIsIntraday ? 'INTRADAY SCALPING AUDIT' : tickerIsMacro ? 'MACRO STRUCTURAL AUDIT' : 'DAILY SWING TREND AUDIT'}
                    </span>
                  </div>

                  {tickerIsIntraday && tickerAnalysisResult.intraday_metrics && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', fontFamily: 'var(--font-data)', fontSize: '11px', color: 'var(--silver-bright)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span>Intraday volatility:</span>
                        <span style={{ fontWeight: 'bold', color: 'var(--gold-bright)' }}>{(tickerAnalysisResult.intraday_metrics.intraday_volatility * 100).toFixed(2)}%</span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span>Candle volume density:</span>
                        <span>{Math.round(tickerAnalysisResult.intraday_metrics.avg_candle_volume).toLocaleString('en-IN')} shares</span>
                      </div>
                      <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '6px', marginTop: '2px' }}>
                        <span style={{ fontSize: '10px', color: 'var(--silver-dim)', display: 'block', marginBottom: '2px' }}>What this suggests:</span>
                        <span style={{ fontWeight: 'bold', color: tickerAnalysisResult.intraday_metrics.intraday_volatility > 0.08 ? 'var(--positive)' : 'var(--gold-bright)' }}>
                          {tickerAnalysisResult.intraday_metrics.intraday_volatility > 0.08
                            ? 'HIGH-VOLATILITY BREAKOUT: Momentum breakouts over narrow daily ranges.'
                            : 'MEAN-REVERSION SCALPING: Range scalps between Bollinger bands.'}
                        </span>
                      </div>
                    </div>
                  )}

                  {!tickerIsIntraday && !tickerIsMacro && tickerAnalysisResult.medium_term_metrics && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', fontFamily: 'var(--font-data)', fontSize: '11px', color: 'var(--silver-bright)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span>50 SMA / 200 SMA:</span>
                        <div>
                          <span>₹{tickerAnalysisResult.medium_term_metrics.sma_50 ? tickerAnalysisResult.medium_term_metrics.sma_50.toLocaleString('en-IN', { maximumFractionDigits: 1 }) : 'N/A'}</span>
                          <span style={{ color: 'var(--silver-dim)', margin: '0 4px' }}>/</span>
                          <span>₹{tickerAnalysisResult.medium_term_metrics.sma_200 ? tickerAnalysisResult.medium_term_metrics.sma_200.toLocaleString('en-IN', { maximumFractionDigits: 1 }) : 'N/A'}</span>
                        </div>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span>RSI (14) momentum:</span>
                        <span style={{ fontWeight: 'bold', color: tickerAnalysisResult.medium_term_metrics.rsi_14 > 70 ? 'var(--negative)' : tickerAnalysisResult.medium_term_metrics.rsi_14 < 30 ? 'var(--positive)' : 'var(--silver-bright)' }}>
                          {tickerAnalysisResult.medium_term_metrics.rsi_14 ? tickerAnalysisResult.medium_term_metrics.rsi_14.toFixed(2) : 'N/A'}
                        </span>
                      </div>
                      <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '6px', marginTop: '2px' }}>
                        <span style={{ fontSize: '10px', color: 'var(--silver-dim)', display: 'block', marginBottom: '2px' }}>What the trend suggests</span>
                        <span style={{ fontWeight: 'bold', color: tickerAnalysisResult.medium_term_metrics.rsi_14 > 70 ? 'var(--negative)' : tickerAnalysisResult.medium_term_metrics.rsi_14 < 30 ? 'var(--positive)' : 'var(--gold-bright)' }}>
                          {tickerAnalysisResult.medium_term_metrics.rsi_14 > 70
                            ? 'Overbought: price has run up fast. Consider tighter stop-losses.'
                            : tickerAnalysisResult.medium_term_metrics.rsi_14 < 30
                            ? 'Oversold: price has pulled back sharply — some see this as a value zone.'
                            : `Trend looks steady, currently ${tickerAnalysisResult.medium_term_metrics.current_trend}.`}
                        </span>
                      </div>
                    </div>
                  )}

                  {tickerIsMacro && tickerAnalysisResult.long_term_metrics && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', fontFamily: 'var(--font-data)', fontSize: '11px', color: 'var(--silver-bright)' }}>
                      <div style={{ borderBottom: '1px solid var(--border-subtle)', paddingBottom: '4px' }}>
                        <span style={{ fontSize: '10px', color: 'var(--silver-dim)', display: 'block', marginBottom: '2px' }}>5-year resistance levels:</span>
                        <div style={{ display: 'flex', gap: '8px', color: 'var(--negative)' }}>
                          {tickerAnalysisResult.long_term_metrics.macro_resistances.map((r: number, idx: number) => (
                            <span key={idx}>₹{r.toLocaleString('en-IN', { maximumFractionDigits: 1 })}</span>
                          ))}
                        </div>
                      </div>
                      <div style={{ paddingBottom: '4px' }}>
                        <span style={{ fontSize: '10px', color: 'var(--silver-dim)', display: 'block', marginBottom: '2px' }}>5-year support levels:</span>
                        <div style={{ display: 'flex', gap: '8px', color: 'var(--positive)' }}>
                          {tickerAnalysisResult.long_term_metrics.macro_supports.map((s: number, idx: number) => (
                            <span key={idx}>₹{s.toLocaleString('en-IN', { maximumFractionDigits: 1 })}</span>
                          ))}
                        </div>
                      </div>
                      <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '6px', marginTop: '2px' }}>
                        <span style={{ fontSize: '10px', color: 'var(--silver-dim)', display: 'block', marginBottom: '2px' }}>Long-term positioning:</span>
                        <span style={{ fontWeight: 'bold', color: 'var(--gold-bright)' }}>
                          MACRO POSITION BIAS: Build core positions near Supports. Scale-out or hedge portfolios near Resistances.
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              )}
                </div>{/* close LEFT COLUMN — Live Telemetry + Strategy Audit */}

                <div style={{ flex: '1 1 420px', display: 'flex', flexDirection: 'column', gap: '14px' }}>{/* RIGHT COLUMN — Market Depth (Bid / Ask stacked) */}
              {/* Bid depth — top box */}
              <div className="feature-slot" style={{ border: '1px solid var(--border)', background: 'var(--bg-raised)', textAlign: 'left', display: 'flex', flexDirection: 'column', gap: '8px', padding: '12px', flex: 1 }}>
                <div style={{ borderBottom: '1px solid var(--border)', paddingBottom: '6px' }}>
                  <span style={{ fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: '12px', letterSpacing: '0.05em' }}>
                    Bid depth (top {tickerBuyDepth.length})
                  </span>
                </div>

                <div style={{ display: 'flex', border: '1px solid var(--border)', borderRadius: '4px', background: 'var(--bg-base)', overflow: 'hidden', flex: 1, minHeight: 0 }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '10px', fontFamily: 'var(--font-data)' }}>
                    <thead>
                      <tr style={{ background: 'rgba(16, 185, 129, 0.05)', borderBottom: '1px solid var(--border)', color: 'var(--positive)' }}>
                        <th style={{ padding: '6px', textAlign: 'left', fontWeight: 'bold', borderRight: '1px solid var(--border-subtle)' }}>Bid price</th>
                        <th style={{ padding: '6px', textAlign: 'right', fontWeight: 'bold', borderRight: '1px solid var(--border-subtle)' }}>Qty</th>
                        <th style={{ padding: '6px', textAlign: 'right', fontWeight: 'bold' }}>Orders</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tickerBuyDepth.map((b: any, i: number) => (
                        <tr key={i} style={{ borderBottom: i < tickerBuyDepth.length - 1 ? '1px solid var(--border-subtle)' : 'none' }}>
                          <td style={{ padding: '6px', color: 'var(--positive)', textAlign: 'left', borderRight: '1px solid var(--border-subtle)' }}>
                            {b.price > 0 ? `₹${b.price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '₹0.00'}
                          </td>
                          <td style={{ padding: '6px', color: 'var(--silver-bright)', textAlign: 'right', fontWeight: 'bold', borderRight: '1px solid var(--border-subtle)' }}>
                            {b.quantity > 0 ? b.quantity.toLocaleString('en-IN') : '0'}
                          </td>
                          <td style={{ padding: '6px', color: 'var(--silver-dim)', textAlign: 'right' }}>
                            {b.orders ? b.orders.toLocaleString('en-IN') : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Ask depth — bottom box */}
              <div className="feature-slot" style={{ border: '1px solid var(--border)', background: 'var(--bg-raised)', textAlign: 'left', display: 'flex', flexDirection: 'column', gap: '8px', padding: '12px', flex: 1 }}>
                <div style={{ borderBottom: '1px solid var(--border)', paddingBottom: '6px' }}>
                  <span style={{ fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: '12px', letterSpacing: '0.05em' }}>
                    Ask depth (top {tickerSellDepth.length})
                  </span>
                </div>

                <div style={{ display: 'flex', border: '1px solid var(--border)', borderRadius: '4px', background: 'var(--bg-base)', overflow: 'hidden', flex: 1, minHeight: 0 }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '10px', fontFamily: 'var(--font-data)' }}>
                    <thead>
                      <tr style={{ background: 'rgba(244, 63, 94, 0.05)', borderBottom: '1px solid var(--border)', color: 'var(--negative)' }}>
                        <th style={{ padding: '6px', textAlign: 'left', fontWeight: 'bold', borderRight: '1px solid var(--border-subtle)' }}>Ask price</th>
                        <th style={{ padding: '6px', textAlign: 'right', fontWeight: 'bold', borderRight: '1px solid var(--border-subtle)' }}>Qty</th>
                        <th style={{ padding: '6px', textAlign: 'right', fontWeight: 'bold' }}>Orders</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tickerSellDepth.map((s: any, i: number) => (
                        <tr key={i} style={{ borderBottom: i < tickerSellDepth.length - 1 ? '1px solid var(--border-subtle)' : 'none' }}>
                          <td style={{ padding: '6px', color: 'var(--negative)', textAlign: 'left', borderRight: '1px solid var(--border-subtle)' }}>
                            {s.price > 0 ? `₹${s.price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '₹0.00'}
                          </td>
                          <td style={{ padding: '6px', color: 'var(--silver-bright)', textAlign: 'right', fontWeight: 'bold', borderRight: '1px solid var(--border-subtle)' }}>
                            {s.quantity > 0 ? s.quantity.toLocaleString('en-IN') : '0'}
                          </td>
                          <td style={{ padding: '6px', color: 'var(--silver-dim)', textAlign: 'right' }}>
                            {s.orders ? s.orders.toLocaleString('en-IN') : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
                </div>{/* close RIGHT COLUMN */}
              </div>{/* close TWO-COLUMN ROW */}

              {/* Daily RMS Summary Card — full width below both columns */}
              <div className="feature-slot" style={{ border: '1px solid var(--border)', background: 'var(--bg-raised)', textAlign: 'left', display: 'flex', flexDirection: 'column', gap: '8px', padding: '12px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border)', paddingBottom: '6px' }}>
                  <span style={{ fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: '12px', letterSpacing: '0.05em' }}>
                    Today's risk summary
                  </span>
                  <span className="status-pulse-dot" style={{ background: 'var(--gold-bright)', boxShadow: '0 0 8px var(--gold-bright)' }} />
                </div>
                {tickerLoadingRms ? (
                  <p style={{ fontFamily: 'var(--font-ui)', fontSize: '11px', color: 'var(--silver-dim)' }}>Syncing your account…</p>
                ) : tickerRmsSummary ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', fontFamily: 'var(--font-data)', fontSize: '11px', color: 'var(--silver-bright)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span>Trades placed today</span>
                      <span style={{ fontWeight: 'bold', color: 'var(--silver-bright)' }}>{tickerRmsSummary.trades_placed}</span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span>Risk used today</span>
                      <span style={{
                        fontWeight: 'bold',
                        color: tickerRmsSummary.drawdown_consumed_pct > 3.0 ? 'var(--negative)' : 'var(--positive)'
                      }}>
                        {tickerRmsSummary.drawdown_consumed_pct.toFixed(2)}%
                      </span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span>Remaining allocation</span>
                      <span style={{ fontWeight: 'bold', color: 'var(--gold-bright)' }}>
                        ${tickerRmsSummary.remaining_capital.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </span>
                    </div>
                  </div>
                ) : (
                  <p style={{ fontFamily: 'var(--font-ui)', fontSize: '11px', color: 'var(--silver-dim)' }}>We can't load your summary right now.</p>
                )}
              </div>

            </div>
          </div>
        )}

        {/* ── TAB: ORDER BOOK (execution ticket + live depth ladder, split out of Ticker) ── */}
        {tab === 'orderbook' && (
          <div style={{
            position: 'absolute', inset: 0, zIndex: 2,
            display: 'flex', flexDirection: 'column', gap: '14px',
            padding: '20px', overflowY: 'auto', background: 'transparent'
          }}>
            <div style={{ width: '100%', maxWidth: '1100px', margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '14px' }}>

              {activeSymbol && tickerQuote && tickerQuote.currentPrice ? (
                <div className="dashboard-grid">

                  {/* Chart Panel — lg:col-span-8 lg:row-span-2 in code.html */}
                  <div className="dashboard-panel" style={{ gridColumn: '1 / span 12', minHeight: '340px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 14px', borderBottom: '1px solid var(--border)', background: 'var(--bg-raised)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <span style={{ fontFamily: 'var(--font-data)', fontSize: '17px', fontWeight: 700, color: 'var(--gold)' }}>{activeSymbol}</span>
                        {active && (
                          <span style={{ padding: '2px 7px', background: 'var(--bg-active)', color: 'var(--silver)', fontFamily: 'var(--font-data)', fontSize: '9.5px', borderRadius: '3px' }}>
                            {active.exchange}
                          </span>
                        )}
                        <span className="status-pulse-dot" style={{ background: tickerIsUp ? 'var(--positive)' : 'var(--negative)', boxShadow: `0 0 8px ${tickerIsUp ? 'var(--positive)' : 'var(--negative)'}` }} />
                      </div>
                      <div style={{ display: 'flex', gap: '6px' }}>
                        {['15s', '5m', '15m', '1h', '1d'].map(iv => (
                          <button
                            key={iv}
                            type="button"
                            onClick={() => onIntervalChange && onIntervalChange(iv)}
                            style={{
                              padding: '4px 9px',
                              border: `1px solid ${lockedInterval === iv ? 'var(--gold)' : 'var(--border)'}`,
                              background: lockedInterval === iv ? 'var(--gold-glow)' : 'transparent',
                              color: lockedInterval === iv ? 'var(--gold-bright)' : 'var(--silver-dim)',
                              fontFamily: 'var(--font-data)', fontSize: '10px', cursor: 'pointer'
                            }}
                          >
                            {iv.toUpperCase()}
                          </button>
                        ))}
                      </div>
                    </div>
                    <div style={{
                      flex: 1, position: 'relative', minHeight: '280px',
                      backgroundImage: 'linear-gradient(rgba(255,255,255,0.04) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.04) 1px, transparent 1px)',
                      backgroundSize: '24px 24px'
                    }}>
                      <div style={{ position: 'absolute', top: '14px', left: '14px', display: 'flex', flexDirection: 'column', gap: '3px' }}>
                        <span style={{ fontFamily: 'var(--font-data)', fontSize: '12px' }}><span style={{ color: 'var(--silver-dim)' }}>O:</span> {tickerQuote.currency === 'INR' ? '₹' : '$'}{tickerQuote.open ? tickerQuote.open.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : 'N/A'}</span>
                        <span style={{ fontFamily: 'var(--font-data)', fontSize: '12px' }}><span style={{ color: 'var(--silver-dim)' }}>H:</span> {tickerQuote.currency === 'INR' ? '₹' : '$'}{tickerQuote.dayHigh ? tickerQuote.dayHigh.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : 'N/A'}</span>
                        <span style={{ fontFamily: 'var(--font-data)', fontSize: '12px' }}><span style={{ color: 'var(--silver-dim)' }}>L:</span> {tickerQuote.currency === 'INR' ? '₹' : '$'}{tickerQuote.dayLow ? tickerQuote.dayLow.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : 'N/A'}</span>
                        <span style={{ fontFamily: 'var(--font-data)', fontSize: '12px' }}><span style={{ color: 'var(--silver-dim)' }}>C:</span> <span style={{ color: tickerIsUp ? 'var(--positive)' : 'var(--negative)' }}>{tickerQuote.currency === 'INR' ? '₹' : '$'}{tickerQuote.currentPrice.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span></span>
                      </div>
                      <div style={{ position: 'absolute', bottom: '14px', right: '14px', fontFamily: 'var(--font-data)', fontSize: '9px', color: 'var(--silver-dim)', textAlign: 'right' }}>
                        Live candles render in the Charts tab
                      </div>
                    </div>
                  </div>

                  {/* Execution Panel */}
                  <div className="dashboard-panel" style={{ gridColumn: '1 / span 12' }}>
                    <div className="dashboard-panel-header">EXECUTION</div>
                    <div style={{ padding: '14px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
                      <div className="execution-type-toggle">
                        {(['LIMIT', 'MARKET', 'STOP'] as const).map(t => (
                          <button key={t} type="button" className={execOrderType === t ? 'active' : ''} onClick={() => setExecOrderType(t)}>
                            {t}
                          </button>
                        ))}
                      </div>

                      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                        <div className="execution-field">
                          <span className="field-label">Price</span>
                          <input
                            type="text"
                            value={execOrderType === 'MARKET' ? tickerQuote.currentPrice.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : execPrice}
                            onChange={e => setExecPrice(e.target.value)}
                            readOnly={execOrderType === 'MARKET'}
                          />
                          <span className="field-unit">{tickerQuote.currency === 'INR' ? 'INR' : 'USD'}</span>
                        </div>
                        <div className="execution-field">
                          <span className="field-label">Size</span>
                          <input
                            type="text"
                            placeholder="1"
                            value={execSize}
                            onChange={e => setExecSize(e.target.value)}
                          />
                          <span className="field-unit">QTY</span>
                        </div>
                      </div>

                      <div style={{ display: 'flex', gap: '8px' }}>
                        <button
                          type="button"
                          className="execution-buy-btn"
                          onClick={() => dispatchTickerQuickTrade(
                            'BUY',
                            execOrderType === 'MARKET' ? tickerQuote.currentPrice : parseFloat(execPrice || String(tickerQuote.currentPrice)),
                            parseFloat(execSize || '1')
                          )}
                        >
                          Buy / Long
                        </button>
                        <button
                          type="button"
                          className="execution-sell-btn"
                          onClick={() => dispatchTickerQuickTrade(
                            'SELL',
                            execOrderType === 'MARKET' ? tickerQuote.currentPrice : parseFloat(execPrice || String(tickerQuote.currentPrice)),
                            parseFloat(execSize || '1')
                          )}
                        >
                          Sell / Short
                        </button>
                      </div>
                    </div>
                  </div>

                  {/* Order Book Panel */}
                  <div className="dashboard-panel" style={{ gridColumn: '1 / span 12' }}>
                    <div className="dashboard-panel-header">ORDER BOOK</div>
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', padding: '8px', overflow: 'hidden', gap: '2px' }}>
                      <div style={{ display: 'flex', flexDirection: 'column-reverse', gap: '2px' }}>
                        {tickerSellDepth.slice(0, 5).map((s: any, i: number, arr: any[]) => {
                          const maxQty = Math.max(...arr.map((x: any) => x.quantity), 1);
                          const width = Math.round((s.quantity / maxQty) * 100);
                          return (
                            <div key={i} className="order-book-row">
                              <div className="order-book-depth-bar ask" style={{ width: `${width}%` }} />
                              <span className="px ask" style={{ paddingLeft: '8px' }}>{s.price > 0 ? s.price.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00'}</span>
                              <span className="qty">{s.quantity > 0 ? s.quantity.toLocaleString('en-IN') : '0'}</span>
                              <span className="total" style={{ paddingRight: '8px' }}>{s.quantity > 0 ? `${Math.round(s.quantity / 1000)}k` : '0'}</span>
                            </div>
                          );
                        })}
                      </div>

                      <div className="order-book-spread">
                        <span className="last-price">{tickerQuote.currentPrice.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span>
                        <span className="spread-label">
                          Spread {tickerSellDepth[0] && tickerBuyDepth[0] ? (tickerSellDepth[0].price - tickerBuyDepth[0].price).toFixed(2) : '—'}
                        </span>
                      </div>

                      <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                        {tickerBuyDepth.slice(0, 5).map((b: any, i: number, arr: any[]) => {
                          const maxQty = Math.max(...arr.map((x: any) => x.quantity), 1);
                          const width = Math.round((b.quantity / maxQty) * 100);
                          return (
                            <div key={i} className="order-book-row">
                              <div className="order-book-depth-bar bid" style={{ width: `${width}%` }} />
                              <span className="px bid" style={{ paddingLeft: '8px' }}>{b.price > 0 ? b.price.toLocaleString('en-IN', { minimumFractionDigits: 2 }) : '0.00'}</span>
                              <span className="qty">{b.quantity > 0 ? b.quantity.toLocaleString('en-IN') : '0'}</span>
                              <span className="total" style={{ paddingRight: '8px' }}>{b.quantity > 0 ? `${Math.round(b.quantity / 1000)}k` : '0'}</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </div>

                </div>
              ) : (
                <div className="feature-slot" style={{ flex: 1, display: 'flex', alignItems: 'center', justifySelf: 'center' }}>
                  <p style={{ color: 'var(--silver-dim)', fontSize: '11px', textAlign: 'center' }}>
                    Search or click on an asset inside Ticker to load an order book here.
                  </p>
                </div>
              )}

            </div>
          </div>
        )}

        {/* ── TAB: PAPER TRADING TERMINAL ── */}
        {tab === 'papertrading' && (
          <div style={{
            position: 'absolute', inset: 0, zIndex: 2,
            display: 'flex', flexDirection: 'column', gap: '16px',
            padding: '20px', overflowY: 'auto', background: 'transparent'
          }}>
            
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px' }}>
              <div className="feature-slot" style={{ flex: 1, minWidth: '220px', display: 'flex', flexDirection: 'column', gap: '4px', textAlign: 'left' }}>
                <div style={{ fontSize: '11px', color: 'var(--silver-dim)' }}>Total portfolio value</div>
                <div style={{ fontFamily: 'var(--font-data)', fontSize: '22px', color: 'var(--gold-bright)', fontWeight: 'bold' }}>
                  ${portfolioValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </div>
                <div style={{ display: 'flex', justifySelf: 'flex-start', justifyContent: 'space-between', width: '100%', fontSize: '11px', marginTop: '6px', borderTop: '1px solid var(--border-subtle)', paddingTop: '6px' }}>
                  <span style={{ color: 'var(--silver-dim)' }}>Cash Balance:</span>
                  <span style={{ color: 'var(--silver-bright)' }}>${balance.toLocaleString('en-US', { minimumFractionDigits: 2 })}</span>
                </div>
              </div>

              <div className="feature-slot" style={{ flex: 1, minWidth: '220px', display: 'flex', flexDirection: 'column', gap: '4px', textAlign: 'left' }}>
                <div style={{ fontSize: '11px', color: 'var(--silver-dim)' }}>Unrealized profit / loss</div>
                <div style={{ fontFamily: 'var(--font-data)', fontSize: '22px', color: openPnLVal >= 0 ? 'var(--positive)' : 'var(--negative)', fontWeight: 'bold' }}>
                  {openPnLVal >= 0 ? '+' : ''}${openPnLVal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </div>
                <div style={{ display: 'flex', justifySelf: 'flex-start', justifyContent: 'space-between', width: '100%', fontSize: '11px', marginTop: '6px', borderTop: '1px solid var(--border-subtle)', paddingTop: '6px' }}>
                  <span style={{ color: 'var(--silver-dim)' }}>Leveraged Multiplier:</span>
                  <span style={{ color: 'var(--silver-bright)' }}>Intraday 5x Allowed</span>
                </div>
              </div>

              <div className="feature-slot" style={{ flex: 1.5, minWidth: '320px', display: 'flex', flexDirection: 'column', gap: '8px', textAlign: 'left' }}>
                <div style={{ fontSize: '11px', color: 'var(--silver-dim)', letterSpacing: '0.05em' }}>MANUAL BALANCE REFILL (CAP $100K)</div>
                <form onSubmit={handleTopup} style={{ display: 'flex', gap: '8px' }}>
                  <input 
                    type="number"
                    value={topupAmount}
                    onChange={(e) => setTopupAmount(e.target.value)}
                    placeholder="Refill Sum"
                    style={{
                      flex: 1, background: 'var(--bg-base)', border: '1px solid var(--border)',
                      borderRadius: '4px', color: 'var(--gold-bright)', fontSize: '11px',
                      padding: '8px', outline: 'none', fontFamily: 'var(--font-data)'
                    }}
                  />
                  <button type="submit" className="login-submit-btn" style={{ margin: 0, padding: '8px 16px', width: 'auto', fontSize: '11px' }}>
                    Add funds
                  </button>
                </form>
              </div>
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px' }}>
              
              {/* Order Router Block */}
              <div className="feature-slot" style={{ flex: '1 0 320px', display: 'flex', flexDirection: 'column', gap: '12px', textAlign: 'left', padding: '16px' }}>
                <div style={{ fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: '14px', letterSpacing: '0.05em', borderBottom: '1px solid var(--border)', paddingBottom: '6px' }}>
                  Place an order
                </div>
                
                <form onSubmit={handleExecuteTradeForm} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                    <label style={{ fontSize: '10px', color: 'var(--silver-dim)' }}>Symbol</label>
                    <input 
                      type="text"
                      value={tradeSymbol}
                      onChange={(e) => setTradeSymbol(e.target.value.toUpperCase())}
                      placeholder="e.g. NSE:RELIANCE or NASDAQ:AAPL"
                      required
                      style={{
                        background: 'var(--bg-base)', border: '1px solid var(--border)',
                        borderRadius: '4px', color: 'var(--silver-bright)', fontSize: '11px',
                        padding: '8px', fontFamily: 'var(--font-data)', outline: 'none'
                      }}
                    />
                  </div>

                  <div style={{ display: 'flex', gap: '10px' }}>
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      <label style={{ fontSize: '10px', color: 'var(--silver-dim)' }}>Quantity</label>
                      <input 
                        type="number"
                        min="1"
                        value={orderQty}
                        onChange={(e) => setOrderQty(Math.max(1, parseInt(e.target.value) || 0))}
                        required
                        style={{
                          background: 'var(--bg-base)', border: '1px solid var(--border)',
                          borderRadius: '4px', color: 'var(--silver-bright)', fontSize: '11px',
                          padding: '8px', fontFamily: 'var(--font-data)', outline: 'none'
                        }}
                      />
                    </div>
                    
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      <label style={{ fontSize: '10px', color: 'var(--silver-dim)' }}>Lot size (multiplier)</label>
                      <input 
                        type="number"
                        min="1"
                        value={lotMultiplier}
                        onChange={(e) => setLotMultiplier(Math.max(1, parseInt(e.target.value) || 1))}
                        required
                        style={{
                          background: 'var(--bg-base)', border: '1px solid var(--border)',
                          borderRadius: '4px', color: 'var(--silver-bright)', fontSize: '11px',
                          padding: '8px', fontFamily: 'var(--font-data)', outline: 'none'
                        }}
                      />
                    </div>
                  </div>

                  <div style={{ display: 'flex', gap: '10px' }}>
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      <label style={{ fontSize: '10px', color: 'var(--silver-dim)' }}>Leverage</label>
                      <select
                        value={leverage}
                        onChange={(e) => setLeverage(parseInt(e.target.value))}
                        style={{
                          background: 'var(--bg-base)', border: '1px solid var(--border-subtle)',
                          borderRadius: '4px', color: 'var(--gold-bright)', fontSize: '11px',
                          padding: '8px', outline: 'none', cursor: 'pointer', fontFamily: 'var(--font-data)'
                        }}
                      >
                        <option value={1}>Delivery (1x Cash)</option>
                        <option value={5}>Intraday Margin (5x Leverage)</option>
                        <option value={10}>Intraday Margin Plus (10x Leverage)</option>
                      </select>
                    </div>

                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      <label style={{ fontSize: '10px', color: 'var(--silver-dim)' }}>Order type</label>
                      <select
                        value={orderType}
                        onChange={(e) => setOrderType(e.target.value as 'MARKET' | 'LIMIT')}
                        style={{
                          background: 'var(--bg-base)', border: '1px solid var(--border-subtle)',
                          borderRadius: '4px', color: 'var(--gold-bright)', fontSize: '11px',
                          padding: '8px', outline: 'none', cursor: 'pointer', fontFamily: 'var(--font-data)'
                        }}
                      >
                        <option value="MARKET">Market</option>
                        <option value="LIMIT">Limit</option>
                      </select>
                    </div>
                  </div>

                  {orderType === 'LIMIT' && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      <label style={{ fontSize: '10px', color: 'var(--silver-dim)' }}>Limit price</label>
                      <input 
                        type="number"
                        step="0.01"
                        value={orderPrice}
                        onChange={(e) => setOrderPrice(e.target.value)}
                        placeholder="Price target"
                        required={orderType === 'LIMIT'}
                        style={{
                          background: 'var(--bg-base)', border: '1px solid var(--border)',
                          borderRadius: '4px', color: 'var(--silver-bright)', fontSize: '11px',
                          padding: '8px', fontFamily: 'var(--font-data)', outline: 'none'
                        }}
                      />
                    </div>
                  )}

                  <div style={{
                    background: 'rgba(201, 168, 76, 0.03)', border: '1px solid var(--border)',
                    borderRadius: '4px', padding: '8px 12px', fontSize: '10px',
                    display: 'flex', flexDirection: 'column', gap: '4px'
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: 'var(--silver-dim)' }}>Notional Exposure:</span>
                      <span style={{ fontFamily: 'var(--font-data)', color: 'var(--silver-bright)' }}>${currentNotionalExposure.toLocaleString('en-US', { minimumFractionDigits: 2 })}</span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: 'var(--silver-dim)' }}>Margin Required:</span>
                      <span style={{ fontFamily: 'var(--font-data)', color: 'var(--gold-bright)', fontWeight: 'bold' }}>${estimatedRequiredMargin.toLocaleString('en-US', { minimumFractionDigits: 2 })}</span>
                    </div>
                  </div>

                  <div style={{ display: 'flex', gap: '10px', marginTop: '4px' }}>
                    <button
                      type="button"
                      onClick={() => setOrderSide('BUY')}
                      style={{
                        flex: 1, padding: '10px', borderRadius: '4px',
                        background: orderSide === 'BUY' ? 'var(--positive)' : 'var(--bg-base)',
                        color: orderSide === 'BUY' ? 'var(--bg-base)' : 'var(--silver)',
                        border: orderSide === 'BUY' ? '1px solid var(--positive)' : '1px solid var(--border)',
                        fontWeight: 'bold', cursor: 'pointer', fontFamily: 'var(--font-ui)', letterSpacing: '0.05em'
                      }}
                    >
                      Buy
                    </button>
                    <button
                      type="button"
                      onClick={() => setOrderSide('SELL')}
                      style={{
                        flex: 1, padding: '10px', borderRadius: '4px',
                        background: orderSide === 'SELL' ? 'var(--negative)' : 'var(--bg-base)',
                        color: orderSide === 'SELL' ? 'var(--bg-base)' : 'var(--silver)',
                        border: orderSide === 'SELL' ? '1px solid var(--negative)' : '1px solid var(--border)',
                        fontWeight: 'bold', cursor: 'pointer', fontFamily: 'var(--font-ui)', letterSpacing: '0.05em'
                      }}
                    >
                      Sell
                    </button>
                  </div>

                  <button
                    type="submit"
                    className="login-submit-btn"
                    style={{ marginTop: '8px', background: 'var(--gold)', color: 'var(--bg-base)', fontWeight: 'bold' }}
                  >
                    Place order
                  </button>
                </form>
              </div>

              {/* Central Fast-Acting Execution Panel */}
              <div className="feature-slot" style={{ flex: '2 1 500px', display: 'flex', flexDirection: 'column', gap: '12px', textAlign: 'left', padding: '16px' }}>
                <div style={{ fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: '14px', letterSpacing: '0.05em', borderBottom: '1px solid var(--border)', paddingBottom: '6px' }}>
                  Open positions
                </div>

                <div style={{ margin: '0 -16px', border: '1px solid var(--border)', borderRadius: '8px', overflow: 'hidden' }}>
                  {positions.length === 0 ? (
                    <div className="list-empty-state">
                      No active positions running. Use the Order Router above to execute a paper trade.
                    </div>
                  ) : (
                    positions.map((pos, idx) => {
                      const isLong = pos.type === 'LONG';
                      const rawPnL = isLong 
                        ? (pos.currentPrice - pos.entryPrice) * pos.qty * pos.lotSize
                        : (pos.entryPrice - pos.currentPrice) * pos.qty * pos.lotSize;
                      const pctPnL = (isLong
                        ? (pos.currentPrice - pos.entryPrice) / pos.entryPrice
                        : (pos.entryPrice - pos.currentPrice) / pos.entryPrice) * 100;
                      const isProfit = rawPnL >= 0;

                      return (
                        <div className="list-row" key={pos.id || idx}>
                          <div className={`list-row-thumb ${isLong ? 'positive' : 'negative'}`}>
                            {pos.symbol.slice(0, 2).toUpperCase()}
                          </div>
                          <div className="list-row-body">
                            <span className="list-row-title">
                              {pos.symbol}
                              <span style={{ color: 'var(--silver-dim)', fontWeight: 400, fontSize: '10px', marginLeft: '6px', fontFamily: 'var(--font-data)' }}>
                                {pos.id}
                              </span>
                            </span>
                            <span className="list-row-sub">
                              <span className={`status-chip ${isLong ? 'long' : 'short'}`}>{pos.type}</span>
                              <span>{pos.qty} × {pos.lotSize} lot · avg ${pos.entryPrice.toLocaleString('en-US', { minimumFractionDigits: 2 })}</span>
                            </span>
                          </div>
                          <div className="list-row-trailing">
                            <span className={`list-row-value ${isProfit ? 'positive' : 'negative'}`}>
                              {isProfit ? '+' : ''}${rawPnL.toLocaleString('en-US', { minimumFractionDigits: 2 })}
                            </span>
                            <span className="list-row-sub" style={{ margin: 0 }}>
                              {isProfit ? '+' : ''}{pctPnL.toFixed(2)}% · LTP ${pos.currentPrice.toLocaleString('en-US', { minimumFractionDigits: 2 })}
                            </span>
                            <button type="button" className="list-row-close-btn" onClick={() => handleSquareOff(pos)}>
                              Close
                            </button>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>

            </div>

          </div>
        )}

        {/* ── TAB: PERFORMANCE & PNL ── */}
        {tab === 'pnltracker' && (
          <div style={{
            position: 'absolute', inset: 0, zIndex: 2,
            display: 'flex', flexDirection: 'column', gap: '16px',
            padding: '20px', overflowY: 'auto', background: 'transparent'
          }}>

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px' }}>
              
              {/* Win/Loss Ratio Pictograph */}
              <div className="feature-slot" style={{ flex: 1, minWidth: '220px', display: 'flex', alignItems: 'center', gap: '16px', textAlign: 'left', padding: '16px' }}>
                <div style={{ width: '80px', height: '80px', position: 'relative' }}>
                  <svg width="80" height="80" viewBox="0 0 36 36">
                    <circle cx="18" cy="18" r="15.915" fill="none" stroke="rgba(244, 63, 94, 0.15)" strokeWidth="3" />
                    <circle cx="18" cy="18" r="15.915" fill="none" stroke="var(--positive)" strokeWidth="3.2"
                      strokeDasharray={`${winRate} ${100 - winRate}`} strokeDashoffset="25" />
                  </svg>
                  <div style={{
                    position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontFamily: 'var(--font-data)', fontSize: '11px', fontWeight: 'bold', color: 'var(--positive)'
                  }}>
                    {winRate.toFixed(0)}%
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: '10px', color: 'var(--silver-dim)' }}>Win ratio</div>
                  <div style={{ fontSize: '14px', fontWeight: 'bold', color: 'var(--silver-bright)', marginTop: '2px' }}>
                    {summaryStats.wins} Wins / {summaryStats.losses} Losses
                  </div>
                  <div style={{ fontSize: '10px', color: 'var(--positive)', marginTop: '2px' }}>
                    Profitable edge verified
                  </div>
                </div>
              </div>

              {/* Profit Distribution Performance Graph */}
              <div className="feature-slot" style={{ flex: 1.5, minWidth: '320px', display: 'flex', flexDirection: 'column', gap: '8px', textAlign: 'left', padding: '16px' }}>
                <div style={{ fontSize: '10px', color: 'var(--silver-dim)' }}>Recent trades</div>
                <div style={{ flex: 1, height: '60px', display: 'flex', alignItems: 'flex-end', gap: '6px' }}>
                  {allTrades.slice(-10).map((t, idx) => {
                    const maxVal = Math.max(...allTrades.map(p => Math.abs(p.pnl))) || 1;
                    const heightPct = Math.min(100, Math.max(10, (Math.abs(t.pnl) / maxVal) * 100));
                    const isWin = t.pnl >= 0;
                    return (
                      <div key={idx} style={{ flex: 1, position: 'relative', height: '100%', display: 'flex', flexDirection: 'column', justifyContent: isWin ? 'flex-end' : 'flex-start' }}>
                        <div style={{
                          width: '100%',
                          height: `${heightPct / 2}%`,
                          background: isWin ? 'var(--positive)' : 'var(--negative)',
                          borderRadius: '2px',
                          alignSelf: isWin ? 'flex-end' : 'flex-start',
                          opacity: 0.85
                        }} title={`$${t.pnl}`} />
                      </div>
                    );
                  })}
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '9px', color: 'var(--silver-dim)' }}>
                  <span>Past Trades Trajectory (Oldest → Latest)</span>
                  <span style={{ color: 'var(--gold-bright)' }}>LTP Based Valuation</span>
                </div>
              </div>

            </div>

            <div className="feature-slot" style={{ display: 'flex', flexDirection: 'column', gap: '12px', textAlign: 'left', padding: '16px' }}>
              <div style={{ fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: '13px', letterSpacing: '0.05em', borderBottom: '1px solid var(--border)', paddingBottom: '6px' }}>
                Trade history
              </div>
              <div style={{ margin: '0 -16px', border: '1px solid var(--border)', borderRadius: '8px', overflow: 'hidden' }}>
                {allTrades.length === 0 ? (
                  <div className="list-empty-state">No mock trade logs recorded yet.</div>
                ) : (
                  (() => {
                    // Group trades into day buckets, most recent first — same
                    // "Today / Yesterday / N days ago" pattern as the reference History screen.
                    const sorted = [...allTrades].sort((a, b) => b.timestamp - a.timestamp);
                    const groups: { label: string; items: typeof sorted }[] = [];
                    sorted.forEach(t => {
                      const label = dayBucketLabel(t.timestamp);
                      const g = groups.find(g => g.label === label);
                      if (g) g.items.push(t); else groups.push({ label, items: [t] });
                    });
                    return groups.map(group => (
                      <React.Fragment key={group.label}>
                        <div className="list-section-header">{group.label}</div>
                        {group.items.map((t, idx) => {
                          const isProfit = t.pnl >= 0;
                          const isOpen = t.status === 'OPEN';
                          return (
                            <div className="list-row" key={t.id || idx}>
                              <div className={`list-row-thumb ${t.type === 'LONG' ? 'positive' : 'negative'}`}>
                                {t.symbol.slice(0, 2).toUpperCase()}
                              </div>
                              <div className="list-row-body">
                                <span className="list-row-title">
                                  {t.symbol}
                                  <span style={{ color: 'var(--silver-dim)', fontWeight: 400, fontSize: '10px', marginLeft: '6px', fontFamily: 'var(--font-data)' }}>
                                    {t.id}
                                  </span>
                                </span>
                                <span className="list-row-sub">
                                  <span className={`status-chip ${t.type === 'LONG' ? 'long' : 'short'}`}>{t.type}</span>
                                  <span className={`status-chip ${isOpen ? 'open' : 'closed'}`}>{t.status}</span>
                                  <span>qty {t.qty.toFixed(2)} · {new Date(t.timestamp).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}</span>
                                </span>
                              </div>
                              <div className="list-row-trailing">
                                <span className={`list-row-value ${isProfit ? 'positive' : 'negative'}`}>
                                  {isProfit ? '+' : ''}{t.pnl.toFixed(2)}
                                </span>
                                <span className="list-row-sub" style={{ margin: 0 }}>
                                  {t.entryPrice.toFixed(2)} → {t.exitPrice.toFixed(2)} ({t.points >= 0 ? '+' : ''}{t.points.toFixed(2)} pts)
                                </span>
                              </div>
                            </div>
                          );
                        })}
                      </React.Fragment>
                    ));
                  })()
                )}
              </div>

              {allTrades.length > 0 && (
                <div style={{ 
                  marginTop: '12px', borderTop: '1px solid var(--border)', paddingTop: '12px',
                  display: 'flex', flexWrap: 'wrap', gap: '16px', fontSize: '11px', fontFamily: 'var(--font-data)'
                }}>
                  <div>
                    <span style={{ color: 'var(--silver-dim)' }}>Total Pnl: </span>
                    <span style={{ fontWeight: 'bold', color: summaryStats.totalPnl >= 0 ? 'var(--positive)' : 'var(--negative)' }}>
                      ₹{summaryStats.totalPnl.toFixed(2)}
                    </span>
                  </div>
                  <div style={{ color: 'var(--border)' }}>|</div>
                  <div>
                    <span style={{ color: 'var(--silver-dim)' }}>Avg Pnl: </span>
                    <span style={{ fontWeight: 'bold', color: 'var(--gold-bright)' }}>
                      ₹{summaryStats.avgPnl.toFixed(2)}
                    </span>
                  </div>
                  <div style={{ color: 'var(--border)' }}>|</div>
                  <div>
                    <span style={{ color: 'var(--silver-dim)' }}>Wins: </span>
                    <span style={{ fontWeight: 'bold', color: 'var(--positive)' }}>{summaryStats.wins}</span>
                  </div>
                  <div style={{ color: 'var(--border)' }}>|</div>
                  <div>
                    <span style={{ color: 'var(--silver-dim)' }}>Losses: </span>
                    <span style={{ fontWeight: 'bold', color: 'var(--negative)' }}>{summaryStats.losses}</span>
                  </div>
                  <div style={{ color: 'var(--border)' }}>|</div>
                  <div>
                    <span style={{ color: 'var(--silver-dim)' }}>Total Trades: </span>
                    <span style={{ fontWeight: 'bold', color: 'var(--silver-bright)' }}>{summaryStats.total}</span>
                  </div>
                </div>
              )}
            </div>

          </div>
        )}

        {/* ── TAB: DEDICATED STRATEGY MATRIX ── */}
        {tab === 'strategy' && (() => {
          const signalObj = localStrategySignal as any;
          const intraRun = signalObj?.detailed_runs?.intraday_mean_reversion;
          const swingRun = signalObj?.detailed_runs?.swing_momentum;
          const valueRun = signalObj?.detailed_runs?.long_term_value;
          const intraInd = intraRun?.indicators || {};
          const swingInd = swingRun?.indicators || {};
          const valueInd = valueRun?.indicators || {};

          // Full strategy list mirrors backend INTERVAL_TO_HORIZON / INTERVAL_LABEL (market.py)
          // Grouped by the horizon each interval auto-routes to, for a classic <optgroup> dropdown.
          const STRATEGY_GROUPS: { horizon: string; groupLabel: string; options: { value: string; label: string }[] }[] = [
            {
              horizon: 'intraday',
              groupLabel: 'Intraday Mean Reversion',
              options: [
                { value: '1m',  label: '1-Minute (Scalping / HFT)' },
                { value: '3m',  label: '3-Minute (Micro Scalping)' },
                { value: '5m',  label: '5-Minute (Short Scalping)' },
                { value: '10m', label: '10-Minute (Intraday)' },
                { value: '15m', label: '15-Minute (Intraday Mean Reversion)' },
                { value: '30m', label: '30-Minute (Intraday Swing)' },
              ],
            },
            {
              horizon: 'swing',
              groupLabel: 'Swing Momentum',
              options: [
                { value: '1h', label: '1-Hour (Momentum Swing)' },
                { value: '4h', label: '4-Hour (Multi-Session Swing)' },
                { value: '1d', label: 'Daily (Swing Momentum)' },
              ],
            },
            {
              horizon: 'investment',
              groupLabel: 'Long-Term Value',
              options: [
                { value: '1w',  label: 'Weekly (Position / Long-Term Value)' },
                { value: '1mo', label: 'Monthly (Long-Term Investment)' },
              ],
            },
          ];

          const selectedStrategyLabel = STRATEGY_GROUPS
            .flatMap(g => g.options)
            .find(o => o.value === selectedInterval)?.label || selectedInterval;

          const signalUpper = signalObj?.signal?.toUpperCase();
          const consensusClass = signalUpper === 'BUY' ? 'buy' : signalUpper === 'SHORT' || signalUpper === 'SELL' ? 'sell' : 'hold';

          const hasAlerts = signalObj?.arbitration && (
            (signalObj.arbitration.discount_factor < 1) ||
            (signalObj.arbitration.data_integrity && !signalObj.arbitration.data_integrity.complete) ||
            signalObj.arbitration.band_walk?.walking ||
            (signalObj.arbitration.timeframe_alignment && signalObj.arbitration.timeframe_alignment.disagreeing.length > 0)
          );

          return (
            <div className="strategy-page" style={{ background: 'transparent', position: 'absolute', inset: 0, zIndex: 2, overflowY: 'auto' }}>

              {/* ── INSTRUMENT STRIP: symbol, live status, strategy selector ── */}
              <div className="instrument-strip">
                <div className="instrument-cell grow">
                  <span className="live-dot-row"><span className="live-dot" />Currently analyzing</span>
                  <div className="instrument-symbol-row">
                    <span className="instrument-symbol-name">
                      {active ? active.name : activeSymbol ? activeSymbol.split(':')[1] : 'Select an asset'}
                    </span>
                    <span className="instrument-symbol-code">
                      {active ? `${active.exchange}:${active.symbol}` : activeSymbol || '—'}
                    </span>
                  </div>
                </div>

                <div className="strategy-select-cell">
                  <span className="instrument-label">
                    Strategy
                    {lockedInterval && (
                      <span className="strategy-select-lock"> · 🔒 locked to {lockedInterval.toUpperCase()}</span>
                    )}
                  </span>
                  <select
                    className="strategy-select"
                    value={selectedInterval}
                    onChange={(e) => handleIntervalChange(e.target.value)}
                    disabled={!!lockedInterval}
                    title={lockedInterval ? `Locked to the ticker's ${lockedInterval.toUpperCase()} timeframe — change it in the Live Telemetry panel to switch strategies.` : undefined}
                  >
                    {STRATEGY_GROUPS.map(group => (
                      <optgroup key={group.horizon} label={group.groupLabel}>
                        {group.options.map(opt => {
                          const isDisabled = !!lockedInterval && opt.value !== lockedInterval;
                          return (
                            <option key={opt.value} value={opt.value} disabled={isDisabled}>
                              {opt.label}{isDisabled ? ' (switch ticker timeframe to use)' : ''}
                            </option>
                          );
                        })}
                      </optgroup>
                    ))}
                  </select>
                </div>

                {signalObj && (
                  <div className="instrument-cell" style={{ justifyContent: 'center' }}>
                    <span className="instrument-label">Consensus</span>
                    <span className={`verdict-tag ${consensusClass}`}>
                      {signalObj?.signal?.toUpperCase() || 'HOLD'}
                    </span>
                  </div>
                )}
              </div>

              {/* ── MODEL ROUTING LINE ── */}
              {signalObj && (
                <div className="model-route">
                  <span>MODEL</span>
                  <span className="model-name">
                    {signalObj?.model_name || signalObj?.horizon?.toUpperCase() + ' MODEL'}
                  </span>
                  <span className="arrow">→</span>
                  <span className="timeframe">
                    {signalObj?.timeframe_label || selectedStrategyLabel}
                  </span>
                  {strategyLoading && <span className="fetching">⟳ fetching analysis…</span>}
                </div>
              )}

              {/* ── AT-A-GLANCE METRIC STRIP ── */}
              {signalObj && (
                <div className="panel">
                  <div className="metric-strip">
                    <div className="metric-cell">
                      <span className="metric-label">Price vs {signalObj?.trend_label || 'SMA-200 (1D)'}</span>
                      <span className={`metric-value ${signalObj?.is_bullish_trend ? 'positive' : 'negative'}`}>
                        {signalObj?.is_bullish_trend ? '▲ ABOVE' : '▼ BELOW'}
                      </span>
                    </div>

                    <div className="metric-cell">
                      <span className="metric-label">Sentiment</span>
                      <span className="metric-value neutral">{signalObj?.sentiment_score}%</span>
                      <div className="metric-bar-track">
                        <div
                          className="metric-bar-fill"
                          style={{
                            width: `${Math.max(0, Math.min(100, signalObj?.sentiment_score))}%`,
                            background: signalObj?.sentiment_score >= 50 ? 'var(--positive)' : 'var(--negative)',
                          }}
                        />
                      </div>
                    </div>

                    <div className="metric-cell">
                      <span className="metric-label">Active strategy</span>
                      <span className="metric-value neutral">
                        {signalObj?.horizon ? signalObj.horizon.toUpperCase() : '—'}
                      </span>
                    </div>

                    {signalObj?.arbitration && (
                      <div className="metric-cell">
                        <span className="metric-label">Regime-adj. confidence</span>
                        <div className="metric-confidence-row">
                          <span className={`metric-value ${signalObj.arbitration.discount_factor < 1 ? 'neutral' : 'positive'}`}>
                            {Math.round(signalObj.arbitration.adjusted_confidence * 100)}%
                          </span>
                          {signalObj.arbitration.discount_factor < 1 && (
                            <span className="metric-confidence-raw">
                              {Math.round(signalObj.arbitration.raw_confidence * 100)}%
                            </span>
                          )}
                        </div>
                        <span className="metric-sub">{signalObj.arbitration.regime.replace(/_/g, ' ')}</span>
                      </div>
                    )}
                  </div>

                  {/* AI Reasoning */}
                  <div className="panel-body" style={{ borderTop: '1px solid var(--border)' }}>
                    {strategyLoading ? (
                      <div className="strategy-empty-state">Computing strategy reasoning from live indicator data…</div>
                    ) : signalObj?.ai_reasoning ? (
                      <p className="rationale-block">{signalObj.ai_reasoning}</p>
                    ) : (
                      <p className="rationale-block">{strategyExplanationText}</p>
                    )}
                  </div>
                </div>
              )}

              {!signalObj && (
                <div className="panel">
                  <div className="strategy-empty-state">Select an active stock in Angel One to generate AI-powered analysis.</div>
                </div>
              )}

              {/* ── ALERT LEDGER — arbitration / data gap / band walk / timeframe alignment ── */}
              {hasAlerts && (
                <div className="ledger">
                  {signalObj.arbitration.discount_factor < 1 && (
                    <div className="ledger-row warn">
                      <span className="ledger-tag">⚠ Arbitration</span>
                      <span className="ledger-body">{signalObj.arbitration.reasoning}</span>
                    </div>
                  )}

                  {signalObj.arbitration.data_integrity && !signalObj.arbitration.data_integrity.complete && (
                    <div className="ledger-row danger">
                      <span className="ledger-tag">⚠ Data gap</span>
                      <span className="ledger-body">{signalObj.arbitration.data_integrity.reasoning}</span>
                    </div>
                  )}

                  {signalObj.arbitration.band_walk?.walking && (
                    <div className="ledger-row info">
                      <span className="ledger-tag">Band walk · {signalObj.arbitration.band_walk.consecutive_candles}C</span>
                      <span className="ledger-body">{signalObj.arbitration.band_walk.reasoning}</span>
                    </div>
                  )}

                  {signalObj.arbitration.timeframe_alignment && signalObj.arbitration.timeframe_alignment.disagreeing.length > 0 && (
                    <div className="ledger-row info">
                      <span className="ledger-tag">TF align · {Math.round(signalObj.arbitration.timeframe_alignment.alignment_pct)}%</span>
                      <span className="ledger-body">{signalObj.arbitration.timeframe_alignment.reasoning}</span>
                    </div>
                  )}
                </div>
              )}

              {signalObj?.arbitration?.position_size_multiplier < 1 && (
                <div className="strategy-size-row">
                  <span>Suggested size multiplier</span>
                  <span className="strategy-size-value num">
                    {Math.round(signalObj.arbitration.position_size_multiplier * 100)}%
                  </span>
                  <span>of normal clip</span>
                </div>
              )}

              {/* ── TECHNICAL PARAMETERS ── */}
              {signalObj?.detailed_runs && (
                <div className="panel">
                  <div className="panel-head">
                    <span className="panel-eyebrow">Technical parameters · {signalObj?.horizon?.toUpperCase() || 'ACTIVE'} model</span>
                    <span className="tech-params-active-badge">● Active</span>
                  </div>

                  {signalObj?.horizon === 'intraday' && (
                    <table className="tech-params-table">
                      <tbody>
                        <tr><td>Signal</td><td className="accent">{intraRun?.action || 'N/A'}</td></tr>
                        <tr><td>Last Price</td><td>₹{intraInd?.last_price?.toFixed(2) || '—'}</td></tr>
                        <tr><td>VWAP</td><td>₹{intraInd?.vwap?.toFixed(2) || '—'}</td></tr>
                        <tr><td>BB Band</td><td>₹{intraInd?.lower_band?.toFixed(0) || '—'} – ₹{intraInd?.upper_band?.toFixed(0) || '—'}</td></tr>
                        <tr><td>Confidence</td><td className="accent">{intraRun?.confidence !== undefined ? `${(intraRun.confidence * 100).toFixed(0)}%` : '—'}</td></tr>
                      </tbody>
                    </table>
                  )}

                  {signalObj?.horizon === 'swing' && (
                    <table className="tech-params-table">
                      <tbody>
                        <tr><td>Signal</td><td className="accent">{swingRun?.action || 'N/A'}</td></tr>
                        <tr><td>EMA-12 / EMA-26</td><td>₹{swingInd?.ema_fast?.toFixed(1) || '—'} / ₹{swingInd?.ema_slow?.toFixed(1) || '—'}</td></tr>
                        <tr><td>MACD</td><td className={swingInd?.macd >= 0 ? 'positive' : 'negative'}>{swingInd?.macd?.toFixed(3) || '—'}</td></tr>
                        <tr><td>Signal Line</td><td>{swingInd?.signal_line?.toFixed(3) || '—'}</td></tr>
                        <tr><td>Confidence</td><td className="accent">{swingRun?.confidence !== undefined ? `${(swingRun.confidence * 100).toFixed(0)}%` : '—'}</td></tr>
                      </tbody>
                    </table>
                  )}

                  {signalObj?.horizon === 'investment' && (
                    <table className="tech-params-table">
                      <tbody>
                        <tr><td>Signal</td><td className="accent">{valueRun?.action || 'N/A'}</td></tr>
                        <tr><td>Weekly EMA-200</td><td>₹{valueInd?.weekly_ema_200?.toFixed(2) || '—'}</td></tr>
                        <tr><td>Weekly RSI-14</td><td>{valueInd?.weekly_rsi_14?.toFixed(2) || '—'}</td></tr>
                        <tr><td>Allocation Cap</td><td>{valueRun?.allocation_ratio !== undefined ? `${(valueRun.allocation_ratio * 100).toFixed(0)}%` : '—'}</td></tr>
                      </tbody>
                    </table>
                  )}
                </div>
              )}

              {/* ── NEWS SENTIMENT ── */}
              <div className="panel">
                <div className="panel-head">
                  <span className="panel-eyebrow">News sentiment</span>
                  {sentimentData && (
                    <span className={`sentiment-verdict-badge ${sentimentData.verdict === 'positive' ? 'positive' : sentimentData.verdict === 'negative' ? 'negative' : 'neutral'}`}>
                      {sentimentData.magnitude || sentimentData.verdict} · score {sentimentData.sentiment_score}
                    </span>
                  )}
                </div>

                {sentimentData && (sentimentData.confidence !== undefined || sentimentData.momentum) && (
                  <div className="sentiment-meta-row">
                    {sentimentData.confidence !== undefined && (
                      <span>Confidence <span className="value">{sentimentData.confidence}%</span></span>
                    )}
                    {sentimentData.momentum && (
                      <span>
                        Momentum{' '}
                        <span
                          className="value"
                          style={{
                            color: sentimentData.momentum === 'IMPROVING' ? 'var(--positive)'
                                 : sentimentData.momentum === 'DETERIORATING' ? 'var(--negative)'
                                 : 'var(--silver-bright)'
                          }}
                        >
                          {sentimentData.momentum ? sentimentData.momentum.charAt(0) + sentimentData.momentum.slice(1).toLowerCase() : ''}
                        </span>
                      </span>
                    )}
                    {sentimentData.article_count !== undefined && (
                      <span>Articles <span className="value">{sentimentData.article_count}</span></span>
                    )}
                    {sentimentData.source_diversity !== undefined && (
                      <span>Sources <span className="value">{sentimentData.source_diversity}</span></span>
                    )}
                    {sentimentData.engine && (
                      <span>Engine <span className="value">{sentimentData.engine}</span></span>
                    )}
                  </div>
                )}

                {sentimentLoading ? (
                  <div className="strategy-empty-state">Processing intelligence channels…</div>
                ) : sentimentData?.reasoning ? (
                  <div className="sentiment-quote">{sentimentData.reasoning}</div>
                ) : sentimentData?.headlines?.length > 0 ? (
                  <div className="sentiment-quote">{sentimentData.headlines.join(' • ')}</div>
                ) : (
                  <div className="strategy-empty-state">
                    No active sentiment archive references available for {tradeSymbol || 'this symbol'}.
                  </div>
                )}
              </div>

            </div>
          );
        })()}

        {/* ── TAB: INDICATORS TAB (own workspace — backend: services/indicator_engine.py) ── */}
        {tab === 'indicators' && (() => {
          const tfPayload = indicatorsData?.[indicatorInterval];
          const summary = tfPayload?.summary;
          const allIndicators: Record<string, any> = tfPayload?.indicators || {};
          
          // Normalize both dynamic properties to avoid character mismatch issues
          const categoryIndicators = Object.entries(allIndicators)
            .filter(([, v]: [string, any]) => {
              if (!v?.category) return false;
              const normCategory = v.category.toLowerCase().trim().replace(/[\s_-]+/g, '_');
              const targetCategory = indicatorCategory.toLowerCase().trim().replace(/[\s_-]+/g, '_');
              return normCategory === targetCategory || (normCategory === 'priceaction' && targetCategory === 'price_action');
            })
            .sort(([a], [b]) => a.localeCompare(b));
            
          const categorySentiment = buildCategorySentimentBreakdown(allIndicators);

          return (
            <div style={{
              position: 'absolute', inset: 0, zIndex: 2,
              display: 'flex', flexDirection: 'column', gap: '14px',
              padding: '20px', overflowY: 'auto', background: 'transparent'
            }}>

              {/* ── HEADER: Active Symbol + Manual Run ── */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
                <div style={{
                  flex: 1, display: 'flex', alignItems: 'center', gap: '10px',
                  background: 'rgba(212,175,55,0.05)', border: '1px solid rgba(212,175,55,0.2)',
                  borderRadius: '4px', padding: '10px 14px'
                }}>
                  <span style={{ fontSize: '16px', fontWeight: 'bold', color: 'var(--gold-bright)', fontFamily: 'var(--font-display)', letterSpacing: '0.05em' }}>
                    {active ? active.name : activeSymbol ? activeSymbol.split(':')[1] : 'SELECT AN ASSET'}
                  </span>
                  <span style={{ fontSize: '11px', color: 'var(--silver-dim)', fontFamily: 'var(--font-data)' }}>
                    ({active ? `${active.exchange}:${active.symbol}` : activeSymbol || '—'})
                  </span>
                  <span style={{ marginLeft: 'auto', fontSize: '10px', color: 'var(--gold-bright)', background: 'rgba(212,175,55,0.1)', padding: '3px 8px', borderRadius: '3px', fontWeight: 'bold' }}>
                    ~100 INDICATORS · 7 CATEGORIES · 20 TIMEFRAMES
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => fetchIndicatorSweep(indicatorInterval, true)}
                  disabled={indicatorsLoading}
                  style={{
                    padding: '10px 16px', borderRadius: '4px', fontSize: '11px', fontWeight: 'bold',
                    letterSpacing: '0.05em', cursor: indicatorsLoading ? 'not-allowed' : 'pointer',
                    background: 'rgba(212,175,55,0.1)', border: '1px solid var(--gold)',
                    color: 'var(--gold-bright)', opacity: indicatorsLoading ? 0.6 : 1,
                    fontFamily: 'var(--font-display)',
                  }}
                >
                  {indicatorsLoading ? '⟳ RUNNING...' : '⟳ RUN SWEEP'}
                </button>
              </div>

              {/* ── ERROR BANNER — the actual failure reason, not a silent empty panel ── */}
              {indicatorsError && (
                <div style={{
                  padding: '12px 14px', borderRadius: '4px', fontSize: '11px', fontFamily: 'var(--font-data)',
                  background: 'rgba(244,63,94,0.08)', border: '1px solid var(--negative)', color: 'var(--negative)',
                  lineHeight: '1.6',
                }}>
                  <strong>Couldn't load indicators:</strong> {indicatorsError}
                  <div style={{ color: 'var(--silver-dim)', marginTop: '4px' }}>
                    Check: backend running at {BACKEND} · routers/indicators.py registered in main.py ·
                    an active symbol is selected · browser DevTools (F12) → Network tab for the full response.
                  </div>
                </div>
              )}

              {/* ── INDICATOR ENGINE PANEL ── */}
              <div className="feature-slot" style={{ display: 'flex', flexDirection: 'column', gap: '12px', padding: '16px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px', borderBottom: '1px solid var(--border)', paddingBottom: '6px' }}>
                  <span style={{ fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: '12px', letterSpacing: '0.05em' }}>
                    INDICATOR ENGINE {tfPayload ? `· ${tfPayload.count} SIGNALS` : ''}
                  </span>
                  {summary && (
                    <div style={{ display: 'flex', gap: '10px', alignItems: 'center', fontSize: '10px', fontFamily: 'var(--font-data)' }}>
                      <span style={{ color: 'var(--positive)' }}>▲ {summary.bullish_count}</span>
                      <span style={{ color: 'var(--negative)' }}>▼ {summary.bearish_count}</span>
                      <span style={{ color: 'var(--silver-dim)' }}>● {summary.neutral_count}</span>
                      <span style={{
                        padding: '3px 9px', borderRadius: '3px', fontWeight: 'bold', letterSpacing: '0.04em',
                        background: summary.net_lean === 'BULLISH' ? 'rgba(16,185,129,0.12)' : summary.net_lean === 'BEARISH' ? 'rgba(244,63,94,0.12)' : 'rgba(255,255,255,0.04)',
                        color: indicatorSignalColor(summary.net_lean),
                      }}>
                        NET LEAN: {summary.net_lean}
                      </span>
                    </div>
                  )}
                </div>

                {/* Timeframe grid */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '9px', color: 'var(--silver-dim)', letterSpacing: '0.06em' }}>
                    TIMEFRAME
                    {lockedInterval && (
                      <span style={{ color: 'var(--gold)', fontWeight: 'bold' }}>
                        🔒 LOCKED TO TICKER TIMEFRAME ({lockedInterval.toUpperCase()})
                      </span>
                    )}
                    {INDICATOR_APPROXIMATED_INTERVALS.has(indicatorInterval) && (
                      <span style={{ color: 'var(--silver-dim)', fontStyle: 'italic' }}>
                        (approximated from 1m candles — no true sub-minute data exists upstream)
                      </span>
                    )}
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px' }}>
                    {TICKER_INTERVAL_GROUPS.map(group => (
                      <div key={group.groupLabel} style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                        <span style={{ fontSize: '8px', color: 'var(--silver-dim)', letterSpacing: '0.08em', paddingLeft: '2px' }}>
                          {group.groupLabel}
                        </span>
                        <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                          {group.options.map(t => {
                            const isSelected = indicatorInterval === t.value;
                            const isDisabled = !!lockedInterval && t.value !== lockedInterval;
                            return (
                              <button
                                key={t.value}
                                type="button"
                                disabled={isDisabled}
                                onClick={() => !isDisabled && setIndicatorInterval(t.value)}
                                title={isDisabled ? `Locked to the ticker's ${lockedInterval!.toUpperCase()} timeframe — change it in the Live Telemetry panel to switch here.` : undefined}
                                style={{
                                  padding: '5px 10px', borderRadius: '3px', fontSize: '10px', fontWeight: 'bold',
                                  letterSpacing: '0.04em', cursor: isDisabled ? 'not-allowed' : 'pointer',
                                  background: isSelected ? 'rgba(212,175,55,0.15)' : 'transparent',
                                  color: isSelected ? 'var(--gold-bright)' : isDisabled ? 'var(--border-bright)' : 'var(--silver-dim)',
                                  border: isSelected ? '1px solid var(--gold)' : '1px solid var(--border-subtle)',
                                  opacity: isDisabled ? 0.5 : 1,
                                }}
                              >
                                {t.label}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Category pills */}
                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                  {INDICATOR_CATEGORY_ORDER.map(cat => (
                    <button
                      key={cat}
                      type="button"
                      onClick={() => setIndicatorCategory(cat)}
                      style={{
                        padding: '5px 12px', borderRadius: '3px', fontSize: '10px', fontWeight: 'bold',
                        letterSpacing: '0.03em', cursor: 'pointer',
                        background: indicatorCategory === cat ? 'rgba(255,255,255,0.06)' : 'transparent',
                        color: indicatorCategory === cat ? 'var(--silver-bright)' : 'var(--silver-dim)',
                        border: '1px solid var(--border-subtle)',
                      }}
                    >
                      {INDICATOR_CATEGORY_LABELS[cat]}
                    </button>
                  ))}
                </div>

                {/* Indicator rows for the selected category/timeframe */}
                {indicatorsLoading ? (
                  <div style={{ padding: '24px', textAlign: 'center', color: 'var(--silver-dim)', fontStyle: 'italic', fontSize: '12px' }}>
                    Running the indicator sweep across timeframes...
                  </div>
                ) : categoryIndicators.length > 0 ? (
                  <div style={{ maxHeight: '520px', overflowY: 'auto', border: '1px solid var(--border-subtle)', borderRadius: '4px' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '11px', fontFamily: 'var(--font-data)' }}>
                      <thead>
                        <tr style={{ position: 'sticky', top: 0, background: 'var(--bg-base)', zIndex: 1 }}>
                          <th style={{ textAlign: 'left', padding: '7px 10px', fontSize: '9px', color: 'var(--silver-dim)', letterSpacing: '0.05em', fontWeight: 'normal', borderBottom: '1px solid var(--border-subtle)' }}>
                            INDICATOR
                          </th>
                          <th style={{ textAlign: 'right', padding: '7px 10px', fontSize: '9px', color: 'var(--silver-dim)', letterSpacing: '0.05em', fontWeight: 'normal', borderBottom: '1px solid var(--border-subtle)' }}>
                            VALUE
                          </th>
                          <th style={{ textAlign: 'center', padding: '7px 10px', fontSize: '9px', color: 'var(--silver-dim)', letterSpacing: '0.05em', fontWeight: 'normal', borderBottom: '1px solid var(--border-subtle)' }}>
                            SIGNAL
                          </th>
                          <th style={{ width: '24px', padding: '7px 4px', borderBottom: '1px solid var(--border-subtle)' }} />
                          <th style={{ width: '24px', padding: '7px 4px', borderBottom: '1px solid var(--border-subtle)' }} />
                        </tr>
                      </thead>
                      <tbody>
                        {categoryIndicators.map(([name, res]: [string, any]) => {
                          const spec = getConfigurableSpec(name);
                          const customResult = customIndicatorResults[name];
                          const displayRes = customResult || res;
                          const isSettingsOpen = openSettingsFor === name;
                          const isCustomLoading = !!customIndicatorLoading[name];

                          return (
                            <React.Fragment key={name}>
                              <tr style={{ background: 'rgba(255,255,255,0.015)' }}>
                                <td style={{ padding: '7px 10px', borderBottom: '1px solid var(--border-subtle)', color: 'var(--silver-dim)' }}>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                    {formatIndicatorName(name)}
                                    {customResult && (
                                      <span style={{ fontSize: '8px', color: 'var(--gold)', border: '1px solid var(--gold)', borderRadius: '2px', padding: '0 4px' }}>
                                        CUSTOM
                                      </span>
                                    )}
                                  </div>
                                </td>
                                <td style={{ padding: '7px 10px', borderBottom: '1px solid var(--border-subtle)', textAlign: 'right', color: 'var(--silver-bright)', whiteSpace: 'nowrap' }}>
                                  {isCustomLoading ? '...' : formatIndicatorValue(displayRes?.value)}
                                </td>
                                <td style={{ padding: '7px 10px', borderBottom: '1px solid var(--border-subtle)', textAlign: 'center' }}>
                                  <span style={{
                                    display: 'inline-block', minWidth: '86px', padding: '2px 6px', borderRadius: '3px',
                                    fontSize: '9px', fontWeight: 'bold', letterSpacing: '0.03em',
                                    color: indicatorSignalColor(displayRes?.signal),
                                    background: 'rgba(255,255,255,0.03)',
                                  }}>
                                    {displayRes?.signal || '—'}
                                  </span>
                                </td>
                                <td style={{ padding: '7px 4px', borderBottom: '1px solid var(--border-subtle)', textAlign: 'center' }}>
                                  {spec && (
                                    <button
                                      type="button"
                                      title="Adjust settings"
                                      onClick={() => {
                                        if (isSettingsOpen) { setOpenSettingsFor(null); return; }
                                        setSettingsDraft(customResult?.params || spec.params);
                                        setOpenSettingsFor(name);
                                      }}
                                      style={{
                                        width: '22px', height: '22px', borderRadius: '3px', cursor: 'pointer',
                                        background: isSettingsOpen ? 'rgba(212,175,55,0.15)' : 'transparent',
                                        border: '1px solid var(--border-subtle)', color: 'var(--silver-dim)',
                                        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px',
                                      }}
                                    >
                                      ⚙
                                    </button>
                                  )}
                                </td>
                                <td style={{ padding: '7px 4px', borderBottom: '1px solid var(--border-subtle)', textAlign: 'center' }}>
                                  {customResult && (
                                    <button
                                      type="button"
                                      title="Reset to default"
                                      onClick={() => setCustomIndicatorResults(prev => {
                                        const next = { ...prev };
                                        delete next[name];
                                        return next;
                                      })}
                                      style={{
                                        width: '22px', height: '22px', borderRadius: '3px', cursor: 'pointer',
                                        background: 'transparent', border: '1px solid var(--border-subtle)',
                                        color: 'var(--negative)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: '11px',
                                      }}
                                    >
                                      ×
                                    </button>
                                  )}
                                </td>
                              </tr>

                              {isSettingsOpen && spec && (
                                <tr>
                                  <td colSpan={5} style={{ padding: 0, borderBottom: '1px solid var(--border-subtle)' }}>
                                    <div style={{
                                      display: 'flex', flexWrap: 'wrap', gap: '10px', alignItems: 'flex-end',
                                      padding: '10px', background: 'rgba(0,0,0,0.1)',
                                    }}>
                                      {Object.keys(spec.params).map(paramKey => (
                                        <label key={paramKey} style={{ display: 'flex', flexDirection: 'column', gap: '3px', fontSize: '9px', color: 'var(--silver-dim)', letterSpacing: '0.04em' }}>
                                          {paramKey.toUpperCase()}
                                          <input
                                            type="number"
                                            step={paramKey === 'stddev' ? '0.1' : '1'}
                                            value={settingsDraft[paramKey] ?? spec.params[paramKey]}
                                            onChange={e => setSettingsDraft(prev => ({ ...prev, [paramKey]: parseFloat(e.target.value) }))}
                                            style={{
                                              width: '64px', padding: '5px 7px', borderRadius: '3px', fontSize: '11px',
                                              background: 'var(--bg-raised)', border: '1px solid var(--border-subtle)',
                                              color: 'var(--gold-bright)', fontFamily: 'var(--font-data)', outline: 'none',
                                            }}
                                          />
                                        </label>
                                      ))}
                                      <button
                                        type="button"
                                        onClick={() => applyCustomIndicatorSettings(name, spec.type, settingsDraft)}
                                        style={{
                                          padding: '7px 14px', borderRadius: '3px', fontSize: '10px', fontWeight: 'bold',
                                          letterSpacing: '0.04em', cursor: 'pointer', background: 'rgba(212,175,55,0.15)',
                                          border: '1px solid var(--gold)', color: 'var(--gold-bright)',
                                        }}
                                      >
                                        APPLY
                                      </button>
                                    </div>
                                  </td>
                                </tr>
                              )}
                            </React.Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div style={{ padding: '20px', textAlign: 'center', color: 'var(--silver-dim)', fontStyle: 'italic', fontSize: '11px' }}>
                    {!tradeSymbol
                      ? 'Select an active stock in Angel One to run the indicator sweep.'
                      : indicatorsData
                        ? `No ${INDICATOR_CATEGORY_LABELS[indicatorCategory]} data for this timeframe yet.`
                        : 'No data yet — click RUN SWEEP above, or check the error banner if one appeared.'}
                  </div>
                )}
              </div>

              {/* ── VISUAL OVERVIEW ── */}
              {tfPayload && (() => {
                const totalBulls = categorySentiment.reduce((a, c) => a + c.bulls, 0);
                const totalBears = categorySentiment.reduce((a, c) => a + c.bears, 0);
                const totalNeutrals = categorySentiment.reduce((a, c) => a + c.neutrals, 0);
                const totalAll = Math.max(1, totalBulls + totalBears + totalNeutrals);
                const r = 54, C = 2 * Math.PI * r;
                const pieSegments = [
                  { len: (totalBulls / totalAll) * C, color: 'var(--positive)' },
                  { len: (totalNeutrals / totalAll) * C, color: 'var(--silver-dim)' },
                  { len: (totalBears / totalAll) * C, color: 'var(--negative)' },
                ];
                let cumulative = 0;

                return (
                  <div className="feature-slot" style={{ display: 'flex', flexDirection: 'column', gap: '14px', padding: '16px' }}>
                    <div style={{ fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: '12px', letterSpacing: '0.05em', borderBottom: '1px solid var(--border)', paddingBottom: '6px' }}>
                      VISUAL OVERVIEW · {INDICATOR_TIMEFRAME_LABELS[indicatorInterval] || indicatorInterval.toUpperCase()}
                    </div>

                    <div style={{ display: 'flex', gap: '24px', flexWrap: 'wrap', alignItems: 'flex-start' }}>

                      {/* Pie chart */}
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                        <svg width="150" height="150" viewBox="0 0 150 150">
                          {pieSegments.map((seg, i) => {
                            const el = (
                              <circle
                                key={i}
                                cx="75" cy="75" r={r}
                                fill="none" stroke={seg.color} strokeWidth="18"
                                strokeDasharray={`${seg.len} ${C - seg.len}`}
                                strokeDashoffset={-cumulative}
                                transform="rotate(-90 75 75)"
                              />
                            );
                            cumulative += seg.len;
                            return el;
                          })}
                          <text x="75" y="71" textAnchor="middle" fontSize="20" fontWeight="bold"
                                fill={indicatorSignalColor(summary?.net_lean)} fontFamily="var(--font-data)">
                            {totalAll ? `${Math.round((totalBulls / totalAll) * 100)}%` : '—'}
                          </text>
                          <text x="75" y="87" textAnchor="middle" fontSize="9" letterSpacing="0.05em"
                                fill="var(--silver-dim)" fontFamily="var(--font-data)">
                            BULLISH
                          </text>
                        </svg>
                        <div style={{ display: 'flex', gap: '12px', fontSize: '9px', fontFamily: 'var(--font-data)' }}>
                          <span style={{ color: 'var(--positive)' }}>● {totalBulls} Bullish</span>
                          <span style={{ color: 'var(--silver-dim)' }}>● {totalNeutrals} Neutral</span>
                          <span style={{ color: 'var(--negative)' }}>● {totalBears} Bearish</span>
                        </div>
                      </div>

                      {/* Bar chart */}
                      <div style={{ flex: 1, minWidth: '260px', display: 'flex', flexDirection: 'column', gap: '9px' }}>
                        <div style={{ fontSize: '9px', color: 'var(--silver-dim)', letterSpacing: '0.05em', marginBottom: '2px' }}>
                          SIGNALS BY CATEGORY · BEARISH ← → BULLISH
                        </div>
                        {categorySentiment.map(cat => {
                          const netScore = cat.total ? ((cat.bulls - cat.bears) / cat.total) * 100 : 0;
                          const leftPct = netScore < 0 ? Math.min(100, Math.abs(netScore)) : 0;
                          const rightPct = netScore > 0 ? Math.min(100, netScore) : 0;
                          return (
                            <div key={cat.category} style={{ display: 'grid', gridTemplateColumns: '92px 1fr 34px', alignItems: 'center', gap: '8px' }}>
                              <span style={{ fontSize: '10px', color: 'var(--silver-dim)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {INDICATOR_CATEGORY_LABELS[cat.category]}
                              </span>
                              <div style={{ position: 'relative', display: 'flex', height: '10px', background: 'rgba(255,255,255,0.05)', borderRadius: '2px', overflow: 'hidden' }}>
                                <div style={{ width: '50%', display: 'flex', justifyContent: 'flex-end' }}>
                                  <div style={{ width: `${leftPct / 2}%`, background: 'var(--negative)', opacity: 0.85 }} />
                                </div>
                                <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: '1px', background: 'var(--border)' }} />
                                <div style={{ width: '50%', display: 'flex', justifyContent: 'flex-start' }}>
                                  <div style={{ width: `${rightPct / 2}%`, background: 'var(--positive)', opacity: 0.85 }} />
                                </div>
                              </div>
                              <span style={{ fontSize: '9px', color: 'var(--silver-dim)', textAlign: 'right', fontFamily: 'var(--font-data)' }}>
                                {cat.total}
                              </span>
                            </div>
                          );
                        })}
                      </div>

                    </div>
                  </div>
                );
              })()}

              {/* ── INDICATOR INTERSECTION MAP ── */}
              {tfPayload && (() => {
                const scoreOfSignal = (signal?: string): number => {
                  if (!signal) return 0;
                  const uSig = signal.toUpperCase();
                  if (INDICATOR_POSITIVE_SIGNALS.has(uSig)) return 1;
                  if (INDICATOR_NEGATIVE_SIGNALS.has(uSig)) return -1;
                  return 0;
                };

                const groupedByCategory: Record<string, { name: string; score: number; signal: string }[]> = {};
                INDICATOR_CATEGORY_ORDER.forEach(cat => { groupedByCategory[cat] = []; });
                
                Object.entries(allIndicators).forEach(([name, res]: [string, any]) => {
                  if (!res) return;
                  const catKey = (res.category || '').toLowerCase().trim().replace(/[\s_-]+/g, '_');
                  const normalizedCat = catKey === 'priceaction' ? 'price_action' : catKey;
                  
                  if (groupedByCategory[normalizedCat]) {
                    groupedByCategory[normalizedCat].push({ 
                      name, 
                      score: scoreOfSignal(res.signal), 
                      signal: res.signal || '—' 
                    });
                  }
                });
                
                Object.values(groupedByCategory).forEach(list => list.sort((a, b) => a.name.localeCompare(b.name)));

                const maxSlots = Math.max(1, ...Object.values(groupedByCategory).map(l => l.length));
                const waveformPoints: WaveformPoint[] = INDICATOR_CATEGORY_ORDER.flatMap((cat, categoryIndex) =>
                  groupedByCategory[cat].map((ind, slotIndex) => ({
                    name: ind.name, 
                    category: cat, 
                    categoryIndex, 
                    slotIndex, 
                    score: ind.score, 
                    signal: ind.signal,
                  }))
                );
                
                const categoryLabels = INDICATOR_CATEGORY_ORDER.map(cat => INDICATOR_CATEGORY_LABELS[cat]);

                const mapTotalBulls = categorySentiment.reduce((a, c) => a + c.bulls, 0);
                const mapTotalBears = categorySentiment.reduce((a, c) => a + c.bears, 0);
                const mapTotalNeutrals = categorySentiment.reduce((a, c) => a + c.neutrals, 0);
                const mapTotalAll = Math.max(1, mapTotalBulls + mapTotalBears + mapTotalNeutrals);
                const overallScore = mapTotalAll ? (mapTotalBulls - mapTotalBears) / mapTotalAll : 0;
                const overallLabel = overallScore > 0.15 ? 'Buy zone' : overallScore < -0.15 ? 'Sell zone' : 'Hold zone';
                const overallColor = overallScore > 0.15 ? 'var(--positive)' : overallScore < -0.15 ? 'var(--negative)' : 'var(--gold)';

                // Ground the zone badge in the same numbers already driving it,
                // rather than leaving "BUY ZONE" unexplained: name the specific
                // categories with the largest net bull/bear tilt so the label
                // traces back to real per-category counts, not a fabricated story.
                const rankedByNet = [...categorySentiment]
                  .filter(c => c.total > 0)
                  .sort((a, b) => Math.abs(b.bulls - b.bears) - Math.abs(a.bulls - a.bears));
                const topDrivers = rankedByNet.slice(0, 2).map(c => {
                  const net = c.bulls - c.bears;
                  const label = INDICATOR_CATEGORY_LABELS[c.category] || c.category;
                  return `${label} (${c.bulls}▲/${c.bears}▼)`;
                });
                const zoneReasoning = mapTotalAll > 1
                  ? `${mapTotalBulls} of ${mapTotalBulls + mapTotalBears + mapTotalNeutrals} indicators bullish, ${mapTotalBears} bearish — led by ${topDrivers.join(' and ')}.`
                  : '';

                return (
                  <div className="feature-slot" style={{ display: 'flex', flexDirection: 'column', gap: '10px', padding: '16px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '10px', borderBottom: '1px solid var(--border)', paddingBottom: '6px' }}>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                        <span style={{ fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: '12px', letterSpacing: '0.05em' }}>
                          3D INDICATOR INTERSECTION MAP
                        </span>
                        {zoneReasoning && (
                          <span style={{ fontSize: '9.5px', fontFamily: 'var(--font-data)', color: 'var(--silver-dim)' }}>
                            {zoneReasoning}
                          </span>
                        )}
                      </div>
                      <span style={{
                        padding: '3px 9px', borderRadius: '3px', fontWeight: 'bold', fontSize: '10px', letterSpacing: '0.04em',
                        background: 'rgba(255,255,255,0.04)', color: overallColor,
                      }}>
                        {overallLabel}
                      </span>
                    </div>

                    <IndicatorWaveform3D points={waveformPoints} categoryLabels={categoryLabels} maxSlots={maxSlots} height={380} theme={theme === 'dark' ? 'dark' : 'light'} />

                    <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', fontSize: '9px', fontFamily: 'var(--font-data)', color: 'var(--silver-dim)' }}>
                      <span>🖱 drag to orbit · scroll to zoom · click a point to pin its explanation</span>
                      <span>▲ column color = indicator category · brighter = stronger bullish</span>
                      <span>▼ darker of that same color = stronger bearish</span>
                      <span>● sitting on the white plane = hold / no clear read</span>
                    </div>
                  </div>
                );
              })()}

              {/* ── COMPOSITE PLANE ANALYSIS ──
                  Same underlying waveformPoints/categoryLabels/maxSlots as the
                  3D map above, but flattened: every category's plane drawn on
                  one shared overlay chart, then reduced by analysisEngine's
                  weighted math into one composite score, an agreement donut,
                  and a plain-language readout of which plane is driving the
                  call and which one is fighting it. */}
              {tfPayload && (() => {
                const scoreOfSignal = (signal?: string): number => {
                  if (!signal) return 0;
                  const uSig = signal.toUpperCase();
                  if (INDICATOR_POSITIVE_SIGNALS.has(uSig)) return 1;
                  if (INDICATOR_NEGATIVE_SIGNALS.has(uSig)) return -1;
                  return 0;
                };

                const groupedByCategory: Record<string, { name: string; score: number; signal: string }[]> = {};
                INDICATOR_CATEGORY_ORDER.forEach(cat => { groupedByCategory[cat] = []; });

                Object.entries(allIndicators).forEach(([name, res]: [string, any]) => {
                  if (!res) return;
                  const catKey = (res.category || '').toLowerCase().trim().replace(/[\s_-]+/g, '_');
                  const normalizedCat = catKey === 'priceaction' ? 'price_action' : catKey;

                  if (groupedByCategory[normalizedCat]) {
                    groupedByCategory[normalizedCat].push({
                      name,
                      score: scoreOfSignal(res.signal),
                      signal: res.signal || '—'
                    });
                  }
                });

                Object.values(groupedByCategory).forEach(list => list.sort((a, b) => a.name.localeCompare(b.name)));

                const maxSlots = Math.max(1, ...Object.values(groupedByCategory).map(l => l.length));
                const waveformPoints: WaveformPoint[] = INDICATOR_CATEGORY_ORDER.flatMap((cat, categoryIndex) =>
                  groupedByCategory[cat].map((ind, slotIndex) => ({
                    name: ind.name,
                    category: cat,
                    categoryIndex,
                    slotIndex,
                    score: ind.score,
                    signal: ind.signal,
                  }))
                );

                const categoryLabels = INDICATOR_CATEGORY_ORDER.map(cat => INDICATOR_CATEGORY_LABELS[cat]);

                return (
                  <>
                    <div className="feature-slot" style={{ display: 'flex', flexDirection: 'column', gap: '10px', padding: '16px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px', borderBottom: '1px solid var(--border)', paddingBottom: '6px' }}>
                        <span style={{ fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: '12px', letterSpacing: '0.05em' }}>
                          COMPOSITE PLANE ANALYSIS · {INDICATOR_TIMEFRAME_LABELS[indicatorInterval] || indicatorInterval.toUpperCase()}
                        </span>
                        <span style={{ fontSize: '9px', color: 'var(--silver-dim)' }}>
                          every category plane overlapped into one composite
                        </span>
                      </div>

                      <PlaneOverlayAnalysis points={waveformPoints} categoryLabels={categoryLabels} maxSlots={maxSlots} height={420} />
                    </div>

                    {/* ── SIGNAL INTELLIGENCE ──
                        Consensus weight bars, market regime, conflict detector,
                        agreement matrix, and FOR/AGAINST explainability -- all
                        derived from the exact same waveformPoints/categoryLabels
                        the two panels above already use, so all three views can
                        never disagree with each other. */}
                    <div className="feature-slot" style={{ display: 'flex', flexDirection: 'column', gap: '10px', padding: '16px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px', borderBottom: '1px solid var(--border)', paddingBottom: '6px' }}>
                        <span style={{ fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: '12px', letterSpacing: '0.05em' }}>
                          SIGNAL INTELLIGENCE · {INDICATOR_TIMEFRAME_LABELS[indicatorInterval] || indicatorInterval.toUpperCase()}
                        </span>
                        <span style={{ fontSize: '9px', color: 'var(--silver-dim)' }}>
                          consensus weighting · regime · conflict · agreement matrix · explainability
                        </span>
                      </div>

                      <SignalIntelligencePanel points={waveformPoints} categoryLabels={categoryLabels} />
                    </div>
                  </>
                );
              })()}

            </div>
          );
        })()}

      </div>
    </div>
  );
}