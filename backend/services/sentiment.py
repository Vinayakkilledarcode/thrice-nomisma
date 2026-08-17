# services/sentiment.py
#
# ── ADVANCED SENTIMENT ENGINE ──────────────────────────────────────────────
# Upgraded from a single flat average score to a corporate-style composite:
#   - per-headline scoring (FinBERT when available, lexicon fallback)
#   - recency-weighted aggregation (a headline from 10 minutes ago should
#     matter more than one from 6 days ago)
#   - a confidence metric based on sample size AND how much the headlines
#     agree with each other (low agreement = low confidence, regardless of
#     the average)
#   - a 5-tier magnitude classification instead of just pos/neg/neutral
#   - a full per-headline breakdown so the UI can show WHY the score is
#     what it is, not just the final number
#   - a synthesized natural-language REASONING summary that explains the
#     composite verdict in prose (top bullish/bearish drivers, confidence
#     rationale) instead of forcing the UI to dump every raw headline
#
# All of the original fields (sentiment_score, headlines, verdict, engine)
# are still returned unchanged for backward compatibility with existing
# callers/UI — the new fields are additive.

import os
# Limit CPU thread consumption inside PyTorch to conserve event loop capacity
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import math
import httpx
import asyncio
import numpy as np
from typing import List, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone

try:
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
except ImportError:
    torch = None
    AutoTokenizer = None
    AutoModelForSequenceClassification = None

FINBERT_MODEL_NAME = "yiyanghkust/finbert-tone"
_tokenizer = None
_model = None

# Recency decay: a headline's influence on the composite score halves every
# HALF_LIFE_HOURS. Recent news should dominate; a week-old headline should
# barely move the needle by the time we're scoring intraday strategies.
HALF_LIFE_HOURS = 18.0

# Transient-failure resilience: if a live fetch comes back empty (Yahoo/Finnhub
# hiccup, momentary rate limit, etc.), reuse the last successful headline set
# for that symbol rather than dropping straight to "no data available" in the
# UI. Entries expire after CACHE_TTL_SECONDS so we don't serve genuinely stale
# news forever if a symbol really has gone quiet.
_last_good_headlines: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
CACHE_TTL_SECONDS = 60 * 60 * 6  # 6 hours

# Magnitude thresholds (5-tier instead of 3-tier pos/neutral/neg)
MAGNITUDE_BANDS = [
    (0.50,  "STRONGLY_POSITIVE"),
    (0.15,  "POSITIVE"),
    (-0.15, "NEUTRAL"),
    (-0.50, "NEGATIVE"),
    (-1.01, "STRONGLY_NEGATIVE"),
]

# How many top bullish / bearish headlines to cite by name in the reasoning
# paragraph. Kept small so the summary reads like an analyst note, not
# another list.
REASONING_TOP_N = 2

# Below this absolute weighted-impact threshold a headline is considered
# "noise" and won't be cited as a named driver, even if it's technically the
# most positive/negative score in the batch.
REASONING_MIN_IMPACT = 0.05


def _clean_env_var(val: str) -> str:
    if not val:
        return ""
    return val.strip().strip('"').strip("'")


def load_finbert_pipeline():
    """Lazy loader for FinBERT tokenizer and sequence classification weights."""
    global _tokenizer, _model
    if AutoTokenizer is None or AutoModelForSequenceClassification is None or torch is None:
        return None, None
    if _tokenizer is not None and _model is not None:
        return _tokenizer, _model
    try:
        print("[SENTIMENT] Initializing FinBERT model...")
        _tokenizer = AutoTokenizer.from_pretrained(FINBERT_MODEL_NAME)
        _model = AutoModelForSequenceClassification.from_pretrained(FINBERT_MODEL_NAME)
        _model.eval()
        print("[SENTIMENT] FinBERT loaded successfully.")
    except Exception as e:
        print(f"[SENTIMENT] Load failure: {e}. Falling back to rule-based lexicon.")
        _tokenizer, _model = None, None
    return _tokenizer, _model


