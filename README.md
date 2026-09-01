# GrinderAlpha

English | [中文](README.zh.md)

An engineering-grade investment decision system — deterministic engines for discipline, a multi-agent debate layer for judgment. Every decision shows its work, so it can be read and backtested instead of trusted on faith.

## Why GrinderAlpha

Chasing rallies and hesitating to cut losses are human nature — discipline is the part humans are worst at. Most "AI trading" tools answer with a black-box signal. GrinderAlpha answers differently: it turns discipline into deterministic rules (when to buy, add, trim, exit, rebuy), and every decision it emits carries a derivation trace — which data went in, which rule fired, what came out.

This is **not** high-frequency trading. The cadence is daily, weekly, monthly — low frequency, but strict. The goal is the certainty of discipline, not speed.

## What a decision looks like

One command, no key, no dependencies — a complete, human-readable decision report:

```bash
python3 examples/demo_decision_report.py
```

```text
📋 决策报告 | 510300 ETF定投
════════════════════════════════════════════════════════

结论  沪深300 距历史底部趋势线 -18% → 落入「恐慌区」，DCA 倍率 3x，估值确认 upgraded
      动作 ADD · 信心 8/10 · 区间 ¥4.0-4.3（恐慌区）

证据
  ✓ 大底趋势     现价折算指数 4166，趋势线投影 5080，偏离 -18%  [腾讯行情(实时)]
  ✓ 估值       指数 PE 分位 28% → 不贵  [akshare]
  ✓ 仓位纪律     单标的占比 12% < 15% 纪律线  [用户持仓]
  ✓ 止损       距 50MA -3% → 建仓期仅展示不拦截  [腾讯日K]

推导
  ① 拉取历史大底
    输入 BOTTOM_DEFINITIONS["沪深300"]
    规则 读历史大底常量
    结果 [807(2005-06), 2836(2014-06)...]
  ② 拟合趋势线
    输入 上一步的底点
    规则 对数线性拟合
    结果 年化 +8.1%, r²=0.98
  ③ 投影当前
    输入 slope + intercept
    规则 趋势线外推
    结果 投影 5080 点
  ④ 拉取实时价
    输入 腾讯 qt.gtimg.cn
    规则 实时行情
    结果 ¥4.20（折算指数 4166）
  ⑤ 判定区域
    输入 4166 vs 5080
    规则 (4166-5080)/5080 = -18%
    结果 落入恐慌区 → DCA 3x

数据  历史大底常量(static,2005-06 起) · 腾讯行情(realtime,now) · akshare 估值(realtime,now)

回测  python -m backtest.run entry_signal --symbol sh000300
数据截至 2026-08-31 · schema v1.0
```

Three layers in one report: a **conclusion** (what to do now), **evidence** (each dimension with its rule and data source), and a **derivation trace** (every step: input → rule → output). The trace is the whole point — it turns a recommendation into something you can audit. *(The demo prints in Chinese; run the command to reproduce this exact output.)*

## Design Principles

- **Methodology as code** — every decision type (buy point, valuation, support, stop-loss) is distilled into rules that can be stated, repeated, and backtested.
- **Providers, not hard dependencies** — data access, strategy params, bottom profiles, and positions are abstracted behind interfaces, so the engines stay decoupled from where that data comes from.
- **Discipline by machine** — discipline is counter to human nature, so it is handed to code.
- **Long-term, not prediction** — the system sells discipline and data, not "will it go up tomorrow". Good asset + good price + long holding.

## Architecture

Two layers, a clear division of labor:

- **Deterministic engines** (`investment/` + top-level engines) — rules and backtests. Bottom acceleration, valuation, support levels, Black-Scholes. Pure math, zero third-party dependencies.
- **LLM debate engine** (`investment/debate_engine/`) — a multi-agent debate that turns raw data into a structured decision.

The pipeline, end to end:

```mermaid
flowchart LR
    P["Providers<br/>data · params · profiles · positions"] --> E["Deterministic engines<br/>bottom · valuation · sniper · support · sell-monitors · backtest"]
    E --> R["DecisionReport<br/>unified schema · trace"]
    R --> D["LLM debate engine<br/>multi-agent · adversarial"]
    D --> O["Recommendation<br/>not an order"]
```

