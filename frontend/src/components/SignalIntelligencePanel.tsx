// components/SignalIntelligencePanel.tsx
//
// Institutional-style read-out that sits alongside PlaneOverlayAnalysis /
// IndicatorWaveform3D. Same input contract (WaveformPoint[] + categoryLabels)
// so it drops into TabbedPanel next to the other two panels with zero new
// backend calls for most of it — only the optional multi-timeframe consensus
// strip needs a second payload (see `timeframeEntries` prop), which you
// already have from GET /active/indicators/multi-timeframe or repeated
// GET /active/indicators/by-interval calls.
//
// Renders, top to bottom:
//   1. Multi-Layer Signal Consensus  -- weighted % contribution per category
//   2. Market Regime badge            -- TRENDING / RANGING / VOLATILE / BREAKOUT / CONFLICTED
//   3. AI Conflict Detector           -- single "how much is it fighting itself" %
//   4. Signal Agreement Matrix        -- category x category heatmap
//   5. AI Explainability              -- top indicators FOR vs AGAINST the verdict
//   6. Timeframe Consensus strip      -- only rendered if timeframeEntries is passed
import React, { useMemo, useState } from 'react';
import { WaveformPoint, hexForCategory } from './IndicatorWaveform3D';
import {
  computeCompositeAnalysis,
  computeAgreementMatrix,
  overallSelfAgreement,
  detectMarketRegime,
  buildExplainability,
  computeConflictDetector,
  computeTimeframeConsensus,
  TimeframeVerdict,
} from './analysisEngine';

interface Props {
  points: WaveformPoint[];
  categoryLabels: string[];
  timeframeEntries?: TimeframeVerdict[]; // optional: pass from a multi-timeframe fetch to render the consensus strip
}

const REGIME_COLOR: Record<string, string> = {
  TRENDING: 'var(--positive)',
  BREAKOUT: 'var(--positive)',
  RANGING: 'var(--gold)',
  VOLATILE: 'var(--negative)',
  CONFLICTED: 'var(--negative)',
  LOW_LIQUIDITY_DATA: 'var(--silver-dim)',
};

const verdictColor = (v: string) =>
  v === 'BUY' || v === 'BULLISH' ? 'var(--positive)' :
  v === 'SELL' || v === 'BEARISH' ? 'var(--negative)' :
  'var(--gold)';

