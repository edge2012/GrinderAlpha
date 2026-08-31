# backtest — 回测核心与统一入口

公开库可运行的回测层。核心计算与数据获取完全解耦，数据源按市场路由，无私有账户/凭证依赖。

## 统一入口

```bash
python -m backtest.run --list          # 列出全部回测
python -m backtest.run <name>          # 运行指定回测
python -m backtest.run <name> --symbol X   # 只跑指定标的
```

| name | 回测 | 市场 | 数据源 |
|------|------|------|--------|
| `entry_signal` | 建仓信号（命题1-4，偏离 MA20 + MA5/MA10 交叉） | A/H 宽基 | 腾讯 fqkline |
| `tail_risk` | 尾部风险补测（深回撤后 120/250/500 日收益分布） | A/H | 腾讯 fqkline |
| `panic` | 恐慌跌幅反弹（单月跌幅分桶 → 前向 1/3/6 月收益） | A/H 指数月K | 腾讯 fqkline |
| `dca_compare` | 等额预算 DCA 对比（无门禁 vs 趋势 vs 均值回归） | 美股 ETF | Yahoo v8 |
| `normal_years` | 正常年份保护层成本（VIX 分层下 Spread/Put 理论成本） | 美股 SPY+VIX | Yahoo v8 |

## 数据源路由

统一接口 `backtest.data.get_history(symbol, market, freq)`：

- **A/H**（`market="A"` 或 `"HK"`）→ 腾讯 fqkline，日K/月K 稳定。
- **美股**（`market="US"`）→ Yahoo v8 chart API（免费 adjusted 日线），带重试 + 本地缓存降级。
- **VIX**（`symbol="^VIX"`）→ 同为 Yahoo v8。

> 已知限制：腾讯 fqkline 不支持美股历史K线（只返首尾数根）；stooq 有反爬挑战；yfinance 易限流。故美股统一走 Yahoo v8，免费端点偶发 HTTP 429，重试与缓存已内建。

## 架构

```
backtest/
├── data.py      # 统一历史K线数据层（路由 + 重试 + 缓存降级）
├── core.py      # 纯计算核心（无 I/O，可单测）
├── run.py       # 注册表 + CLI 入口
└── *.py         # 原始研究脚本（含 yfinance 私有依赖，仅供研究复现）
```

核心函数与数据获取完全分离，`core.py` 全部为「输入数据 → 输出结果」的纯函数，由 `tests/test_backtest_core.py` 单测覆盖。
