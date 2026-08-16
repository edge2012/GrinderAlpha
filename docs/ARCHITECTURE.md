# Architecture

InvestmentOS separates **deterministic computation** from **LLM judgment**. Two layers, a strict division of labor.

## System Overview

```
                ┌─────────────────────────────────────────────┐
                │               Decision Pipeline             │
                │                                             │
  Market data ──┤  Macro → Posture → Valuation → Buy Point    │
                │     │        │          │            │       │
                │     ▼        ▼          ▼            ▼       │
                │  ┌─────────────────────────────────────┐    │
                │  │   Layer 1: Deterministic Engines    │    │
                │  │   (engine/)                         │    │
                │  │   · market_state_engine   5 postures│    │
                │  │   · valuation_engine      PE/PB pct │    │
                │  │   · bottom_accelerator    DCA mult  │    │
                │  │   · buy_point_engine      plugin    │    │
                │  │   · cboe_options / options_estimator│   │
                │  │   · support_levels                  │    │
                │  └─────────────────────────────────────┘    │
                │                    │                        │
                │                    ▼                        │
                │  ┌─────────────────────────────────────┐    │
                │  │   Layer 2: LLM Debate Engine         │    │
                │  │   (debate_engine/)                   │    │
                │  │   Bull vs Bear → Judge → Trader      │    │
                │  │   → Risk debate → PM decision        │    │
                │  └─────────────────────────────────────┘    │
                │                    │                        │
                │                    ▼                        │
  Decision ─────┤  Record → Review (retrospective)            │
                └─────────────────────────────────────────────┘
```

## Layer 1: Deterministic Engines (`engine/`)

Pure computation — rules, math, and backtests. No LLM, no third-party dependencies (standard library only).

| Engine | Role |
|--------|------|
| `market_state_engine.py` | Aggregates 15+ indicators into 5 market postures |
| `valuation_engine.py` | Per-category PE/PB percentile valuation |
| `bottom_accelerator.py` | Log-linear trendline on drawdown bottoms → DCA multiplier |
| `buy_point_engine.py` | Routes a symbol to the right methodology plugin |
| `cboe_options.py` | Real CBOE options chain + liquidity gate |
| `options_estimator.py` | Pure-Python Black-Scholes (fallback) |
| `support_levels.py` | Support extraction from historical drawdown bottoms |
| `macro_pipeline.py` | 7-indicator macro regime classification |

The BuyPointEngine is the cleanest example of the plugin pattern: the top layer defines only routing and an output schema; `methodologies/` holds five independent implementations, one per market × asset type.

## Layer 2: LLM Debate Engine (`debate_engine/`)

A multi-agent pipeline that turns raw data into a structured decision. The engine is deliberately **self-contained** — it receives already-formatted `AnalysisInput` and returns `DebateResult`, importing none of the deterministic engines.

### Pipeline

```
AnalysisInput
    │
    ├── Scenario Debate  (Bull vs Bear, up to 2 rounds)
    │       └── Scenario Judge  (picks the stronger case)
    │
    ├── Trader  (simulated position sizing)
    │
    ├── Risk Debate  (Aggressive / Conservative / Neutral)
    │
    └── Portfolio Manager  (final recommendation)
            │
            ▼
       DebateResult
```

### Agent Roles

| Agent | Responsibility |
|-------|----------------|
| Bull / Bear | Argue opposite sides of the trade thesis |
| Scenario Judge | Weigh the two cases, declare the stronger one |
| Trader | Propose a concrete position |
| Risk debaters | Challenge the plan from three risk postures |
| Portfolio Manager | Synthesize everything into a final decision |

### Key Design Decisions

1. **Shadow mode first.** The debate runs *alongside* the baseline; it only replaces the baseline after proving itself. A disciplined, data-driven rollout.
2. **Tiered models.** Judge/PM nodes use a stronger model (`deepseek-v4-pro`); debaters use a faster one (`deepseek-v4-flash`). Cost and quality are balanced per role.
3. **Context compression.** A compressor summarizes earlier rounds so the context window stays under a 16K-token cap.
4. **Self-contained.** No dependency on the deterministic engines — the LLM layer is swappable and testable in isolation.
5. **The LLM recommends, it does not execute.** Output is a structured `DebateResult`, never a trade order.

## The Decision Loop

The full system is a closed loop:

```
Macro regime (monthly)
  → Market posture (daily)
    → Buy point scan (weekly)
      → Options structuring (per candidate)
        → Decision recorded
          → Retrospective review (monthly, win-rate + decision quality)
```

Each signal carries backtest evidence where available. The loop's purpose is not to predict the market — it is to make every decision *reproducible* and *reviewable*.

## See Also

- `README.md` — overview and quick start
- `engine/examples/demo_options.py` — a runnable slice of the deterministic layer
- `debate_engine/` — the LLM layer's source
