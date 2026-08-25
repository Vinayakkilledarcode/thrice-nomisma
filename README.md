# Thrice Nomisma

A Bloomberg-style desktop trading terminal for Indian equities (NSE/BSE), built with Electron, React, and TypeScript on the frontend and FastAPI on the backend. Thrice Nomisma streams live tick-by-tick price and order-book data over authenticated sessions and layers institutional-style quantitative analysis on top.

**Status:** In progress

## Overview

Thrice Nomisma aims to bring the depth of a professional trading terminal to Indian markets — real-time data, a large indicator library, multi-timeframe strategy signals, and a custom UI built for fast, focused reading of market state.

## Features

### Market data
- Live tick-by-tick price feeds and order-book data over secure, authenticated sessions
- Broad symbol support for NSE/BSE equities

### Indicator & signal engine
- ~100-indicator engine spanning 7 categories, each with configurable parameters
- Institutional-grade additions: order flow pressure, signed ADX, RSI divergence, Stochastic RSI, CCI, Williams %R, OBV, Aroon, Ultimate Oscillator, Choppiness Index, Keltner squeeze, rolling Z-score
- Multi-timeframe strategy engine (1-minute through monthly) across 11 strategy modules, from scalping to monthly horizons
- Quantitative factor engine covering trend, momentum, volatility, and volume/flow signals
- Signal-quality scoring and a signal arbitration layer with regime-aware confidence discounting

### Signal intelligence
- Market regime detection
- Signal consensus bars and a conflict detector
- Category heatmap for at-a-glance indicator agreement
- Timeframe consensus strip
- AI explainability for signals, powered by a local reasoning engine (no external LLM API dependency)
- FinBERT-based sentiment engine with in-memory caching and headline fallback

### Visualization
- 3D indicator waveform (Three.js) with per-category color palettes, click-to-pin tooltips, and legend-based category isolation
- Integrated charting and technical-analysis layer
- Plane-overlap analysis view backed by a Python engine using Wilson confidence intervals and Shannon entropy

### Risk management
- Shared risk-management utilities across strategies
- Hardened risk engine with an F-grade hard block for high-risk conditions

### Design
- Custom void-black theme aimed at Bloomberg/cursor.com-level polish
- JetBrains Mono for data, Space Grotesk for headers, amber accents, sharp corner radii, and dedicated motion tokens
- Accessibility fixes throughout
- Landing page as a proper welcome screen ahead of the trading portal
- Consistent Electron window chrome theming (popup windows, title bar overlay, scrollbar, nav transparency)

## Tech stack

**Frontend:** Electron, React, TypeScript
**Backend:** Python, FastAPI
**Data/analysis:** NaN/Inf-safe JSON serialization for indicator output, Wilson confidence intervals, Shannon entropy
**3D/visualization:** Three.js
**NLP:** FinBERT (sentiment)

## Architecture notes

- Backend indicator and strategy logic lives primarily in `strategy_utils.py` and the risk engine (`risk.py`)
- Frontend and backend communicate over authenticated sessions for live data
- Started as a React + FastAPI + yfinance capstone project (Phase 1, ~June 2025), including early TradingView widget embedding and a TradingView-style dark theme, before evolving into the current Electron-based terminal

## Known issues

- Actively debugging an Electron blank-screen issue tied to the `package.json` `main` field

## Getting started

```bash
# Clone the repository
git clone <repo-url>
cd thrice-nomisma

# Install frontend dependencies
npm install

# Install backend dependencies
cd backend
pip install -r requirements.txt
cd ..

# Run the backend
uvicorn main:app --reload

# Run the Electron app
npm start
```

## Roadmap

- [ ] Resolve Electron blank-screen bug
- [ ] Expand strategy module coverage
- [ ] Broaden backtesting support
- [ ] Production-hardening for live trading use

---

**Author:** Pushkar Kumar || Mrigank Rautela || Vinayak Uttam Killedar