def _fetch_yfinance_news_sync(symbol: str) -> List[Dict[str, Any]]:
    """
    Fallback headline source using yfinance's built-in news feed — no API key
    required. Runs in a worker thread since yfinance is synchronous/blocking.
    Normalized into the same shape fetch_news_headlines() returns from
    Finnhub (headline / datetime / source) so downstream scoring code doesn't
    need to know which provider served the data.
    """
    try:
        import yfinance as yf
        clean_symbol = symbol.split(":")[-1].strip().upper()
        # Finnhub wants the bare NSE ticker (e.g. "TCS"); yfinance wants the
        # ".NS" suffixed symbol for Indian equities. Re-append it here.
        yf_symbol = clean_symbol if "." in clean_symbol else f"{clean_symbol}.NS"
        raw_items = yf.Ticker(yf_symbol).news or []

        normalized = []
        for item in raw_items:
            # yfinance's news schema has shifted between versions — some
            # return fields at the top level, some nest them under "content".
            content = item.get("content", item)
            headline = content.get("title") or item.get("title") or ""
            if not headline:
                continue
            pub_date = content.get("pubDate") or item.get("providerPublishTime")
            unix_ts = 0
            if isinstance(pub_date, str):
                try:
                    unix_ts = datetime.fromisoformat(pub_date.replace("Z", "+00:00")).timestamp()
                except Exception:
                    unix_ts = 0
            elif isinstance(pub_date, (int, float)):
                unix_ts = float(pub_date)
            provider = (content.get("provider") or {}).get("displayName") if isinstance(content.get("provider"), dict) else None
            normalized.append({
                "headline": headline,
                "datetime": unix_ts,
                "source": provider or item.get("publisher") or "Yahoo Finance",
            })
        normalized.sort(key=lambda s: s.get("datetime", 0), reverse=True)
        return normalized[:15]
    except Exception as e:
        print(f"[SENTIMENT] yfinance news fallback exception: {e}")
        return []


async def fetch_news_headlines(symbol: str) -> List[Dict[str, Any]]:
    """
    Queries Finnhub's REST API for recent news stories when FINNHUB_API_KEY
    is configured; falls back to yfinance's free news feed otherwise (or if
    Finnhub returns zero stories). Without this fallback, a missing/invalid
    Finnhub key silently produced an empty headline list on every call,
    which is why sentiment always showed NEUTRAL / exactly 50% — there was
    simply no data being scored, not a scoring bug.
    """
    api_key = _clean_env_var(os.getenv("FINNHUB_API_KEY", ""))
    clean_symbol = symbol.split(":")[-1].replace(".NS", "").replace(".BO", "").strip().upper()

    if api_key:
        to_date = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        url = "https://finnhub.io/api/v1/company-news"
        params = {
            "symbol": clean_symbol,
            "from": from_date,
            "to": to_date,
            "token": api_key
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=10.0)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list) and data:
                        # Sort newest-first so recency weighting is intuitive downstream
                        data.sort(key=lambda s: s.get("datetime", 0), reverse=True)
                        result = data[:15]
                        _last_good_headlines[clean_symbol] = (datetime.now(timezone.utc).timestamp(), result)
                        return result
                else:
                    print(f"[SENTIMENT] Finnhub returned HTTP {response.status_code}: {response.text[:200]}")
        except Exception as e:
            print(f"[SENTIMENT] News fetch exception: {e}")
    else:
        print("[SENTIMENT] FINNHUB_API_KEY not set — using yfinance news fallback.")

    # Fallback path: Finnhub key missing, or Finnhub returned zero stories.
    fetched = await asyncio.to_thread(_fetch_yfinance_news_sync, symbol)

    now = datetime.now(timezone.utc).timestamp()
    if fetched:
        _last_good_headlines[clean_symbol] = (now, fetched)
        return fetched

    # Live fetch came back empty — check for a recent cached success before
    # giving up and reporting "no headlines available".
    cached = _last_good_headlines.get(clean_symbol)
    if cached:
        cached_at, cached_stories = cached
        if now - cached_at <= CACHE_TTL_SECONDS:
            print(f"[SENTIMENT] Live fetch empty for {clean_symbol}; serving cached headlines from "
                  f"{(now - cached_at) / 60:.0f} min ago.")
            return cached_stories

    return []