The debate engine orchestrates several LLM agents in a pipeline:

```mermaid
flowchart LR
    A[AnalysisInput] --> B{Scenario Debate<br/>Bull vs Bear}
    B --> C[Scenario Judge]
    C --> D[Trader<br/>simulated]
    D --> E{Risk Debate<br/>Aggressive / Conservative / Neutral}
    E --> F[Portfolio Manager]
    F --> G[DebateResult]
```

Design highlights:

- **Adversarial debate** — bull and bear agents argue against each other, rather than a single LLM producing one opinion.
- **Tiered models** — judge/PM nodes use a stronger model; debaters use a faster one.
- **Context compression** — a compressor caps context across debate rounds.
- **Shadow mode** — the debate first runs read-only alongside the deterministic output, and only affects the final recommendation after proving itself.

The LLM produces a *recommendation*, not an order. Nothing here places trades automatically.

## Lifecycle

A position flows through a full life cycle, expressed with one action vocabulary:

| Action | Meaning | Engine |
|--------|---------|--------|
| `BUY` | open a position | `bottom_accelerator` / `sniper_ah` |
| `ADD` | DCA add (buy more as it drops) | `bottom_accelerator` |
| `HOLD` | hold | — |
| `TRIM` | take profit / reduce | `sell_monitors` |
| `EXIT` | stop-loss / close | `sell_monitors` |
| `REBUY` | rebuy after trim | `sell_monitors` |
| `WAIT` | no position, conditions unmet | — |

When multiple signals conflict, one priority ladder resolves them — stop-loss beats everything, take-profit beats adding, adding beats new positions:

```
EXIT > TRIM > ADD > BUY > REBUY > HOLD / WAIT
```

## Disclaimer

This project is for **educational and research purposes only**.

- Not intended as real trading or investment advice
- No guarantees of any kind
- The author assumes no liability for financial losses
- Past performance does not indicate future results

## Quick Start

```bash
git clone https://github.com/edge2012/GrinderAlpha.git
cd GrinderAlpha

# 1. See a full decision report (zero deps, no key)
python3 examples/demo_decision_report.py

# 2. Black-Scholes option pricing (pure Python, zero dependencies)
python3 -c "from investment.options_estimator import bs_put_price; print(bs_put_price(94, 82, 30/365, 0.04, 0.60))"

# 3. Backtests (install dependencies first)
pip install -r requirements.txt
python -m backtest.run --list                          # list all backtests
python -m backtest.run entry_signal --symbol sh000300  # run one on CSI 300
```

The deterministic engines depend only on the Python standard library. Backtests need `numpy`/`pandas`/`scipy`; valuation data fallback needs `akshare`. See `requirements.txt`.

## Configuration

Everything runs on free public data with no key — only the LLM debate engine needs one.

| Feature | Key required | Notes |
|---------|-------------|-------|
| Deterministic engines (bottom, valuation, support, options) | None | Free public data (Tencent quotes, CBOE) |
| Valuation data fallback | None | `akshare` (legulegu / Danjuan), free |
| `investment/debate_engine/` (LLM debate) | `OPENAI_API_KEY` + `OPENAI_BASE_URL` | Any OpenAI-compatible endpoint |

### LLM provider (debate engine only)

The debate engine uses an OpenAI-compatible client (`langchain_openai.ChatOpenAI`), so it works with any endpoint that speaks the OpenAI `/v1` protocol — OpenAI, DeepSeek, Qwen, GLM, or a self-hosted vLLM / LM Studio. Set both variables:

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.openai.com/v1
```

Common endpoints:

| Provider | `OPENAI_BASE_URL` |
|----------|-------------------|
| OpenAI | `https://api.openai.com/v1` |
| DeepSeek | `https://api.deepseek.com/v1` |
| Self-hosted vLLM / LM Studio | `http://localhost:8000/v1` |

