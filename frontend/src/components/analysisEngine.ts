// analysisEngine.ts
//
// The math layer behind the "Composite Plane Analysis" panel, extended with
// the institutional-style features from the Aladdin/Bloomberg feature list:
//
//   1. Each category is one "plane" -- reduce it to a mean score (-1..1)
//      and an evidence weight (how many indicators back it).
//   2. Overlap all planes into one composite score: a weight-averaged mean
//      of the per-category means (categories with more indicators behind
//      them pull the composite harder -- that's the literal "overlap").
//   3. Measure how much the planes agree with each other in two ways:
//        - dispersion: weighted standard deviation of the per-category
//          means around the composite (tight cluster = planes agree;
//          wide spread = planes are fighting each other).
//        - agreementRatio: the fraction of the *evidence* (not just the
//          category count) that sits on the same side as the composite.
//   4. Turn (compositeScore, dispersion, agreementRatio) into a plain
//      conviction tier and a BUY/SELL/HOLD verdict, plus point out which
//      single category is driving the read the hardest and which one is
//      most internally conflicted.
//
//   NEW in this version (all still pure functions of the same
//   (categoryIndex, score) points -- nothing indicator-specific):
//   5. computeAgreementMatrix   -> pairwise category-vs-category agreement,
//                                  the "Signal Agreement Matrix" feature.
//   6. detectMarketRegime       -> TRENDING / RANGING / VOLATILE / BREAKOUT /
//                                  CONFLICTED read from trend + volatility +
//                                  dispersion, the "Regime Detection" feature.
//   7. buildExplainability      -> ranks individual indicators by how much
//                                  weight they contribute FOR vs AGAINST the
//                                  overall verdict, the "AI Explainability" feature.
//   8. computeConflictDetector  -> single conflict % + plain-language main
//                                  cause, the "AI Conflict Detector" feature.
//   9. computeTimeframeConsensus-> folds several timeframes' verdicts into one
//                                  consensus %, the "Timeframe Consensus" feature.
//
// Nothing in here is indicator-specific -- it only ever looks at
// (categoryIndex, score) pairs, so it stays correct no matter how many
// categories or indicators are wired in upstream.
import { WaveformPoint } from './IndicatorWaveform3D';

export interface CategoryPlaneStats {
  categoryIndex: number;
  label: string;
  mean: number;   // -1..1, average score across this category's indicators
  n: number;      // indicator count backing this plane
  bulls: number;
  bears: number;
  holds: number;
  weight: number; // n / total n across all categories -- this plane's share of the evidence
  verdict: 'BULLISH' | 'BEARISH' | 'HOLD';
}

export interface CompositeAnalysis {
  categories: CategoryPlaneStats[];
  compositeScore: number;   // weighted mean of all category means, -1..1
  dispersion: number;       // weighted std-dev of category means around the composite -- how much the planes disagree
  agreementRatio: number;   // 0..1, share of weighted evidence agreeing with the composite's direction
  convictionTier: 'HIGH' | 'MODERATE' | 'LOW';
  overallVerdict: 'BUY' | 'SELL' | 'HOLD';
  dominantCategory: CategoryPlaneStats | null;       // biggest contributor to the composite (|mean| * weight)
  mostConflictedCategory: CategoryPlaneStats | null; // plane most evenly split between its own bulls & bears
  narrative: string;
}

