# GrinderAlpha

[English](README.md) | 中文

一个工程化的投资决策系统 —— 确定性引擎负责纪律，多智能体辩论层负责判断。

## 概述

GrinderAlpha 把投资决策过程工程化为一个系统。从底部识别、估值、买点选择，到止损监控与复盘，每一步都被编码为可重复、可回测的规则。在确定性层之上，是一个多智能体 LLM 辩论引擎，把相互冲突的观点综合成结构化决策。目标是让纪律由系统来执行，减少人性的干扰 —— 追涨、舍不得割肉。

这**不是**高频交易。节奏是日、周、月 —— 低频，但严格。追求的是纪律的确定性，而非速度。

## 设计原则

- **方法论即代码** —— 每一种决策类型（买点、估值、支撑位、止损）都被提炼为可陈述、可重复、可回测的规则。
- **Provider 而非硬依赖** —— 数据访问、策略参数、底部档案、持仓，各自抽象在 Provider 接口之后，让引擎与数据的来源解耦。
- **机器执行纪律** —— 纪律违逆人性，所以交给代码。

## 架构

两层，分工明确：

- **确定性引擎**（`investment/` + 顶层引擎）—— 规则与回测。底部加速、估值、支撑位、Black-Scholes。纯数学，零第三方依赖。
- **LLM 辩论引擎**（`investment/debate_engine/`）—— 多智能体辩论，把原始数据转化为结构化决策。

整条流水线，从左到右：

```mermaid
flowchart LR
    P["Providers<br/>数据 · 参数 · 档案 · 持仓"] --> E["确定性引擎<br/>底部 · 估值 · 狙击 · 支撑 · 卖出监控 · 回测"]
    E --> R["DecisionReport<br/>统一 schema · 推导链"]
    R --> D["LLM 辩论引擎<br/>多智能体 · 对抗式"]
    D --> O["建议<br/>非订单"]
```

辩论引擎按流水线编排多个 LLM 智能体：

```mermaid
flowchart LR
    A[AnalysisInput] --> B{情景辩论<br/>多方 vs 空方}
    B --> C[情景裁判]
    C --> D[交易员<br/>模拟]
    D --> E{风险辩论<br/>激进 / 保守 / 中性}
    E --> F[组合经理]
    F --> G[DebateResult]
```

设计亮点：

- **对抗式辩论** —— 多方与空方智能体彼此交锋，而非单个 LLM 给出一个观点。
- **分层模型** —— 裁判/组合经理节点用更强的模型；辩论方用更快的模型。
- **上下文压缩** —— 压缩器在多轮辩论中封顶上下文。
- **影子模式** —— 辩论先以只读方式与确定性输出并行运行，证明自己后才影响最终建议。

LLM 产出的是*建议*，不是订单。这里没有任何东西会自动下单。

## 免责声明

本项目仅用于**教育与研究目的**。

- 不构成真实交易或投资建议
- 不作任何形式的保证
- 作者不对财务损失承担任何责任
- 过往表现不代表未来结果

## 快速开始

```bash
git clone https://github.com/edge2012/GrinderAlpha.git
cd GrinderAlpha

# 1. Black-Scholes 期权定价（纯 Python，零依赖）
python3 -c "from investment.options_estimator import bs_put_price; print(bs_put_price(94, 82, 30/365, 0.04, 0.60))"

# 2. 回测（先装依赖）
pip install -r requirements.txt
python -m backtest.run --list                          # 列出所有回测
python -m backtest.run entry_signal --symbol sh000300  # 对沪深300跑一个
```

确定性引擎只依赖 Python 标准库。回测需要 `numpy`/`pandas`/`scipy`；估值数据兜底需要 `akshare`。见 `requirements.txt`。

## 配置

所有功能都跑在免费公开数据上，无需 key —— 只有 LLM 辩论引擎需要一个。

| 功能 | 需要的 Key | 说明 |
|------|-----------|------|
| 确定性引擎（底部、估值、支撑、期权） | 无 | 免费公开数据（腾讯行情、CBOE） |
| 估值数据兜底 | 无 | `akshare`（legulegu / 蛋卷），免费 |
| `investment/debate_engine/`（LLM 辩论） | `OPENAI_API_KEY` + `OPENAI_BASE_URL` | 任意 OpenAI 兼容端点 |

### LLM 后端（仅辩论引擎）