export default function SignalIntelligencePanel({ points, categoryLabels, timeframeEntries }: Props) {
  const [hoveredCell, setHoveredCell] = useState<{ r: number; c: number } | null>(null);

  const analysis = useMemo(() => computeCompositeAnalysis(points, categoryLabels), [points, categoryLabels]);
  const matrix = useMemo(() => computeAgreementMatrix(analysis.categories), [analysis.categories]);
  const selfAgreement = useMemo(() => overallSelfAgreement(matrix, analysis.categories), [matrix, analysis.categories]);
  const regime = useMemo(() => detectMarketRegime(analysis.categories), [analysis.categories]);
  const conflict = useMemo(() => computeConflictDetector(analysis), [analysis]);
  const explain = useMemo(() => buildExplainability(points, analysis), [points, analysis]);
  const tfConsensus = useMemo(
    () => (timeframeEntries && timeframeEntries.length ? computeTimeframeConsensus(timeframeEntries) : null),
    [timeframeEntries]
  );

  const withEvidence = analysis.categories.filter(c => c.n > 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>

      {/* ── 1. MULTI-LAYER SIGNAL CONSENSUS ── */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <div style={{ fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: '11px', letterSpacing: '0.05em' }}>
          MULTI-LAYER SIGNAL CONSENSUS
        </div>
        {withEvidence.map(c => {
          const color = hexForCategory(c.categoryIndex, analysis.categories.length);
          const pct = Math.round(c.weight * 100);
          return (
            <div key={c.categoryIndex} style={{ display: 'grid', gridTemplateColumns: '96px 1fr 40px', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '10px', color: 'var(--silver-dim)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {c.label}
              </span>
              <div style={{ height: '9px', background: 'rgba(255,255,255,0.05)', borderRadius: '2px', overflow: 'hidden' }}>
                <div style={{ width: `${pct}%`, height: '100%', background: color, opacity: 0.85 }} />
              </div>
              <span style={{ fontSize: '9px', color: 'var(--silver-dim)', textAlign: 'right', fontFamily: 'var(--font-data)' }}>
                {pct}%
              </span>
            </div>
          );
        })}
      </div>

      {/* ── 2 & 3. REGIME + CONFLICT ── */}
      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 220px', border: '1px solid var(--border)', borderRadius: '6px', padding: '12px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <span style={{ fontSize: '9px', color: 'var(--silver-dim)', letterSpacing: '0.05em' }}>MARKET REGIME</span>
          <span style={{ fontFamily: 'var(--font-display)', fontSize: '15px', fontWeight: 'bold', color: REGIME_COLOR[regime.regime] || 'var(--silver)' }}>
            {regime.regime.replace(/_/g, ' ')}
          </span>
          <span style={{ fontSize: '9.5px', color: 'var(--silver-dim)', fontFamily: 'var(--font-data)' }}>
            {regime.reasoning}
          </span>
        </div>

        <div style={{ flex: '1 1 220px', border: '1px solid var(--border)', borderRadius: '6px', padding: '12px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <span style={{ fontSize: '9px', color: 'var(--silver-dim)', letterSpacing: '0.05em' }}>AI CONFLICT DETECTOR</span>
          <span style={{ fontFamily: 'var(--font-display)', fontSize: '15px', fontWeight: 'bold', color: conflict.conflictPct > 50 ? 'var(--negative)' : conflict.conflictPct > 25 ? 'var(--gold)' : 'var(--positive)' }}>
            {conflict.conflictPct}% conflict
          </span>
          <span style={{ fontSize: '9.5px', color: 'var(--silver-dim)', fontFamily: 'var(--font-data)' }}>
            {conflict.mainCause}
          </span>
        </div>

        <div style={{ flex: '1 1 220px', border: '1px solid var(--border)', borderRadius: '6px', padding: '12px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <span style={{ fontSize: '9px', color: 'var(--silver-dim)', letterSpacing: '0.05em' }}>SELF-AGREEMENT</span>
          <span style={{ fontFamily: 'var(--font-display)', fontSize: '15px', fontWeight: 'bold', color: selfAgreement > 0.65 ? 'var(--positive)' : selfAgreement > 0.4 ? 'var(--gold)' : 'var(--negative)' }}>
            {Math.round(selfAgreement * 100)}%
          </span>
          <span style={{ fontSize: '9.5px', color: 'var(--silver-dim)', fontFamily: 'var(--font-data)' }}>
            How much every category plane agrees with every other one, averaged across all pairs.
          </span>
        </div>
      </div>

      {/* ── 4. SIGNAL AGREEMENT MATRIX ── */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <div style={{ fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: '11px', letterSpacing: '0.05em' }}>
          SIGNAL AGREEMENT MATRIX
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', fontSize: '9px', fontFamily: 'var(--font-data)' }}>
            <thead>
              <tr>
                <th style={{ padding: '4px' }} />
                {analysis.categories.map(c => (
                  <th key={c.categoryIndex} style={{ padding: '4px', color: 'var(--silver-dim)', fontWeight: 'normal', writingMode: 'vertical-rl', textOrientation: 'mixed', maxHeight: '70px' }}>
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {matrix.map((row, r) => (
                <tr key={r}>
                  <td style={{ padding: '4px', color: 'var(--silver-dim)', textAlign: 'right', whiteSpace: 'nowrap' }}>
                    {analysis.categories[r].label}
                  </td>
                  {row.map((cell, c) => {
                    const noEvidence = analysis.categories[r].n === 0 || analysis.categories[c].n === 0;
                    const bg = noEvidence
                      ? 'rgba(255,255,255,0.03)'
                      : cell.agreement > 0
                        ? `rgba(38, 194, 129, ${0.15 + Math.abs(cell.agreement) * 0.55})`
                        : cell.agreement < 0
                          ? `rgba(244, 63, 94, ${0.15 + Math.abs(cell.agreement) * 0.55})`
                          : 'rgba(255,255,255,0.05)';
                    const isHovered = hoveredCell && hoveredCell.r === r && hoveredCell.c === c;
                    return (
                      <td
                        key={c}
                        onMouseEnter={() => setHoveredCell({ r, c })}
                        onMouseLeave={() => setHoveredCell(null)}
                        title={noEvidence ? 'No evidence in one or both categories' : `${(cell.agreement * 100).toFixed(0)}% ${cell.agreement >= 0 ? 'agreement' : 'conflict'}`}
                        style={{
                          width: '26px', height: '22px', textAlign: 'center', background: bg,
                          border: isHovered ? '1px solid var(--gold)' : '1px solid var(--border-subtle)',
                          color: 'var(--silver)', cursor: 'default',
                        }}
                      >
                        {r === c ? '·' : noEvidence ? '' : cell.agreement >= 0 ? '✓' : '✕'}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ display: 'flex', gap: '14px', fontSize: '9px', color: 'var(--silver-dim)', fontFamily: 'var(--font-data)' }}>
          <span><span style={{ color: 'var(--positive)' }}>✓</span> categories agree</span>
          <span><span style={{ color: 'var(--negative)' }}>✕</span> categories conflict</span>
          <span>blank = no evidence</span>
        </div>
      </div>

      {/* ── 5. AI EXPLAINABILITY ── */}
      <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 260px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <div style={{ fontFamily: 'var(--font-display)', color: 'var(--positive)', fontSize: '11px', letterSpacing: '0.05em' }}>
            ✓ FOR {analysis.overallVerdict}
          </div>
          {explain.for.length === 0 && (
            <span style={{ fontSize: '9.5px', color: 'var(--silver-dim)', fontStyle: 'italic' }}>No supporting indicators found.</span>
          )}
          {explain.for.map((item, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', borderLeft: '2px solid var(--positive)', paddingLeft: '8px' }}>
              <span style={{ fontSize: '10px', color: 'var(--silver-bright)' }}>{item.name}</span>
              <span style={{ fontSize: '9px', color: 'var(--silver-dim)', fontFamily: 'var(--font-data)', whiteSpace: 'nowrap' }}>
                {item.signal} · {item.score >= 0 ? '+' : ''}{item.score.toFixed(2)}
              </span>
            </div>
          ))}
        </div>

        <div style={{ flex: '1 1 260px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <div style={{ fontFamily: 'var(--font-display)', color: 'var(--negative)', fontSize: '11px', letterSpacing: '0.05em' }}>
            ✕ AGAINST {analysis.overallVerdict}
          </div>
          {explain.against.length === 0 && (
            <span style={{ fontSize: '9.5px', color: 'var(--silver-dim)', fontStyle: 'italic' }}>No conflicting indicators found.</span>
          )}
          {explain.against.map((item, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', borderLeft: '2px solid var(--negative)', paddingLeft: '8px' }}>
              <span style={{ fontSize: '10px', color: 'var(--silver-bright)' }}>{item.name}</span>
              <span style={{ fontSize: '9px', color: 'var(--silver-dim)', fontFamily: 'var(--font-data)', whiteSpace: 'nowrap' }}>
                {item.signal} · {item.score >= 0 ? '+' : ''}{item.score.toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* ── 6. TIMEFRAME CONSENSUS (optional) ── */}
      {tfConsensus && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontFamily: 'var(--font-display)', color: 'var(--gold-bright)', fontSize: '11px', letterSpacing: '0.05em' }}>
              TIMEFRAME CONSENSUS
            </span>
            <span style={{ fontSize: '10px', fontFamily: 'var(--font-data)', color: verdictColor(tfConsensus.overallVerdict) }}>
              {tfConsensus.consensusPct}% agree · {tfConsensus.overallVerdict}
            </span>
          </div>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            {tfConsensus.entries.map(e => (
              <span
                key={e.timeframe}
                style={{
                  padding: '4px 8px', borderRadius: '3px', fontSize: '9.5px', fontFamily: 'var(--font-data)',
                  background: 'rgba(255,255,255,0.04)',
                  border: `1px solid ${verdictColor(e.verdict)}`,
                  color: verdictColor(e.verdict),
                }}
              >
                {e.timeframe} · {e.verdict}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