export function computeCompositeAnalysis(points: WaveformPoint[], categoryLabels: string[]): CompositeAnalysis {
  const categories: CategoryPlaneStats[] = categoryLabels.map((label, i) => {
    const inCat = points.filter(p => p.categoryIndex === i);
    const n = inCat.length;
    const bulls = inCat.filter(p => p.score > 0.001).length;
    const bears = inCat.filter(p => p.score < -0.001).length;
    const holds = n - bulls - bears;
    const mean = n ? inCat.reduce((a, p) => a + p.score, 0) / n : 0;
    const verdict: CategoryPlaneStats['verdict'] = mean > 0.1 ? 'BULLISH' : mean < -0.1 ? 'BEARISH' : 'HOLD';
    return { categoryIndex: i, label, mean, n, bulls, bears, holds, weight: 0, verdict };
  });

  const totalN = categories.reduce((a, c) => a + c.n, 0) || 1;
  categories.forEach(c => { c.weight = c.n / totalN; });

  // ── overlap step: fold every plane into one composite surface ──────────
  const compositeScore = categories.reduce((a, c) => a + c.mean * c.weight, 0);

  // ── how well the overlapped planes agree with each other ───────────────
  const variance = categories.reduce((a, c) => a + c.weight * Math.pow(c.mean - compositeScore, 2), 0);
  const dispersion = Math.sqrt(variance);

  const directionSign = compositeScore > 0.05 ? 1 : compositeScore < -0.05 ? -1 : 0;
  const agreementRatio = directionSign === 0
    ? categories.reduce((a, c) => a + (Math.abs(c.mean) <= 0.1 ? c.weight : 0), 0)
    : categories.reduce((a, c) => a + (c.mean * directionSign > 0.05 ? c.weight : 0), 0);

  let convictionTier: CompositeAnalysis['convictionTier'] = 'LOW';
  if (agreementRatio >= 0.7 && Math.abs(compositeScore) >= 0.2) convictionTier = 'HIGH';
  else if (agreementRatio >= 0.5 || Math.abs(compositeScore) >= 0.15) convictionTier = 'MODERATE';

  const overallVerdict: CompositeAnalysis['overallVerdict'] =
    compositeScore > 0.15 ? 'BUY' : compositeScore < -0.15 ? 'SELL' : 'HOLD';

  const withEvidence = categories.filter(c => c.n > 0);
  const dominantCategory = withEvidence.length
    ? withEvidence.reduce((best, c) => (Math.abs(c.mean) * c.weight > Math.abs(best.mean) * best.weight ? c : best))
    : null;

  const conflicted = withEvidence.filter(c => c.n >= 2 && c.bulls > 0 && c.bears > 0);
  const mostConflictedCategory = conflicted.length
    ? conflicted.reduce((worst, c) => {
        const cSplit = Math.min(c.bulls, c.bears) / c.n;
        const worstSplit = Math.min(worst.bulls, worst.bears) / worst.n;
        return cSplit > worstSplit ? c : worst;
      })
    : null;

  const partial = { categories, compositeScore, dispersion, agreementRatio, convictionTier, overallVerdict, dominantCategory, mostConflictedCategory };
  return { ...partial, narrative: buildNarrative(partial) };
}

function buildNarrative(a: Omit<CompositeAnalysis, 'narrative'>): string {
  const pct = (x: number) => `${Math.round(x * 100)}%`;
  const dirWord = a.overallVerdict === 'BUY' ? 'bullish' : a.overallVerdict === 'SELL' ? 'bearish' : 'flat';
  const dominant = a.dominantCategory
    ? `${a.dominantCategory.label} (${a.dominantCategory.mean >= 0 ? '+' : ''}${a.dominantCategory.mean.toFixed(2)})`
    : 'no single category';
  const convictionWord =
    a.convictionTier === 'HIGH' ? 'a tight, high-conviction overlap' :
    a.convictionTier === 'MODERATE' ? 'a moderate overlap with some disagreement' :
    'a loose overlap with meaningful disagreement between planes';
  const conflict = a.mostConflictedCategory
    ? ` ${a.mostConflictedCategory.label} is the most internally split plane (${a.mostConflictedCategory.bulls} bullish vs ${a.mostConflictedCategory.bears} bearish within it).`
    : '';
  return `Overlapping all ${a.categories.length} category planes gives a composite score of ${a.compositeScore.toFixed(2)} (${dirWord}), with ${pct(a.agreementRatio)} of the weighted evidence agreeing on that direction and a dispersion of ${a.dispersion.toFixed(2)} between planes — ${convictionWord}. ${dominant} is contributing the most to the composite.${conflict}`;
}

// ────────────────────────────────────────────────────────────────────────
// 5. SIGNAL AGREEMENT MATRIX
//
// Aladdin-style "how much does the AI agree with itself" grid. For every
// pair of categories, agreement is the cosine-style sign correlation of
// their means, scaled by how confident each side is (|mean|), so two
// planes that are both barely off zero don't read as fully agreeing even
// if their signs match.
// ────────────────────────────────────────────────────────────────────────
export interface AgreementCell {
  rowIndex: number;
  colIndex: number;
  agreement: number; // -1 (full conflict) .. 0 (unrelated/no evidence) .. 1 (full agreement)
}