def _recency_weight(published_unix: float, now_unix: float) -> float:
    """Exponential decay weight — a headline's influence halves every HALF_LIFE_HOURS."""
    if not published_unix or published_unix <= 0:
        return 0.35  # unknown timestamp: modest, non-zero, non-dominant weight
    age_hours = max(0.0, (now_unix - published_unix) / 3600.0)
    return float(0.5 ** (age_hours / HALF_LIFE_HOURS))


def _score_to_magnitude(score: float) -> str:
    for threshold, label in MAGNITUDE_BANDS:
        if score >= threshold:
            return label
    return "STRONGLY_NEGATIVE"


def _score_to_verdict(score: float) -> str:
    """3-tier verdict retained for backward compatibility with existing UI badges."""
    if score > 0.15:
        return "POSITIVE"
    if score < -0.15:
        return "NEGATIVE"
    return "NEUTRAL"


def evaluate_fallback_sentiment_per_headline(headlines: List[str]) -> List[float]:
    """Evaluates EACH headline's sentiment using a rule-based lexical matcher (no averaging)."""
    pos_keywords = {"grow", "growth", "profit", "profits", "bullish", "acquisition", "surpasses",
                     "dividend", "highest", "raise", "raised", "win", "wins", "partnership", "record",
                     "upgrade", "upgraded", "beat", "beats", "outperform", "rally", "surge", "surges"}
    neg_keywords = {"drop", "drops", "loss", "losses", "bearish", "investigation", "deficit", "decline",
                     "declines", "misses", "miss", "breach", "fail", "fails", "lawsuit", "downgrade",
                     "downgraded", "plunge", "plunges", "crash", "probe", "fraud", "recall"}

    scores = []
    for line in headlines:
        tokens = line.lower().split()
        pos_count = sum(1 for token in tokens if token.strip(".,!?") in pos_keywords)
        neg_count = sum(1 for token in tokens if token.strip(".,!?") in neg_keywords)

        diff = pos_count - neg_count
        if diff > 0:
            scores.append(min(0.85, 0.35 + 0.15 * diff))
        elif diff < 0:
            scores.append(max(-0.85, -0.35 + 0.15 * diff))
        else:
            scores.append(0.0)
    return scores


def _sync_finbert_inference_per_headline(headlines: List[str], tokenizer, model) -> List[float]:
    """Performs CPU inference synchronously, returning ONE score per headline (no averaging).
    Executed in a separate thread pool."""
    scores = []
    for text in headlines:
        inputs = tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=512)
        with torch.no_grad():
            outputs = model(**inputs)
            probs = torch.nn.functional.softmax(outputs.logits, dim=-1).numpy()[0]
            # Class mapping: 0 -> Positive, 1 -> Negative, 2 -> Neutral
            pos, neg, _ = probs[0], probs[1], probs[2]
            score = (pos * 1.0) + (neg * -1.0)
            scores.append(float(score))
    return scores


def _composite_from_scores(
    stories: List[Dict[str, Any]],
    headlines: List[str],
    raw_scores: List[float],
) -> Dict[str, Any]:
    """Combines per-headline scores into a recency-weighted composite plus a
    confidence metric derived from sample size and inter-headline agreement."""
    now_unix = datetime.now(timezone.utc).timestamp()

    weights = []
    for story in stories:
        weights.append(_recency_weight(story.get("datetime", 0), now_unix))

    total_weight = sum(weights) or 1.0
    weighted_score = sum(s * w for s, w in zip(raw_scores, weights)) / total_weight

    # Agreement: low variance across headlines => higher confidence.
    # Sample-size factor: more corroborating headlines => higher confidence,
    # saturating around 8 headlines (diminishing returns beyond that).
    variance = float(np.var(raw_scores)) if len(raw_scores) > 1 else 0.0
    agreement_factor = max(0.0, 1.0 - min(1.0, variance / 0.35))
    sample_factor = min(1.0, len(raw_scores) / 8.0)
    confidence_pct = round(100.0 * (0.55 * agreement_factor + 0.45 * sample_factor), 1)

    breakdown = []
    for story, headline, score, weight in zip(stories, headlines, raw_scores, weights):
        breakdown.append({
            "headline": headline,
            "score": round(score, 3),
            "weight": round(weight, 3),
            "verdict": _score_to_verdict(score),
            "published_at": story.get("datetime", None),
            "source": story.get("source", None),
        })

    return {
        "weighted_score": round(weighted_score, 3),
        "confidence": confidence_pct,
        "magnitude": _score_to_magnitude(weighted_score),
        "breakdown": breakdown,
        "agreement_factor": agreement_factor,
        "sample_factor": sample_factor,
    }


