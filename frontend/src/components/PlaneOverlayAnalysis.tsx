// components/PlaneOverlayAnalysis.tsx
//
// Companion panel to IndicatorWaveform3D. All 7 category planes are drawn
// on one shared -1..+1 axis (single overlay chart), and every point where
// two category lines actually cross is detected geometrically and marked.
// Hovering a crossing marker shows which two categories converged/crossed
// there and what that implies (agreement vs. a signal flip), so the
// overlap -- instead of being noise -- is the insight.
//
// A synchronized crosshair still sweeps the shared axis: move the mouse
// over any slot and every category's reading at that exact slot lights up
// at once.
//
// The composite verdict (donut + written analysis) below the chart is
// unchanged -- still analysisEngine.ts's computeCompositeAnalysis() over
// the exact same WaveformPoint[] the 3D terrain reads from, so both views
// always agree.
import React, { useMemo, useState } from 'react';
import { WaveformPoint, hexForCategory } from './IndicatorWaveform3D';
import { computeCompositeAnalysis } from './analysisEngine';

interface Props {
  points: WaveformPoint[];
  categoryLabels: string[];
  maxSlots: number;
  height?: number;
}

const CHART_W = 700;
const PAD_LEFT = 34;   // room for the -1 / 0 / +1 axis labels
const PAD_RIGHT = 96;  // room for the end-of-line category label
const PAD_TOP = 14;
const PAD_BOTTOM = 14;

type Coord = { x: number; y: number; score: number; slot: number; name: string };

type Intersection = {
  x: number;
  y: number;
  score: number;
  catI: number;
  catJ: number;
  labelI: string;
  labelJ: string;
  colorI: string;
  colorJ: string;
  direction: 'above' | 'below';
};

// Standard 2D line-segment intersection (returns null if parallel or the
// crossing falls outside either segment).
function segmentIntersect(p1: Coord, p2: Coord, p3: Coord, p4: Coord): { x: number; y: number } | null {
  const d1x = p2.x - p1.x, d1y = p2.y - p1.y;
  const d2x = p4.x - p3.x, d2y = p4.y - p3.y;
  const denom = d1x * d2y - d1y * d2x;
  if (Math.abs(denom) < 1e-9) return null;
  const t = ((p3.x - p1.x) * d2y - (p3.y - p1.y) * d2x) / denom;
  const u = ((p3.x - p1.x) * d1y - (p3.y - p1.y) * d1x) / denom;
  if (t < 0 || t > 1 || u < 0 || u > 1) return null;
  return { x: p1.x + t * d1x, y: p1.y + t * d1y };
}

