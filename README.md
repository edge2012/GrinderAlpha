# InvestmentOS

English | [中文](README.zh.md)

An engineering-grade investment decision system — from macro positioning to trade review, a complete closed loop.

## Overview

InvestmentOS turns the investment decision process into an engineered system. From macro regime analysis, valuation, and buy-point identification to stop-loss and retrospective review, each step is codified into repeatable, backtestable rules that converge into a unified signal language. The goal is to let discipline be executed by the system, reducing the influence of human emotion — chasing rallies, hesitating to cut losses.

This is **not** high-frequency trading. The cadence is daily, weekly, monthly — low frequency, but strict. The aim is the certainty of discipline, not speed.

## Design Principles

- **Methodology as code** — each decision type (buy point, valuation, stop-loss, support level) is distilled into rules that can be stated, repeated, and backtested.
- **Signal systematization** — a dozen scattered indicators (macro, valuation, trend, sentiment) converge into one signal language, so the right action is obvious at a glance.
- **Discipline by machine** — discipline is counter to human nature, so it is handed to code.

## Disclaimer

This project is for **educational and research purposes only**.

- Not intended as real trading or investment advice
- No guarantees of any kind
- The author assumes no liability for financial losses
- Past performance does not indicate future results

## Quick Start

```bash
git clone https://github.com/edge2012/investment-os.git
cd investment-os

# 1. Black-Scholes option pricing (pure math, no data needed)
python3 -c "from engine.options_estimator import bs_put_price; print(bs_put_price(94, 82, 30/365, 0.04, 0.60))"

# 2. Support-level extraction (with a sample SPY bottom profile)
python3 engine/examples/demo_options.py

# 3. Buy-point routing (full output needs live market data + API keys)
python3 engine/buy_point_engine.py SPY
```

The core engines depend only on the Python standard library — no third-party packages required. The exception is `macro_pipeline.py`, which needs `akshare`, `pandas`, and `numpy` (see `requirements.txt`).

## Repository Structure

```
investment-os/
├── engine/                          # Pure decision engines (zero third-party deps)
│   ├── market_state_engine.py       # Market state: 5-posture aggregation
│   ├── bottom_accelerator.py        # Bottom acceleration: log-linear trendline + DCA multiplier
│   ├── valuation_engine.py          # Valuation: per-category PE/PB percentile
│   ├── macro_pipeline.py            # Macro regime: 7-indicator classification
│   ├── strategy_param_loader.py     # "Params never enter git" security pattern
│   ├── buy_point_engine.py          # Buy-point router (plugin architecture)
│   ├── cboe_options.py              # CBOE options chain + liquidity gate
│   ├── options_estimator.py         # Pure-Python Black-Scholes (no scipy)
│   ├── support_levels.py            # Support-level extraction
│   ├── methodologies/               # 5 market methodologies (the plugin layer)
│   └── examples/                    # Runnable demos
├── backtest/                        # Backtest scripts (in progress)
├── data/                            # Sample data (bottom profiles)
└── docs/                            # Methodologies & limitations (in progress)
```

## Core Modules

### BuyPointEngine — plugin architecture

`buy_point_engine.py` defines only routing and the output schema (`BuyPointResult`). `methodologies/` holds five independent implementations, one per market × asset type:

- `trend_etf.py` — US index ETFs (trend + valuation)
- `value_us.py` — US value stocks (PE percentile + drawdown depth)
- `growth_us.py` — US growth stocks (PEG + revenue growth)
- `sniper_ah.py` — A/H stocks (PE anchor + drawdown anchor)
- `turnaround_us.py` — US turnaround plays (bet on fundamental inflection)

Adding a new market means implementing a new subclass — the top layer never changes.

### Options chain — data over estimation

`cboe_options.py` fetches real CBOE bid/ask mid-prices and enforces a liquidity gate (`bid=0` blocks). The original approach estimated implied volatility with a heuristic; a live test proved it wrong by 55 percentage points, so estimation was demoted to a documented fallback (`options_estimator.py`).

### Black-Scholes — pure Python

`options_estimator.py` implements Black-Scholes in pure Python, deliberately avoiding scipy for deployment simplicity.

### Support levels — data-driven

`support_levels.py` extracts support from real historical drawdown bottoms rather than arbitrary multipliers.

## Known Limitations

| Limitation | Status |
|-----------|--------|
| Macro DCA multiplier not yet wired into position builder | Signal produced, display-only |
| A-share lacks cycle-manager coverage | US has a 3-signal state machine, A-share does not |
| Temperature weights are experience-set | Back-infer from 2+ years of posture data (planned) |
| Options backtests are recent | CBOE path added 2026-08 |

## License

[MIT](LICENSE)