def _magnitude_phrase(magnitude: str) -> str:
    return {
        "STRONGLY_POSITIVE": "strongly positive",
        "POSITIVE": "positive",
        "NEUTRAL": "neutral",
        "NEGATIVE": "negative",
        "STRONGLY_NEGATIVE": "strongly negative",
    }.get(magnitude, "neutral")


def _confidence_phrase(confidence_pct: float, agreement_factor: float, sample_factor: float) -> str:
    if confidence_pct >= 80:
        level = "high"
    elif confidence_pct >= 50:
        level = "moderate"
    else:
        level = "low"

    if agreement_factor >= 0.75 and sample_factor >= 0.75:
        driver = "a solid headline count and strong agreement across sources"
    elif agreement_factor < 0.4 and sample_factor >= 0.5:
        driver = "headlines pulling in noticeably different directions, despite a decent sample size"
    elif sample_factor < 0.4:
        driver = "a fairly thin sample of recent headlines"
    else:
        driver = "a mix of sample size and cross-source agreement"

    return f"{level} confidence, driven by {driver}"


def _describe_headline(item: Dict[str, Any]) -> str:
    """Trims a headline for inline citation in a sentence — keeps it readable
    without just repeating the raw string verbatim as a list item."""
    text = (item.get("headline") or "").strip()
    if len(text) > 90:
        text = text[:87].rstrip() + "..."
    return text


def _generate_reasoning_summary(
    symbol: str,
    weighted_score: float,
    magnitude: str,
    confidence_pct: float,
    agreement_factor: float,
    sample_factor: float,
    sample_size: int,
    breakdown: List[Dict[str, Any]],
    engine: str,
) -> str:
    """
    Synthesizes the composite score + per-headline breakdown into a short
    analyst-style paragraph, instead of leaving the UI to dump every raw
    headline. Deterministic and rule-based — no external LLM call, so it's
    free, fast, and doesn't depend on an API key being configured.

    Approach:
      1. Rank headlines by *weighted impact* (score * recency weight), not
         raw score alone — a strongly-worded but week-old headline shouldn't
         get cited as "the reason" over a milder but fresh one.
      2. Pull out the top bullish and bearish drivers above a minimum impact
         threshold (so near-zero/neutral headlines aren't cited as "drivers").
      3. Compose a paragraph: overall read -> named drivers on each side ->
         confidence rationale.
    """
    if sample_size == 0:
        return "No recent headlines were available for this symbol, so there isn't enough data for a confident sentiment read."

    ranked = sorted(
        breakdown,
        key=lambda item: item.get("score", 0.0) * item.get("weight", 0.0),
        reverse=True,
    )

    bullish = [
        item for item in ranked
        if item.get("score", 0.0) * item.get("weight", 0.0) >= REASONING_MIN_IMPACT
    ][:REASONING_TOP_N]

    bearish = [
        item for item in reversed(ranked)
        if item.get("score", 0.0) * item.get("weight", 0.0) <= -REASONING_MIN_IMPACT
    ][:REASONING_TOP_N]

    mag_phrase = _magnitude_phrase(magnitude)
    conf_phrase = _confidence_phrase(confidence_pct, agreement_factor, sample_factor)

    opener = (
        f"Sentiment on {symbol} reads {mag_phrase} "
        f"(composite score {weighted_score:+.2f}) across {sample_size} recent headline"
        f"{'s' if sample_size != 1 else ''} via {engine}."
    )

    parts = [opener]

    if bullish:
        cited = "; ".join(f"\u201c{_describe_headline(item)}\u201d" for item in bullish)
        parts.append(f"On the positive side: {cited}.")

    if bearish:
        cited = "; ".join(f"\u201c{_describe_headline(item)}\u201d" for item in bearish)
        parts.append(f"Weighing against that: {cited}.")

    if bullish and bearish:
        parts.append(
            f"These offsetting signals are the main reason the composite settles near {mag_phrase}, "
            f"rather than one side dominating outright."
        )
    elif bullish and not bearish:
        parts.append("There's no comparably strong negative headline pulling the other way right now.")
    elif bearish and not bullish:
        parts.append("There's no comparably strong positive headline offsetting this right now.")
    else:
        parts.append("No single headline stands out as a strong driver either way — the read is mostly noise-level.")

    parts.append(f"Confidence is {conf_phrase}.")

    return " ".join(parts)