export function computeAgreementMatrix(categories: CategoryPlaneStats[]): AgreementCell[][] {
  return categories.map((rowCat, r) =>
    categories.map((colCat, c) => {
      if (r === c) return { rowIndex: r, colIndex: c, agreement: rowCat.n ? 1 : 0 };
      if (!rowCat.n || !colCat.n) return { rowIndex: r, colIndex: c, agreement: 0 };
      const sameSign = Math.sign(rowCat.mean) === Math.sign(colCat.mean) || (rowCat.mean === 0 && colCat.mean === 0);
      const strength = Math.min(Math.abs(rowCat.mean), Math.abs(colCat.mean));
      const agreement = (Math.abs(rowCat.mean) < 0.05 || Math.abs(colCat.mean) < 0.05)
        ? 0
        : (sameSign ? 1 : -1) * Math.max(strength, 0.25); // floor so any clear same-direction read still shows as agreement
      return { rowIndex: r, colIndex: c, agreement: Math.max(-1, Math.min(1, agreement)) };
    })
  );
}

// Overall self-agreement score (0..1): average of all off-diagonal cells
// among categories that actually have evidence, rescaled from -1..1.
export function overallSelfAgreement(matrix: AgreementCell[][], categories: CategoryPlaneStats[]): number {
  const cells: number[] = [];
  matrix.forEach((row, r) => row.forEach((cell, c) => {
    if (r === c) return;
    if (!categories[r].n || !categories[c].n) return;
    cells.push(cell.agreement);
  }));
  if (!cells.length) return 0;
  const avg = cells.reduce((a, b) => a + b, 0) / cells.length;
  return (avg + 1) / 2; // 0..1
}

// ────────────────────────────────────────────────────────────────────────
// 6. MARKET REGIME DETECTION
//
// Reads the trend and volatility planes (plus overall dispersion) to
// classify the current read into one of the regimes institutional desks
// actually condition their strategies on.
// ────────────────────────────────────────────────────────────────────────
export type MarketRegime = 'TRENDING' | 'RANGING' | 'VOLATILE' | 'BREAKOUT' | 'CONFLICTED' | 'LOW_LIQUIDITY_DATA';

export interface RegimeResult {
  regime: MarketRegime;
  confidence: number; // 0..1
  reasoning: string;
}