The same pattern extends to any OpenAI-compatible provider — just point `OPENAI_BASE_URL` at its `/v1` endpoint.

> **Backward compatibility.** `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` (and `LLM_API_KEY` / `LLM_BASE_URL`) are still recognized, kept only so existing setups that predate the `OPENAI_*` convention keep working. New users can ignore them and just set `OPENAI_API_KEY` + `OPENAI_BASE_URL`.

## Repository Structure

```
grinderalpha/
├── backtest/                       # Backtest runner (core / data / run)
├── bottom_accelerator.py           # Bottom acceleration: trendline + DCA multiplier
├── valuation_engine.py             # Valuation: per-category PE/PB percentile
├── investment/                     # Core package (engines + providers + debate)
│   ├── data_access.py              # DataAccess provider (quotes + valuation)
│   ├── param_provider.py           # ParamProvider (strategy params)
│   ├── profile_provider.py         # ProfileProvider (bottom profiles)
│   ├── support_levels.py           # Support-level extraction
│   ├── options_estimator.py        # Pure-Python Black-Scholes (fallback)
│   ├── cboe_options.py             # CBOE options chain client
│   ├── decision_report.py          # Unified decision report schema (Action + resolve_action)
│   ├── methodologies/              # Buy-point methodologies (base + sniper_ah)
│   ├── sell_monitors/              # Sell monitors (PositionProvider + 3 strategies)
│   └── debate_engine/              # LLM multi-agent debate
├── data/bottom_profiles/           # Sample bottom profiles
├── examples/                       # Runnable demos (decision report, …)
└── strategy_params.example.json    # Example strategy params (copy and tune)
```

## Core Modules

The deterministic layer is organized around the decision lifecycle. Every module is pure Python and runs on free public data unless noted; only the debate engine needs a key.

| Stage | Module | What it does |
|-------|--------|--------------|
| **Buy** | `bottom_accelerator.py` | Fits a log-linear trendline through historical bottoms and sizes the DCA multiplier by how far below it the price sits (per-index calibration) |
| **Buy** | `investment/methodologies/sniper_ah.py` | "Good company + extreme cheapness": PE back to its historical bottom **and** drawdown at extremes |
| **Value** | `valuation_engine.py` | Per-category PE/PB percentile (broad / dividend / sector / AI-chain / HK), multi-source with graceful degradation |
| **Protect** | `investment/support_levels.py` | S1/S2 support derived from real drawdown bottoms — protection, not a strike anchor |
| **Protect** | `investment/sell_monitors/` | Sell / stop / rebuy through the `PositionProvider` interface (3 strategies) |
| **Decide** | `investment/decision_report.py` | Unified `DecisionReport` schema: action + per-dimension checks + derivation `trace` |
| **Enhance** | `investment/cboe_options.py` + `options_estimator.py` | Real CBOE chain (liquidity-gated) with a pure-Python Black-Scholes fallback |
| **Verify** | `backtest/` | Unified runner → long-term return / win-rate / max drawdown, with a data-source declaration |
| **Debate** | `investment/debate_engine/` | Multi-agent LLM debate (the only key-requiring module) |

Three details worth calling out, because this is where the engineering — not the "AI" — does the work:

- **Support is protection.** `support_levels.py` derives S1/S2 from real historical drawdown bottoms, not arbitrary multipliers.
- **Estimation is a fallback, not a promise.** `options_estimator.py` (pure-Python Black-Scholes) was demoted to a documented fallback after a live test showed it off by 55 percentage points against the real CBOE chain.
- **Stop-loss beats everything.** `decision_report.resolve_action` enforces a priority ladder — stop-loss > take-profit > adding > new positions.

## Known Limitations

| Area | Status |
|-------|--------|
| US methodologies (trend / value / growth / turnaround) | Planned |
| Macro regime layer (multi-indicator posture classification) | Planned |
| Temperature weights | Experience-set; back-inferred from posture data (planned) |
| Debate engine output quality | Depends on the underlying LLM; shadow mode validates before promotion |
| Options backtests | Recent — CBOE path added 2026-08 |

## License

[MIT](LICENSE)
