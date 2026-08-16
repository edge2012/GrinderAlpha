# InvestmentOS

[English](README.md) | 中文

工程化的投资决策系统——从宏观定位到交易复盘的完整闭环。

## 概述

InvestmentOS 把投资决策过程工程化：从宏观环境判断、估值分析、买入点识别，到止损和复盘，每一步都固化成可复述、可回测的规则。在确定性引擎之上，还有一个多智能体 LLM 辩论引擎，把相互冲突的观点综合成结构化的决策。目标是让纪律由系统执行，弱化人性的弱点——追涨杀跌、该止损时犹豫。

这不是高频交易。系统的节奏是按天、按周、按月，低频但严格。追求的不是速度，是纪律的确定性。

## 设计原则

- **方法论沉淀** — 每一类决策（买点、估值、止损、支撑位）都沉淀成可复述、可回测的规则。
- **信号系统化** — 把十几个分散的指标（宏观、估值、趋势、情绪）收敛成一套统一的信号语言。
- **纪律交给机器** — 纪律是反人性的，所以交给代码。

## AI 是怎么用的

InvestmentOS 有两层，分工明确：

- **确定性引擎**（`engine/`）——规则和回测。市场状态、估值、支撑位、Black-Scholes。纯数学，零第三方依赖。
- **LLM 辩论引擎**（`debate_engine/`）——多智能体辩论，把原始数据变成结构化决策。

辩论引擎编排了多个 LLM agent，形成一条流水线：

```
AnalysisInput → 情景辩论（多 vs 空）→ 情景裁判
              → 模拟交易员 → 风控辩论（激进 / 保守 / 中性）
              → 组合经理 → DebateResult
```

设计亮点：

- **对抗式辩论** —— 多头和空头 agent 互相攻辩，而不是让单个 LLM 给出一个观点。
- **分层模型** —— 裁判/PM 节点用更强的模型（`deepseek-v4-pro`），辩手用更快的（`deepseek-v4-flash`）。
- **上下文压缩** —— 压缩器把多轮辩论的上下文控制在 16K token 以内。
- **影子模式** —— 辩论先与基线并行运行，验证有效后才替换基线。

LLM 产出的是**建议**，不是指令。这里没有任何东西会自动下单。

## 免责声明

本项目仅用于**教育和研究目的**。

- 不构成任何真实的交易或投资建议
- 不提供任何形式的保证
- 作者不对任何财务损失承担责任
- 过往表现不代表未来结果

## 快速开始

```bash
git clone https://github.com/edge2012/investment-os.git
cd investment-os

# 1. Black-Scholes 期权定价（纯数学，不需要数据）
python3 -c "from engine.options_estimator import bs_put_price; print(bs_put_price(94, 82, 30/365, 0.04, 0.60))"

# 2. 支撑位提取（内置 SPY 示例档案）
python3 engine/examples/demo_options.py

# 3. 买入点路由（完整输出需要实时行情 + API key）
python3 engine/buy_point_engine.py SPY
```

确定性引擎只依赖 Python 标准库。`macro_pipeline.py` 需要 `akshare`、`pandas`、`numpy`。见 `requirements.txt`。

## 配置

大多数功能不需要 API key——只有 LLM 辩论引擎需要一个。

| 功能 | 需要的 key | 说明 |
|------|-----------|------|
| 确定性引擎（姿势、估值、期权、支撑位） | 无 | 免费公开数据（腾讯行情、CBOE） |
| BuyPointEngine 方法论（A/H、价值、成长、拐点） | 无 | 腾讯行情，无需 key |
| `trend_etf.py`（美股 ETF 月K） | `ALPHA_VANTAGE_API_KEY` | 免费 key；缺失会优雅降级 |
| `debate_engine/`（LLM 辩论） | `DEEPSEEK_API_KEY` | 来自 DeepSeek 平台 |

### 设置 key

两种方式任选其一：

**方式一 —— 环境变量：**