// categoryIndex convention shared with TabbedPanel's INDICATOR_CATEGORY_ORDER:
// 0 trend, 1 momentum, 2 volatility, 3 volume, 4 statistical, 5 price_action, 6 candlestick
export function detectMarketRegime(categories: CategoryPlaneStats[]): RegimeResult {
  const byIndex = (i: number) => categories.find(c => c.categoryIndex === i) || null;
  const trend = byIndex(0);
  const volatility = byIndex(2);
  const volume = byIndex(3);
  const priceAction = byIndex(5);

  const haveEvidence = categories.some(c => c.n > 0);
  if (!haveEvidence) {
    return { regime: 'LOW_LIQUIDITY_DATA', confidence: 0, reasoning: 'No indicators returned evidence for this symbol/timeframe yet.' };
  }

  const trendStrength = trend ? Math.abs(trend.mean) : 0;
  const volSkew = volatility ? volatility.mean : 0; // volatility category bullish-lean == expansion, bearish-lean == compression, by how INDICATOR_POSITIVE_SIGNALS maps for that category
  const dispersion = (() => {
    const withEv = categories.filter(c => c.n > 0);
    if (!withEv.length) return 0;
    const totalW = withEv.reduce((a, c) => a + c.weight, 0) || 1;
    const mean = withEv.reduce((a, c) => a + c.mean * c.weight, 0) / totalW;
    const variance = withEv.reduce((a, c) => a + c.weight * Math.pow(c.mean - mean, 2), 0) / totalW;
    return Math.sqrt(variance);
  })();

  const volumeConfirms = volume ? volume.mean * Math.sign(trend?.mean || 0) > 0.05 : false;
  const priceActionBreak = priceAction ? Math.abs(priceAction.mean) > 0.4 : false;

  // Breakout: strong price action + volume confirmation + volatility expanding
  if (priceActionBreak && volumeConfirms && volSkew > 0.15) {
    return {
      regime: 'BREAKOUT',
      confidence: Math.min(1, (Math.abs(priceAction!.mean) + Math.abs(volSkew)) / 2),
      reasoning: `Price Action (${priceAction!.mean.toFixed(2)}) is moving sharply with Volume confirming and Volatility expanding (${volSkew.toFixed(2)}) — consistent with a breakout regime.`,
    };
  }

  // Trending: strong, consistent trend plane with low overall dispersion
  if (trendStrength > 0.3 && dispersion < 0.35) {
    return {
      regime: 'TRENDING',
      confidence: Math.min(1, trendStrength),
      reasoning: `Trend plane reads ${trend!.mean.toFixed(2)} with low cross-category dispersion (${dispersion.toFixed(2)}) — the other planes are largely rowing in the same direction.`,
    };
  }

  // Volatile / conflicted: high dispersion, no dominant direction
  if (dispersion > 0.5) {
    return {
      regime: 'CONFLICTED',
      confidence: Math.min(1, dispersion),
      reasoning: `Category planes disagree heavily (dispersion ${dispersion.toFixed(2)}) with no dominant trend read — treat any single-category signal with caution here.`,
    };
  }

  if (Math.abs(volSkew) > 0.3 && trendStrength < 0.25) {
    return {
      regime: 'VOLATILE',
      confidence: Math.min(1, Math.abs(volSkew)),
      reasoning: `Volatility plane reads ${volSkew.toFixed(2)} while Trend stays flat (${trendStrength.toFixed(2)}) — price is moving without a clear directional bias.`,
    };
  }

  // RANGING is a fallthrough from two different situations that were
  // previously both reported as "Trend plane is flat", which is only
  // accurate for one of them:
  //   (a) trendStrength really is weak (< 0.3) -- flat is the right word.
  //   (b) trendStrength is actually >= 0.3 (a real directional read) but
  //       dispersion is too high (>= 0.35) for the other planes to
  //       corroborate it -- the trend isn't flat, it's just uncorroborated.
  // Ported 1:1 from services/signal_arbitration.py's detect_market_regime --
  // keep both in sync if you touch this branch.
  if (trendStrength >= 0.3) {
    return {
      regime: 'RANGING',
      confidence: Math.min(1, 1 - dispersion),
      reasoning: `Trend plane reads ${trend!.mean.toFixed(2)} — not flat — but cross-category dispersion (${dispersion.toFixed(2)}) is too high for the other planes to corroborate it, so this doesn't qualify as a clean trending regime.`,
    };
  }

  // Default: ranging (trend genuinely weak)
  return {
    regime: 'RANGING',
    confidence: Math.min(1, 1 - trendStrength),
    reasoning: `Trend plane is flat (${trend ? trend.mean.toFixed(2) : '0.00'}) and volatility is contained (${volSkew.toFixed(2)}) — price looks range-bound rather than directional.`,
  };
}

// ────────────────────────────────────────────────────────────────────────
// 7. AI EXPLAINABILITY
//
// Instead of just "BUY 82%", rank the individual indicators (not
// categories) that pushed the verdict the hardest, split into FOR and
// AGAINST the overall call, each weighted by |score| / category evidence
// share — so a lone indicator inside a thin category doesn't outrank a
// consistent read from a well-populated one.
// ────────────────────────────────────────────────────────────────────────
export interface ExplainabilityItem {
  name: string;
  category: string;
  signal: string;
  score: number;
  contribution: number; // relative weight of this single indicator's pull, 0..1 within its side
  direction: 'FOR' | 'AGAINST';
}

