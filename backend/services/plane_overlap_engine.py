# services/plane_overlap_engine.py
#
# Backend counterpart to analysisEngine.ts. Same underlying idea, done at
# the level Python is actually good at: real statistics instead of
# eyeballing a 3D scene.
#
# Two things live here:
#
#   1. compute_composite_analysis()  -- folds every category "plane" (its
#      indicators' scores) into one composite verdict: weighted mean,
#      weighted dispersion, agreement ratio, a Wilson confidence interval
#      on the bullish share, and a Shannon-entropy read on how "mixed" the
#      category verdicts are. Two independent ways of asking the same
#      question -- "how much do the planes actually agree once overlapped"
#      -- so one isn't just restating the other.
#
#   2. detect_plane_intersections()  -- the literal geometric read: walks
#      every pair of category planes across the shared slot axis and finds
#      where one category's score curve crosses another's (linear
#      interpolation between the two nearest slots), which is exactly what
#      you'd see as two sheets crossing in the 3D view. This is the same
#      crossing math the frontend's IndicatorWaveform3D radial-plane layout
#      renders, so the backend "engine" and the 3D visualization always
#      agree on where the planes actually intersect -- neither is guessing
#      independently.
#
# Nothing here is indicator-specific. It only ever consumes
# (category, slot_index, score) triples, so it works unchanged no matter
# how many categories/indicators indicator_engine.py produces.
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel


# ── input shape ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class IndicatorPoint:
    name: str
    category: str
    category_index: int
    slot_index: int
    score: float  # -1..1 (bearish..bullish); 0 = hold/no clear read
    signal: str = ""


# ── output shapes (pydantic so a FastAPI route can return these directly) ─

class CategoryPlaneStats(BaseModel):
    category_index: int
    label: str
    mean: float
    n: int
    bulls: int
    bears: int
    holds: int
    weight: float
    verdict: str  # "BULLISH" | "BEARISH" | "HOLD"


class IntersectionEvent(BaseModel):
    category_a: str
    category_b: str
    slot: float          # interpolated slot position where the two planes cross
    score: float          # interpolated score at the crossing
    before: str            # which category led (was higher) just before the crossing
    after: str              # which category leads just after the crossing


class CompositeAnalysis(BaseModel):
    categories: List[CategoryPlaneStats]
    composite_score: float
    dispersion: float
    agreement_ratio: float
    conviction_tier: str          # "HIGH" | "MODERATE" | "LOW"
    overall_verdict: str          # "BUY" | "SELL" | "HOLD"
    dominant_category: Optional[CategoryPlaneStats]
    most_conflicted_category: Optional[CategoryPlaneStats]
    confidence_interval: Tuple[float, float]  # Wilson 95% CI on the bullish share of all indicators
    entropy: float                              # 0..log2(3), Shannon entropy of the category verdict distribution
    intersections: List[IntersectionEvent]
    intersection_density: float                 # intersections per category-pair -- a second, independent read on how "tangled" the overlap is
    narrative: str


# ── composite math ──────────────────────────────────────────────────────