```bash
export DEEPSEEK_API_KEY=sk-...
export ALPHA_VANTAGE_API_KEY=...
```

**方式二 —— `.env` 文件**（辩论引擎会自动加载）：

```bash
# 项目根目录下的 .env
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
ALPHA_VANTAGE_API_KEY=...
```

辩论引擎的配置加载器会自动读取 `.env`（路径通过 `DOTENV_PATH` 指定，默认 `.env`），并且只设置环境里还不存在的变量——所以环境变量永远优先。`.env` 文件已被 gitignore，不要把 key 提交进版本控制。

## 目录结构

```
investment-os/
├── engine/                          # 确定性决策引擎（零第三方依赖）
│   ├── market_state_engine.py       # 市场状态：5 姿势聚合
│   ├── bottom_accelerator.py        # 底部加速：对数线性趋势线 + DCA 倍率
│   ├── valuation_engine.py          # 估值：分类 PE/PB 分位
│   ├── macro_pipeline.py            # 宏观 Regime：7 指标分类
│   ├── strategy_param_loader.py     # 「参数不进 git」的安全模式
│   ├── buy_point_engine.py          # 买入点路由（插件化架构）
│   ├── cboe_options.py              # CBOE 期权链 + 流动性门禁
│   ├── options_estimator.py         # 纯 Python Black-Scholes（不依赖 scipy）
│   ├── support_levels.py            # 支撑位提取
│   ├── methodologies/               # 5 套市场方法论（插件层）
│   └── examples/                    # 可运行示例
├── debate_engine/                   # LLM 多智能体辩论引擎
│   ├── engine.py                    # 编排（多空 → 裁判 → 交易员 → 风控 → PM）
│   ├── prompts.py / zh_prompts.py   # 提示词（英文 / 中文）
│   ├── compressor.py                # 多轮辩论的上下文压缩
│   ├── quality.py                   # 论证质量评估
│   └── state.py / config.py         # 数据模型 / 配置
├── backtest/                        # 回测脚本（整理中）
├── data/                            # 示例数据（底部档案）
└── docs/                            # 方法论与限制（整理中）
```

## 核心模块

### BuyPointEngine — 插件化架构

`buy_point_engine.py` 只定义路由和输出 schema（`BuyPointResult`）。`methodologies/` 里有五套独立实现，按「市场 × 标的类型」分：

- `trend_etf.py` — 美股指数 ETF（趋势 + 估值）
- `value_us.py` — 美股价值股（PE 分位 + 回撤深度）
- `growth_us.py` — 美股成长股（PEG + 收入增速）
- `sniper_ah.py` — A/H 股（PE 锚 + 回撤锚）
- `turnaround_us.py` — 美股拐点股（赌基本面反转）

新增一个市场 = 实现一个新子类，顶层不用改。

### 期权链 — 让数据代替估算

`cboe_options.py` 读取 CBOE 真实 bid/ask 中间价，并设置流动性门禁（`bid=0` 直接拦截）。最初的方法用启发式估算隐含波动率，一次实测证明它高估了 55 个百分点，于是估算被降级为文档化的兜底方案（`options_estimator.py`）。

### Black-Scholes — 纯 Python

`options_estimator.py` 用纯 Python 实现 Black-Scholes，刻意不依赖 scipy，换取部署的简单。

### 支撑位 — 数据驱动

`support_levels.py` 从真实的历史回撤底部提取支撑位，而不是用任意系数拍脑袋。

## 已知限制

| 限制 | 现状 |
|------|------|
| 辩论引擎输出质量依赖底层 LLM | 影子模式验证后才转正式 |
| 宏观 DCA 倍率未接入建仓 | 信号已产出，仅展示 |
| A 股缺周期管理器 | 美股有 3 信号状态机，A 股没有 |
| 温度权重是经验设定 | 计划积累 2 年+ 数据后反推 |
| 期权回测样本还少 | CBOE 路径 2026-08 才加入 |

## License

[MIT](LICENSE)
