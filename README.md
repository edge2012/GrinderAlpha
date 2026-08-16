# InvestmentOS

English | [中文](README.zh.md)

An engineering-grade investment decision system — from macro positioning to trade review, a complete closed loop.

## Overview

InvestmentOS turns the investment decision process into an engineered system. From macro regime analysis, valuation, and buy-point identification to stop-loss and retrospective review, each step is codified into repeatable, backtestable rules. On top of the deterministic layer sits a multi-agent LLM debate engine that synthesizes conflicting views into a structured decision. The goal is to let discipline be executed by the system, reducing the influence of human emotion — chasing rallies, hesitating to cut losses.

This is **not** high-frequency trading. The cadence is daily, weekly, monthly — low frequency, but strict. The aim is the certainty of discipline, not speed.

## Design Principles

- **Methodology as code** — each decision type (buy point, valuation, stop-loss, support level) is distilled into rules that can be stated, repeated, and backtested.
- **Signal systematization** — a dozen scattered indicators (macro, valuation, trend, sentiment) converge into one signal language, so the right action is obvious at a glance.
- **Discipline by machine** — discipline is counter to human nature, so it is handed to code.

## How AI Is Used

InvestmentOS has two layers with a clear division of labor:

- **Deterministic engines** (`engine/`) — rules and backtests. Market posture, valuation, support levels, Black-Scholes. Pure math, zero third-party dependencies.
- **LLM debate engine** (`debate_engine/`) — a multi-agent debate that turns raw data into a structured decision.

The debate engine orchestrates several LLM agents in a pipeline:

```
AnalysisInput → Scenario Debate (Bull vs Bear) → Scenario Judge
              → Trader (simulated) → Risk Debate (Aggressive / Conservative / Neutral)
              → Portfolio Manager → DebateResult
```

Design highlights:

- **Adversarial debate** — bull and bear agents argue against each other, rather than a single LLM producing one opinion.
- **Tiered models** — judge/PM nodes use a stronger model (`deepseek-v4-pro`); debaters use a faster one (`deepseek-v4-flash`).
- **Context compression** — a compressor caps context at 16K tokens across debate rounds.
- **Shadow mode** — the debate runs alongside the baseline first; it only replaces the baseline after proving itself.

The LLM produces a *recommendation*, not an order. Nothing here places trades automatically.

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
python3 -m engine.buy_point_engine SPY
```

The deterministic engines depend only on the Python standard library. `macro_pipeline.py` needs `akshare`, `pandas`, and `numpy`. See `requirements.txt`.

## Configuration

Most features need no API key — only the LLM debate engine requires one.

| Feature | Key required | Notes |
|---------|-------------|-------|
| Deterministic engines (posture, valuation, options, support) | None | Free public data (Tencent quotes, CBOE) |
| BuyPointEngine methodologies (A/H, value, growth, turnaround) | None | Tencent quotes, no key |
| `trend_etf.py` (US ETF monthly bars) | `ALPHA_VANTAGE_API_KEY` | Free key; degrades gracefully if missing |
| `debate_engine/` (LLM debate) | `DEEPSEEK_API_KEY` | From the DeepSeek platform |

### Setting keys

Either approach works:

**Option 1 — environment variables:**

```bash
export DEEPSEEK_API_KEY=sk-...
export ALPHA_VANTAGE_API_KEY=...
```

**Option 2 — a `.env` file** (auto-loaded by the debate engine):

```bash
# .env in the project root
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
ALPHA_VANTAGE_API_KEY=...
```

The debate engine's config loader reads `.env` automatically (path via `DOTENV_PATH`, default `.env`) and only sets variables not already in the environment — so env vars always win. The `.env` file is gitignored, so your keys stay out of version control.

## Repository Structure

```
investment-os/
├── engine/                          # Deterministic decision engines (zero third-party deps)
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
├── debate_engine/                   # LLM multi-agent debate engine
│   ├── engine.py                    # Orchestration (Bull/Bear → Judge → Trader → Risk → PM)
│   ├── prompts.py / zh_prompts.py   # Prompts (English / Chinese)
│   ├── compressor.py                # Context compression across debate rounds
│   ├── quality.py                   # Argument quality evaluation
│   └── state.py / config.py         # Data models / configuration
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
| Debate engine output quality depends on the underlying LLM | Shadow mode validates before promotion |
| Macro DCA multiplier not yet wired into position builder | Signal produced, display-only |
| A-share lacks cycle-manager coverage | US has a 3-signal state machine, A-share does not |
| Temperature weights are experience-set | Back-infer from 2+ years of posture data (planned) |
| Options backtests are recent | CBOE path added 2026-08 |

## License

[MIT](LICENSE)