def _wilson_interval(successes: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """95% Wilson score interval for a binomial proportion. Used instead of
    a naive normal-approximation CI because it stays well-behaved (doesn't
    go outside [0, 1] or collapse to a point) even with the small indicator
    counts a single category can have."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    lo = (centre - margin) / denom
    hi = (centre + margin) / denom
    return (max(0.0, lo), min(1.0, hi))


def _shannon_entropy(counts: List[int]) -> float:
    """Shannon entropy (bits) of the category-verdict distribution.
    0 = every category landed on the same verdict (BULLISH/BEARISH/HOLD);
    log2(3) ≈ 1.585 = verdicts are as spread across the three buckets as
    possible. This is a *second*, independent way of reading "how tangled
    is the overlap" -- dispersion measures spread in score-space,
    entropy measures spread in verdict-space, and they can disagree
    (e.g. many categories clustered near +0.11 vs. -0.11 gives low
    dispersion but can still split BULLISH/BEARISH by verdict)."""
    total = sum(counts)
    if total == 0:
        return 0.0
    h = 0.0
    for c in counts:
        if c == 0:
            continue
        p = c / total
        h -= p * math.log2(p)
    return h


def compute_composite_analysis(points: List[IndicatorPoint], category_labels: List[str]) -> CompositeAnalysis:
    categories: List[CategoryPlaneStats] = []
    for i, label in enumerate(category_labels):
        in_cat = [p for p in points if p.category_index == i]
        n = len(in_cat)
        bulls = sum(1 for p in in_cat if p.score > 0.001)
        bears = sum(1 for p in in_cat if p.score < -0.001)
        holds = n - bulls - bears
        mean = (sum(p.score for p in in_cat) / n) if n else 0.0
        verdict = "BULLISH" if mean > 0.1 else "BEARISH" if mean < -0.1 else "HOLD"
        categories.append(CategoryPlaneStats(
            category_index=i, label=label, mean=mean, n=n,
            bulls=bulls, bears=bears, holds=holds, weight=0.0, verdict=verdict,
        ))

    total_n = sum(c.n for c in categories) or 1
    for c in categories:
        c.weight = c.n / total_n

    # ── overlap step: fold every plane into one composite surface ────────
    composite_score = sum(c.mean * c.weight for c in categories)

    variance = sum(c.weight * (c.mean - composite_score) ** 2 for c in categories)
    dispersion = math.sqrt(variance)

    direction_sign = 1 if composite_score > 0.05 else -1 if composite_score < -0.05 else 0
    if direction_sign == 0:
        agreement_ratio = sum(c.weight for c in categories if abs(c.mean) <= 0.1)
    else:
        agreement_ratio = sum(c.weight for c in categories if c.mean * direction_sign > 0.05)

    conviction_tier = "LOW"
    if agreement_ratio >= 0.7 and abs(composite_score) >= 0.2:
        conviction_tier = "HIGH"
    elif agreement_ratio >= 0.5 or abs(composite_score) >= 0.15:
        conviction_tier = "MODERATE"

    overall_verdict = "BUY" if composite_score > 0.15 else "SELL" if composite_score < -0.15 else "HOLD"

    with_evidence = [c for c in categories if c.n > 0]
    dominant_category = max(with_evidence, key=lambda c: abs(c.mean) * c.weight) if with_evidence else None

    conflicted = [c for c in with_evidence if c.n >= 2 and c.bulls > 0 and c.bears > 0]
    most_conflicted_category = (
        max(conflicted, key=lambda c: min(c.bulls, c.bears) / c.n) if conflicted else None
    )

    total_bulls = sum(p.score > 0.001 for p in points)
    total_all = len(points)
    confidence_interval = _wilson_interval(total_bulls, total_all)

    entropy = _shannon_entropy([
        sum(1 for c in categories if c.verdict == "BULLISH"),
        sum(1 for c in categories if c.verdict == "BEARISH"),
        sum(1 for c in categories if c.verdict == "HOLD"),
    ])

    intersections = detect_plane_intersections(points, category_labels)
    pair_count = max(1, len(category_labels) * (len(category_labels) - 1) // 2)
    intersection_density = len(intersections) / pair_count

    narrative = _build_narrative(
        categories, composite_score, dispersion, agreement_ratio, conviction_tier,
        overall_verdict, dominant_category, most_conflicted_category,
        confidence_interval, entropy, len(intersections),
    )

    return CompositeAnalysis(
        categories=categories,
        composite_score=composite_score,
        dispersion=dispersion,
        agreement_ratio=agreement_ratio,
        conviction_tier=conviction_tier,
        overall_verdict=overall_verdict,
        dominant_category=dominant_category,
        most_conflicted_category=most_conflicted_category,
        confidence_interval=confidence_interval,
        entropy=entropy,
        intersections=intersections,
        intersection_density=intersection_density,
        narrative=narrative,
    )


# ── plane-intersection detection ────────────────────────────────────────

def detect_plane_intersections(points: List[IndicatorPoint], category_labels: List[str]) -> List[IntersectionEvent]:
    """For every pair of category planes, walk the shared slot axis and find
    where one plane's score curve crosses the other's -- i.e. exactly the
    points where, in the 3D overlap view, two colored sheets pass through
    each other. Uses linear interpolation between the two nearest slots to
    locate the crossing precisely rather than only flagging "somewhere
    between slot 3 and 4"."""
    by_category: Dict[int, Dict[int, float]] = {}
    for p in points:
        by_category.setdefault(p.category_index, {})[p.slot_index] = p.score

    events: List[IntersectionEvent] = []
    n_cats = len(category_labels)
    for i in range(n_cats):
        for j in range(i + 1, n_cats):
            a, b = by_category.get(i, {}), by_category.get(j, {})
            shared_slots = sorted(set(a.keys()) & set(b.keys()))
            if len(shared_slots) < 2:
                continue
            for s0, s1 in zip(shared_slots, shared_slots[1:]):
                d0 = a[s0] - b[s0]
                d1 = a[s1] - b[s1]
                if d0 == 0 or d1 == 0 or (d0 > 0) == (d1 > 0):
                    continue  # no sign change -> no crossing in this segment
                # linear interpolation for the exact crossing point
                t = d0 / (d0 - d1)
                slot_at_cross = s0 + t * (s1 - s0)
                score_at_cross = a[s0] + t * (a[s1] - a[s0])
                before = category_labels[i] if d0 > 0 else category_labels[j]
                after = category_labels[j] if d0 > 0 else category_labels[i]
                events.append(IntersectionEvent(
                    category_a=category_labels[i], category_b=category_labels[j],
                    slot=round(slot_at_cross, 3), score=round(score_at_cross, 3),
                    before=before, after=after,
                ))
    return events


# ── narrative ────────────────────────────────────────────────────────────

def _build_narrative(
    categories: List[CategoryPlaneStats], composite_score: float, dispersion: float,
    agreement_ratio: float, conviction_tier: str, overall_verdict: str,
    dominant_category: Optional[CategoryPlaneStats], most_conflicted_category: Optional[CategoryPlaneStats],
    confidence_interval: Tuple[float, float], entropy: float, intersection_count: int,
) -> str:
    dir_word = {"BUY": "bullish", "SELL": "bearish", "HOLD": "flat"}[overall_verdict]
    conviction_word = {
        "HIGH": "a tight, high-conviction overlap",
        "MODERATE": "a moderate overlap with some disagreement",
        "LOW": "a loose overlap with meaningful disagreement between planes",
    }[conviction_tier]
    dominant_txt = (
        f"{dominant_category.label} ({dominant_category.mean:+.2f})" if dominant_category else "no single category"
    )
    conflict_txt = (
        f" {most_conflicted_category.label} is the most internally split plane "
        f"({most_conflicted_category.bulls} bullish vs {most_conflicted_category.bears} bearish within it)."
        if most_conflicted_category else ""
    )
    lo, hi = confidence_interval
    return (
        f"Overlapping all {len(categories)} category planes gives a composite score of {composite_score:.2f} "
        f"({dir_word}), with {agreement_ratio * 100:.0f}% of the weighted evidence agreeing on that direction "
        f"and a dispersion of {dispersion:.2f} between planes — {conviction_word}. "
        f"The bullish share across all indicators is {lo * 100:.0f}%–{hi * 100:.0f}% at 95% confidence "
        f"(Wilson interval), and category verdicts carry {entropy:.2f} bits of entropy "
        f"(0 = unanimous, 1.58 = maximally split). {dominant_txt} is contributing the most to the composite. "
        f"The planes physically cross each other {intersection_count} time(s) across the overlap.{conflict_txt}"
    )


# ── convenience: build IndicatorPoints straight from a raw indicator dict ─

def points_from_indicator_results(
    all_indicators: Dict[str, dict],
    category_order: List[str],
    category_labels: Dict[str, str],
    positive_signals: set,
    negative_signals: set,
) -> Tuple[List[IndicatorPoint], List[str]]:
    """Mirrors the grouping logic already used on the frontend
    (TabbedPanel's groupedByCategory / waveformPoints construction) so the
    backend engine can be fed straight from indicator_engine.py's output
    without re-deriving the category bucketing twice. Pass in your actual
    INDICATOR_POSITIVE_SIGNALS / INDICATOR_NEGATIVE_SIGNALS sets rather than
    hardcoding them here, since those already live in TabbedPanel.tsx and
    should stay the single source of truth for what counts as bullish vs
    bearish (routers/plane_overlap.py keeps a matching copy server-side)."""
    grouped: Dict[str, List[Tuple[str, float, str]]] = {cat: [] for cat in category_order}
    for name, res in all_indicators.items():
        if not res:
            continue
        raw_cat = (res.get("category") or "").lower().strip().replace(" ", "_").replace("-", "_")
        cat = "price_action" if raw_cat == "priceaction" else raw_cat
        if cat not in grouped:
            continue
        signal = (res.get("signal") or "").upper()
        score = 1.0 if signal in positive_signals else -1.0 if signal in negative_signals else 0.0
        grouped[cat].append((name, score, signal or "—"))

    for lst in grouped.values():
        lst.sort(key=lambda t: t[0])

    points: List[IndicatorPoint] = []
    for category_index, cat in enumerate(category_order):
        for slot_index, (name, score, signal) in enumerate(grouped[cat]):
            points.append(IndicatorPoint(
                name=name, category=cat, category_index=category_index,
                slot_index=slot_index, score=score, signal=signal,
            ))

    labels = [category_labels.get(cat, cat) for cat in category_order]
    return points, labels