async def analyze_sentiment(symbol: str) -> Dict[str, Any]:
    """Orchestrates news collection and evaluates a recency-weighted, confidence-scored
    composite sentiment using offloaded execution threads."""
    stories = await fetch_news_headlines(symbol)
    headlines = [s.get("headline", "") for s in stories if s.get("headline")]
    stories = [s for s in stories if s.get("headline")]  # keep stories/headlines aligned

    if not headlines:
        return {
            # Legacy fields (unchanged shape for existing consumers)
            "sentiment_score": 0.0,
            "headlines": [],
            "verdict": "NEUTRAL",
            "engine": "Default",
            # Advanced fields
            "confidence": 0.0,
            "magnitude": "NEUTRAL",
            "sample_size": 0,
            "headline_breakdown": [],
            "methodology": "No recent headlines available — insufficient data for a confident read.",
            "reasoning": _generate_reasoning_summary(
                symbol, 0.0, "NEUTRAL", 0.0, 0.0, 0.0, 0, [], "Default"
            ),
        }

    tokenizer, model = load_finbert_pipeline()

    if tokenizer is None or model is None or torch is None:
        raw_scores = evaluate_fallback_sentiment_per_headline(headlines)
        engine = "Lexicon Heuristics"
    else:
        try:
            raw_scores = await asyncio.to_thread(_sync_finbert_inference_per_headline, headlines, tokenizer, model)
            engine = "FinBERT DL"
        except Exception as e:
            print(f"[SENTIMENT] Model evaluation error: {e}")
            raw_scores = evaluate_fallback_sentiment_per_headline(headlines)
            engine = "Lexicon Heuristics (Fallback)"

    composite = _composite_from_scores(stories, headlines, raw_scores)

    reasoning = _generate_reasoning_summary(
        symbol=symbol,
        weighted_score=composite["weighted_score"],
        magnitude=composite["magnitude"],
        confidence_pct=composite["confidence"],
        agreement_factor=composite["agreement_factor"],
        sample_factor=composite["sample_factor"],
        sample_size=len(headlines),
        breakdown=composite["breakdown"],
        engine=engine,
    )

    return {
        # Legacy fields
        "sentiment_score": composite["weighted_score"],
        "headlines": headlines,
        "verdict": _score_to_verdict(composite["weighted_score"]),
        "engine": engine,
        # Advanced fields
        "confidence": composite["confidence"],
        "magnitude": composite["magnitude"],
        "sample_size": len(headlines),
        "headline_breakdown": composite["breakdown"],
        "methodology": (
            f"Recency-weighted composite of {len(headlines)} headline(s) "
            f"(half-life {HALF_LIFE_HOURS:.0f}h) via {engine}; confidence reflects "
            f"both sample size and cross-headline agreement."
        ),
        # New: synthesized natural-language explanation of the composite verdict
        "reasoning": reasoning,
    }