辩论引擎使用 OpenAI 兼容客户端（`langchain_openai.ChatOpenAI`），因此任何讲 OpenAI `/v1` 协议的端点都能用 —— OpenAI、DeepSeek、Qwen、GLM，或自托管 vLLM / LM Studio。设置两个变量：

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://api.openai.com/v1
```

常见端点：

| Provider | `OPENAI_BASE_URL` |
|----------|-------------------|
| OpenAI | `https://api.openai.com/v1` |
| DeepSeek | `https://api.deepseek.com/v1` |
| 自托管 vLLM / LM Studio | `http://localhost:8000/v1` |

同样的方式适用于任何 OpenAI 兼容 provider —— 把 `OPENAI_BASE_URL` 指向它的 `/v1` 端点即可。

> **向后兼容。** `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL`（以及 `LLM_API_KEY` / `LLM_BASE_URL`）仍会被识别，保留只是为了兼容 `OPENAI_*` 约定之前的旧配置。新用户忽略它们，设置 `OPENAI_API_KEY` + `OPENAI_BASE_URL` 即可。

## 仓库结构

```
grinderalpha/
├── backtest/                       # 回测入口（core / data / run）
├── bottom_accelerator.py           # 底部加速：趋势线 + DCA 倍率
├── valuation_engine.py             # 估值：分类型 PE/PB 百分位
├── investment/                     # 核心包（引擎 + provider + 辩论）
│   ├── data_access.py              # DataAccess provider（行情 + 估值）
│   ├── param_provider.py           # ParamProvider（策略参数）
│   ├── profile_provider.py         # ProfileProvider（底部档案）
│   ├── support_levels.py           # 支撑位提取
│   ├── options_estimator.py        # 纯 Python Black-Scholes（兜底）
│   ├── cboe_options.py             # CBOE 期权链客户端
│   ├── decision_report.py          # 统一决策报告 schema（Action + resolve_action）
│   ├── methodologies/              # 买点方法论（base + sniper_ah）
│   ├── sell_monitors/              # 卖出监控（PositionProvider + 3 策略）
│   └── debate_engine/              # LLM 多智能体辩论
├── data/bottom_profiles/           # 示例底部档案
├── examples/                       # 教学示例
└── strategy_params.example.json    # 策略参数示例（复制后按需调整）
```

## 核心模块

确定性层围绕决策生命周期组织。每个模块都是纯 Python、跑在免费公开数据上（除非特别标注）；只有辩论引擎需要 key。

| 阶段 | 模块 | 作用 |
|------|------|------|
| **买入** | `bottom_accelerator.py` | 对历史大底拟合对数线性趋势线，按价格低于趋势线的深度确定 DCA 倍率（各指数独立校准） |
| **买入** | `investment/methodologies/sniper_ah.py` | 「好公司 + 极端便宜」：PE 回到历史底部区间，且回撤触及极值 |
| **估值** | `valuation_engine.py` | 分类型 PE/PB 百分位（宽基 / 红利 / 行业 / AI 链 / 港股），多源优雅降级 |
| **保护** | `investment/support_levels.py` | 从真实历史回撤底部提取 S1/S2 —— 支撑是保护，不是行权价的锚 |
| **保护** | `investment/sell_monitors/` | 通过 `PositionProvider` 接口执行卖出 / 止损 / 回补（3 个策略） |
| **决策** | `investment/decision_report.py` | 统一 `DecisionReport` schema：动作 + 逐维度检查 + 推导链 `trace` |
| **增强** | `investment/cboe_options.py` + `options_estimator.py` | 真实 CBOE 期权链（流动性门槛）+ 纯 Python Black-Scholes 兜底 |
| **验证** | `backtest/` | 统一入口 → 长期收益 / 胜率 / 最大回撤，附带数据来源声明 |
| **辩论** | `investment/debate_engine/` | 多智能体 LLM 辩论（唯一需要 key 的模块） |

三点值得单独说明，因为这是「工程」而非「AI」真正起作用的地方：

- **支撑是保护。** `support_levels.py` 从真实历史回撤底部提取 S1/S2，而非任意乘数。
- **估算是兜底，不是承诺。** `options_estimator.py`（纯 Python Black-Scholes）在一次实盘测试证明比真实 CBOE 链偏差 55 个百分点后，被降级为文档化的兜底路径。
- **止损压一切。** `decision_report.resolve_action` 执行优先级阶梯 —— 止损 > 止盈 > 加仓 > 建仓。

## 已知限制

| 领域 | 状态 |
|------|------|
| 美股方法论（trend / value / growth / turnaround） | 规划中 |
| 宏观 regime 层（多指标姿态分类） | 规划中 |
| 温度权重 | 经验设定；计划从姿态数据反推 |
| 辩论引擎输出质量 | 取决于底层 LLM；影子模式验证后才启用 |
| 期权回测 | 较新 —— CBOE 路径 2026-08 加入 |

## License

[MIT](LICENSE)