export default function PlaneOverlayAnalysis({ points, categoryLabels, maxSlots, height = 300 }: Props) {
  const [dimmedCategory, setDimmedCategory] = useState<number | null>(null);
  const [hoveredSlot, setHoveredSlot] = useState<number | null>(null);
  const [hoveredCross, setHoveredCross] = useState<Intersection | null>(null);

  const analysis = useMemo(() => computeCompositeAnalysis(points, categoryLabels), [points, categoryLabels]);

  const chartH = Math.max(160, height);
  const plotTop = PAD_TOP;
  const plotBottom = chartH - PAD_BOTTOM;
  const plotH = plotBottom - plotTop;

  const plotX0 = PAD_LEFT;
  const plotX1 = CHART_W - PAD_RIGHT;
  const slotToX = (slot: number) =>
    plotX0 + (maxSlots <= 1 ? 0.5 : slot / (maxSlots - 1)) * (plotX1 - plotX0);
  const scoreToY = (score: number) => plotTop + ((1 - Math.max(-1, Math.min(1, score))) / 2) * plotH;
  const yToScore = (y: number) => Math.max(-1, Math.min(1, 1 - (2 * (y - plotTop)) / plotH));

  // Per-category point lookup by slot, so the crosshair can name the exact
  // indicator behind whatever the hovered column shows, not just a number.
  const pointGrid = useMemo(() => {
    const grid: (WaveformPoint | null)[][] = categoryLabels.map(() => Array(maxSlots).fill(null));
    points.forEach(p => {
      if (p.categoryIndex >= 0 && p.categoryIndex < grid.length && p.slotIndex >= 0 && p.slotIndex < maxSlots) {
        grid[p.categoryIndex][p.slotIndex] = p;
      }
    });
    return grid;
  }, [points, categoryLabels, maxSlots]);

  // One coordinate list per category on the shared axis, plus its drawn line path.
  const categoryCoords = useMemo(() => {
    return categoryLabels.map((_, i) => {
      const inCat = points
        .filter(p => p.categoryIndex === i)
        .slice()
        .sort((a, b) => a.slotIndex - b.slotIndex);
      return inCat.map(p => ({
        x: slotToX(p.slotIndex), y: scoreToY(p.score), score: p.score, slot: p.slotIndex, name: p.name,
      })) as Coord[];
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, categoryLabels, maxSlots, chartH]);

  const rowPaths = useMemo(() => {
    return categoryLabels.map((label, i) => {
      const coords = categoryCoords[i];
      if (coords.length === 0) return null;
      const linePath = coords.map((c, idx) => `${idx === 0 ? 'M' : 'L'}${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ');
      return { categoryIndex: i, label, linePath, color: hexForCategory(i, categoryLabels.length), endY: coords[coords.length - 1].y };
    });
  }, [categoryCoords, categoryLabels]);

  // Direct end-of-line labels so each line is identified by name, not just
  // color -- with simple vertical spacing so labels whose lines end close
  // together don't overlap each other.
  const MIN_LABEL_GAP = 12;
  const endLabels = useMemo(() => {
    const raw = rowPaths
      .map((pp, i) => (pp ? { i, label: pp.label, color: pp.color, rawY: pp.endY, y: pp.endY } : null))
      .filter((x): x is { i: number; label: string; color: string; rawY: number; y: number } => x !== null)
      .sort((a, b) => a.rawY - b.rawY);
    for (let k = 1; k < raw.length; k++) {
      if (raw[k].y - raw[k - 1].y < MIN_LABEL_GAP) raw[k].y = raw[k - 1].y + MIN_LABEL_GAP;
    }
    // if pushing down ran past the bottom, pull the whole stack back up
    const overflow = raw.length ? raw[raw.length - 1].y - (plotBottom + 4) : 0;
    if (overflow > 0) raw.forEach(r => { r.y -= overflow; });
    return raw;
  }, [rowPaths, plotBottom]);

  // Every point where two category lines actually cross, found geometrically
  // rather than by comparing values at matching slots -- so a crossing that
  // happens *between* two slots is still caught.
  const intersections = useMemo(() => {
    const found: Intersection[] = [];
    const seen = new Set<string>();
    for (let i = 0; i < categoryLabels.length; i++) {
      for (let j = i + 1; j < categoryLabels.length; j++) {
        const ci = categoryCoords[i], cj = categoryCoords[j];
        if (ci.length < 2 || cj.length < 2) continue;
        for (let a = 0; a < ci.length - 1; a++) {
          for (let b = 0; b < cj.length - 1; b++) {
            const hit = segmentIntersect(ci[a], ci[a + 1], cj[b], cj[b + 1]);
            if (!hit) continue;
            const key = `${i}-${j}-${hit.x.toFixed(0)}-${hit.y.toFixed(0)}`;
            if (seen.has(key)) continue;
            seen.add(key);
            const diffStart = ci[a].score - cj[b].score;
            const diffEnd = ci[a + 1].score - cj[b + 1].score;
            const direction: 'above' | 'below' = diffStart < diffEnd ? 'above' : 'below';
            found.push({
              x: hit.x, y: hit.y, score: yToScore(hit.y),
              catI: i, catJ: j,
              labelI: categoryLabels[i], labelJ: categoryLabels[j],
              colorI: hexForCategory(i, categoryLabels.length), colorJ: hexForCategory(j, categoryLabels.length),
              direction,
            });
          }
        }
      }
    }
    return found;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryCoords, categoryLabels]);

  const verdictColor = analysis.overallVerdict === 'BUY' ? '#4ade80' : analysis.overallVerdict === 'SELL' ? '#f87171' : '#facc15';

  // ── agreement donut ("the circle") ────────────────────────────────────
  const directionSign = analysis.compositeScore > 0.05 ? 1 : analysis.compositeScore < -0.05 ? -1 : 0;
  const opposingWeight = directionSign === 0
    ? 0
    : analysis.categories.reduce((a, c) => a + (c.mean * directionSign < -0.05 ? c.weight : 0), 0);
  const agreeingWeight = analysis.agreementRatio;
  const neutralWeight = Math.max(0, 1 - agreeingWeight - opposingWeight);

  const R = 46;
  const CIRC = 2 * Math.PI * R;
  const donutSegs = [
    { pct: agreeingWeight, color: verdictColor },
    { pct: opposingWeight, color: directionSign >= 0 ? '#f87171' : '#4ade80' },
    { pct: neutralWeight, color: '#6b7280' },
  ].filter(s => s.pct > 0.001);
  let cumulative = 0;

  const onPlotMouseMove = (ev: React.MouseEvent<SVGSVGElement>) => {
    const rect = ev.currentTarget.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * CHART_W;
    if (px < plotX0 - 4 || px > plotX1 + 4) { setHoveredSlot(null); return; }
    const ratio = (px - plotX0) / Math.max(1, plotX1 - plotX0);
    const slot = Math.round(ratio * (maxSlots - 1));
    setHoveredSlot(Math.max(0, Math.min(maxSlots - 1, slot)));
  };

  const crosshairX = hoveredSlot !== null ? slotToX(hoveredSlot) : null;
  const hoveredRows = hoveredSlot !== null
    ? categoryLabels
      .map((label, i) => ({ i, label, point: pointGrid[i]?.[hoveredSlot] || null, color: hexForCategory(i, categoryLabels.length) }))
      .filter(r => r.point !== null)
    : [];

  const axisTicks = [1, 0.5, 0, -0.5, -1];

  return (
    <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* ── single shared-axis overlay: every category plane on one chart ── */}
      <div style={{ width: '100%', background: '#000000', borderRadius: 4, border: '1px solid var(--border-subtle, #2a2d35)', padding: '10px 6px', position: 'relative' }}>
        <svg
          viewBox={`0 0 ${CHART_W} ${chartH}`}
          width="100%"
          height={chartH}
          preserveAspectRatio="none"
          onMouseMove={onPlotMouseMove}
          onMouseLeave={() => { setHoveredSlot(null); }}
        >
          {/* +1 / +0.5 / 0 / -0.5 / -1 reference lines, one shared scale for every category */}
          {axisTicks.map(t => {
            const y = scoreToY(t);
            return (
              <g key={t}>
                <line
                  x1={plotX0} x2={plotX1} y1={y} y2={y}
                  stroke={t === 0 ? '#ffffff' : '#1c1e24'}
                  strokeOpacity={t === 0 ? 0.22 : 0.9}
                  strokeDasharray={t === 0 ? '3 3' : undefined}
                  strokeWidth={1}
                />
                <text x={plotX0 - 5} y={y + 3} textAnchor="end" fontSize={8} fontFamily="var(--font-data, monospace)" fill="#4a4e58">
                  {t > 0 ? `+${t}` : t}
                </text>
              </g>
            );
          })}

          {/* every category's line, sharing the same axis -- dark halo first so
              overlapping colors stay readable, then the colored line, then an
              invisible wide hit-area so hovering the line itself identifies it */}
          {rowPaths.map((pp, i) => {
            if (!pp) return null;
            const dimmed = dimmedCategory !== null && dimmedCategory !== i;
            return (
              <g key={pp.label + i}>
                <path
                  d={pp.linePath}
                  fill="none"
                  stroke="#05060a"
                  strokeWidth={dimmedCategory === i ? 4.4 : 3.2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  opacity={dimmed ? 0.05 : 0.65}
                  vectorEffect="non-scaling-stroke"
                  style={{ transition: 'opacity 0.15s ease, stroke-width 0.15s ease' }}
                />
                <path
                  d={pp.linePath}
                  fill="none"
                  stroke={pp.color}
                  strokeWidth={dimmedCategory === i ? 2.4 : 1.4}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  opacity={dimmed ? 0.12 : 0.92}
                  vectorEffect="non-scaling-stroke"
                  style={{ transition: 'opacity 0.15s ease, stroke-width 0.15s ease' }}
                />
                <path
                  d={pp.linePath}
                  fill="none"
                  stroke="transparent"
                  strokeWidth={10}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  style={{ cursor: 'pointer' }}
                  onMouseEnter={() => setDimmedCategory(i)}
                  onMouseLeave={() => setDimmedCategory(prev => (prev === i ? null : prev))}
                />
              </g>
            );
          })}

          {/* every point where two lines actually cross -- subtle until hovered, so it's insight, not clutter */}
          {intersections.map((x, idx) => (
            <circle
              key={idx}
              cx={x.x}
              cy={x.y}
              r={hoveredCross === x ? 5.5 : 2.5}
              fill={hoveredCross === x ? '#0a0b0e' : 'none'}
              stroke="#d4af37"
              strokeWidth={hoveredCross === x ? 1.6 : 1}
              opacity={
                dimmedCategory !== null && dimmedCategory !== x.catI && dimmedCategory !== x.catJ
                  ? 0.08
                  : hoveredCross === x ? 1 : 0.4
              }
              style={{ cursor: 'pointer', transition: 'r 0.1s ease, opacity 0.15s ease' }}
              onMouseEnter={() => setHoveredCross(x)}
              onMouseLeave={() => setHoveredCross(prev => (prev === x ? null : prev))}
            />
          ))}

          {/* direct end-of-line labels -- name every line where it ends, not just by color */}
          {endLabels.map(l => {
            const dimmed = dimmedCategory !== null && dimmedCategory !== l.i;
            const displaced = Math.abs(l.y - l.rawY) > 1.5;
            return (
              <g
                key={l.i}
                opacity={dimmed ? 0.25 : 1}
                style={{ cursor: 'pointer', transition: 'opacity 0.15s ease' }}
                onMouseEnter={() => setDimmedCategory(l.i)}
                onMouseLeave={() => setDimmedCategory(prev => (prev === l.i ? null : prev))}
              >
                {displaced && (
                  <line x1={plotX1 + 2} x2={plotX1 + 8} y1={l.rawY} y2={l.y} stroke={l.color} strokeOpacity={0.5} strokeWidth={1} />
                )}
                <circle cx={plotX1 + 3} cy={l.rawY} r={2} fill={l.color} />
                <text
                  x={plotX1 + 10} y={l.y + 3}
                  fontSize={9.5} fontWeight={700}
                  fontFamily="var(--font-data, monospace)"
                  fill={l.color}
                >
                  {l.label}
                </text>
              </g>
            );
          })}

          {/* synchronized crosshair across the shared axis */}
          {crosshairX !== null && (
            <line x1={crosshairX} x2={crosshairX} y1={plotTop} y2={plotBottom} stroke="#d4af37" strokeOpacity={0.4} strokeWidth={1} />
          )}
          {crosshairX !== null && hoveredRows.map(r => (
            <circle
              key={r.i}
              cx={crosshairX}
              cy={scoreToY(r.point!.score)}
              r={3}
              fill={r.color}
              stroke="#000"
              strokeWidth={1}
            />
          ))}
        </svg>

        {/* crossing tooltip -- which two categories met here, and what it means */}
        {hoveredCross && (
          <div
            style={{
              position: 'absolute',
              left: `${(hoveredCross.x / CHART_W) * 100}%`,
              top: `${Math.max(0, (hoveredCross.y / chartH) * 100 - 14)}%`,
              transform: 'translate(-50%, -100%)',
              background: 'rgba(8,9,12,0.97)',
              border: '1px solid #d4af37',
              borderRadius: 6,
              padding: '6px 9px',
              fontSize: 10,
              fontFamily: 'var(--font-data, monospace)',
              color: 'var(--silver, #c8ccd4)',
              maxWidth: 230,
              pointerEvents: 'none',
              boxShadow: '0 4px 18px rgba(0,0,0,0.6)',
              whiteSpace: 'nowrap',
              zIndex: 2,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{ width: 7, height: 7, borderRadius: 2, background: hoveredCross.colorI, display: 'inline-block' }} />
              <span>{hoveredCross.labelI}</span>
              <span style={{ color: '#7a7f8a' }}>{hoveredCross.direction === 'above' ? 'crosses above' : 'crosses below'}</span>
              <span style={{ width: 7, height: 7, borderRadius: 2, background: hoveredCross.colorJ, display: 'inline-block' }} />
              <span>{hoveredCross.labelJ}</span>
            </div>
            <div style={{ marginTop: 3, color: 'var(--gold-dim, #a8863f)' }}>
              at {hoveredCross.score >= 0 ? '+' : ''}{hoveredCross.score.toFixed(2)} ·{' '}
              {hoveredCross.direction === 'above'
                ? `${hoveredCross.labelI} taking over from ${hoveredCross.labelJ}`
                : `${hoveredCross.labelJ} taking over from ${hoveredCross.labelI}`}
            </div>
          </div>
        )}

        {/* crosshair readout -- what every category says at this exact slot */}
        {hoveredRows.length > 0 && (
          <div
            style={{
              position: 'absolute',
              top: 8,
              right: 8,
              background: 'rgba(8,9,12,0.96)',
              border: '1px solid var(--border-subtle, #2a2d35)',
              borderRadius: 6,
              padding: '6px 9px',
              fontSize: 10,
              fontFamily: 'var(--font-data, monospace)',
              color: 'var(--silver, #c8ccd4)',
              maxWidth: 220,
              pointerEvents: 'none',
              boxShadow: '0 4px 18px rgba(0,0,0,0.6)',
            }}
          >
            <div style={{ color: 'var(--gold-dim, #a8863f)', marginBottom: 3 }}>slot {hoveredSlot}</div>
            {hoveredRows.map(r => (
              <div key={r.i} style={{ display: 'flex', alignItems: 'center', gap: 5, marginTop: 2 }}>
                <span style={{ width: 7, height: 7, borderRadius: 2, background: r.color, flexShrink: 0, display: 'inline-block' }} />
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.point!.name}</span>
                <span style={{ marginLeft: 'auto', color: r.point!.score > 0 ? '#4ade80' : r.point!.score < 0 ? '#f87171' : '#9ca3af', fontWeight: 700 }}>
                  {r.point!.score >= 0 ? '+' : ''}{r.point!.score.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* clickable legend -- hover/click a category to isolate its line above */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', fontSize: 10.5, fontFamily: 'var(--font-data, monospace)', color: 'var(--silver, #c8ccd4)' }}>
        {categoryLabels.map((label, i) => {
          const stats = analysis.categories[i];
          return (
            <span
              key={label + i}
              onMouseEnter={() => setDimmedCategory(i)}
              onMouseLeave={() => setDimmedCategory(null)}
              onClick={() => setDimmedCategory(prev => (prev === i ? null : i))}
              style={{ display: 'flex', alignItems: 'center', gap: 5, cursor: 'pointer', opacity: dimmedCategory !== null && dimmedCategory !== i ? 0.45 : 1 }}
            >
              <span style={{ width: 8, height: 8, borderRadius: 2, background: hexForCategory(i, categoryLabels.length), display: 'inline-block', flexShrink: 0 }} />
              {label}
              <span style={{ color: 'var(--silver-dim, #7a7f8a)' }}>{stats.mean >= 0 ? '+' : ''}{stats.mean.toFixed(2)}</span>
            </span>
          );
        })}
      </div>

      {/* ── the circle: agreement donut + composite readout, then the written analysis ── */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 18, alignItems: 'center' }}>
        <svg width={120} height={120} viewBox="0 0 120 120" style={{ flexShrink: 0 }}>
          <circle cx={60} cy={60} r={R} fill="none" stroke="#1c1f26" strokeWidth={14} />
          {donutSegs.map((seg, idx) => {
            const len = seg.pct * CIRC;
            const el = (
              <circle
                key={idx}
                cx={60} cy={60} r={R} fill="none"
                stroke={seg.color} strokeWidth={14}
                strokeDasharray={`${len} ${CIRC - len}`}
                strokeDashoffset={-cumulative}
                transform="rotate(-90 60 60)"
                strokeLinecap="butt"
              />
            );
            cumulative += len;
            return el;
          })}
          <text x={60} y={56} textAnchor="middle" fontSize={16} fontWeight={700} fill={verdictColor} fontFamily="var(--font-data, monospace)">
            {analysis.overallVerdict}
          </text>
          <text x={60} y={72} textAnchor="middle" fontSize={9} fill="#9ca3af" fontFamily="var(--font-data, monospace)">
            {Math.round(agreeingWeight * 100)}% aligned
          </text>
        </svg>

        <div style={{ flex: '1 1 260px', minWidth: 240, fontSize: 10.5, fontFamily: 'var(--font-data, monospace)', color: 'var(--silver, #c8ccd4)' }}>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 6 }}>
            <span>composite <b style={{ color: verdictColor }}>{analysis.compositeScore >= 0 ? '+' : ''}{analysis.compositeScore.toFixed(2)}</b></span>
            <span>dispersion <b>{analysis.dispersion.toFixed(2)}</b></span>
            <span>conviction <b style={{ color: analysis.convictionTier === 'HIGH' ? '#4ade80' : analysis.convictionTier === 'MODERATE' ? '#facc15' : '#f87171' }}>{analysis.convictionTier}</b></span>
          </div>
          <div style={{ lineHeight: 1.5, color: 'var(--silver, #c8ccd4)' }}>{analysis.narrative}</div>
        </div>
      </div>

      <div style={{ padding: '0 2px', fontSize: 9.5, fontFamily: 'var(--font-data, monospace)', color: 'var(--silver-dim, #7a7f8a)', lineHeight: 1.5 }}>
        All categories share one -1..+1 axis · gold rings mark where two lines cross -- hover a ring to see which categories met and what it means · move the mouse to sweep a synced crosshair · click a legend entry to isolate its line.
      </div>
    </div>
  );
}