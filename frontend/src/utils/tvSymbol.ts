// src/utils/tvSymbol.ts

const SUFFIX_EXCHANGE: Record<string, string> = {
  '.NS': 'NSE',
  '.BO': 'BSE',
  '.L':  'LSE',
  '.TO': 'TSX',
  '.AX': 'ASX',
  '.HK': 'HKEX',
  '.T':  'TSE',
  '.SS': 'SSE',
  '.SZ': 'SZSE',
  '.DE': 'XETR',
  '.PA': 'EURONEXT',
  '.MI': 'MTA',
};

const NASDAQ_SET = new Set([
  'AAPL','MSFT','GOOGL','GOOG','AMZN','NVDA','META','TSLA','AVGO','COST',
  'NFLX','AMD','INTC','QCOM','TXN','MU','AMAT','LRCX','KLAC','MRVL',
  'ADBE','PYPL','INTU','SBUX','MDLZ','GILD','BKNG','REGN','ISRG','VRTX',
]);

export function toTvSymbol(raw: string): string {
  const ticker = raw.trim().toUpperCase();
  if (ticker.includes(':')) return ticker;

  for (const [suffix, exchange] of Object.entries(SUFFIX_EXCHANGE)) {
    if (ticker.endsWith(suffix.toUpperCase())) {
      const base = ticker.slice(0, -suffix.length);
      return `${exchange}:${base}`;
    }
  }

  if (ticker.startsWith('NIFTY') || ticker === 'SENSEX') return `NSE:${ticker}`;
  if (NASDAQ_SET.has(ticker)) return `NASDAQ:${ticker}`;
  return `NYSE:${ticker}`;
}

export function fromTvSymbol(tvSymbol: string): string {
  return tvSymbol.includes(':') ? tvSymbol.split(':')[1] : tvSymbol;
}