export function buildExplainability(
  points: WaveformPoint[],
  analysis: CompositeAnalysis,
  maxPerSide = 5
): { for: ExplainabilityItem[]; against: ExplainabilityItem[] } {
  const dirSign = analysis.overallVerdict === 'BUY' ? 1 : analysis.overallVerdict === 'SELL' ? -1 : 0;
  const catWeightByIndex = new Map(analysis.categories.map(c => [c.categoryIndex, c.n ? 1 / c.n : 0]));

  const scored = points
    .filter(p => Math.abs(p.score) > 0.001)
    .map(p => {
      const perIndicatorShare = catWeightByIndex.get(p.categoryIndex) || 0;
      const pull = Math.abs(p.score) * perIndicatorShare;
      const side: 'FOR' | 'AGAINST' = dirSign === 0
        ? (Math.abs(p.score) <= 0.1 ? 'FOR' : 'AGAINST') // if verdict is HOLD, "for" = indicators near flat too
        : (p.score * dirSign > 0 ? 'FOR' : 'AGAINST');
      return { name: p.name, category: p.category, signal: p.signal, score: p.score, pull, direction: side };
    });

  const forSide = scored.filter(s => s.direction === 'FOR').sort((a, b) => b.pull - a.pull).slice(0, maxPerSide);
  const againstSide = scored.filter(s => s.direction === 'AGAINST').sort((a, b) => b.pull - a.pull).slice(0, maxPerSide);

  const normalize = (list: typeof scored): ExplainabilityItem[] => {
    const maxPull = Math.max(...list.map(l => l.pull), 0.0001);
    return list.map(l => ({
      name: l.name, category: l.category, signal: l.signal, score: l.score,
      contribution: l.pull / maxPull, direction: l.direction,
    }));
  };

  return { for: normalize(forSide), against: normalize(againstSide) };
}

// ────────────────────────────────────────────────────────────────────────
// 8. AI CONFLICT DETECTOR
//
// A single "how much is the AI fighting itself" percentage plus a
// plain-language main cause, derived from the same dispersion/agreement
// numbers the composite already computes -- no new inputs needed.
// ────────────────────────────────────────────────────────────────────────
export interface ConflictReport {
  conflictPct: number; // 0..100
  mainCause: string;
}

export function computeConflictDetector(analysis: CompositeAnalysis): ConflictReport {
  const conflictPct = Math.round((1 - analysis.agreementRatio) * 100);
  const mainCause = analysis.mostConflictedCategory
    ? `${analysis.mostConflictedCategory.label} is internally split (${analysis.mostConflictedCategory.bulls} bullish vs ${analysis.mostConflictedCategory.bears} bearish)`
    : analysis.dispersion > 0.3
      ? 'Category planes disagree on direction rather than any one plane being split internally'
      : 'No significant conflict detected';
  return { conflictPct, mainCause };
}

// ────────────────────────────────────────────────────────────────────────
// 9. TIMEFRAME CONSENSUS
//
// Folds several timeframes' composite verdicts (e.g. from
// GET /active/indicators/multi-timeframe or repeated calls to
// GET /active/indicators/by-interval) into one consensus percentage --
// the "1m/5m/15m/1H/4H/Daily all say BUY" institutional feature.
// ────────────────────────────────────────────────────────────────────────
export interface TimeframeVerdict {
  timeframe: string;      // display label, e.g. '15m', '1H', 'Daily'
  verdict: 'BUY' | 'SELL' | 'HOLD';
  score: number;          // that timeframe's compositeScore, -1..1
}

export interface TimeframeConsensus {
  entries: TimeframeVerdict[];
  overallVerdict: 'BUY' | 'SELL' | 'HOLD';
  consensusPct: number; // 0..100, share of timeframes agreeing with overallVerdict
  agreeing: string[];
  disagreeing: string[];
}

export function computeTimeframeConsensus(entries: TimeframeVerdict[]): TimeframeConsensus {
  if (!entries.length) {
    return { entries, overallVerdict: 'HOLD', consensusPct: 0, agreeing: [], disagreeing: [] };
  }
  const avgScore = entries.reduce((a, e) => a + e.score, 0) / entries.length;
  const overallVerdict: TimeframeConsensus['overallVerdict'] =
    avgScore > 0.15 ? 'BUY' : avgScore < -0.15 ? 'SELL' : 'HOLD';

  const agreeing = entries.filter(e => e.verdict === overallVerdict).map(e => e.timeframe);
  const disagreeing = entries.filter(e => e.verdict !== overallVerdict).map(e => e.timeframe);
  const consensusPct = Math.round((agreeing.length / entries.length) * 100);

  return { entries, overallVerdict, consensusPct, agreeing, disagreeing };
}