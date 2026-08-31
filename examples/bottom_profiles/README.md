# 底部档案教学示例

本目录存放**教学示例**底部档案，演示 `investment/profile_provider.py` 消费的档案 schema，
供公开库用户理解「狙击方法论（SniperAH）」如何用 PE 锚 + 回撤锚做买入点判断。

## 字段说明

| 顶层字段 | 含义 |
|---|---|
| `symbol` | 标的代码（A股 6 位 / 港股 5 位，无前缀） |
| `market` | `A` 或 `HK` |
| `pe_anchor` | PE 估值锚：成熟期 PE 区间 + 历史底部 + 当前 PE |
| `drawdown_anchor` | 回撤锚：历史峰值→谷底回撤 + 当前价/ATH |
| `forward_returns` | 底部后 12 个月前向收益（校准「底部真的能买吗」） |
| `sniper_range` | 狙击线：`condition_pe` / `condition_dd` 双条件定义 |

## 两个口径不要混用

- **PE 锚**：不复权价格 ÷ 年度 EPS（成熟期大底 PE）
- **回撤锚**：后复权价格（保留分红效应）

## 建一个真实档案的路径

1. `generate_draft(symbol, name, market)` 产出骨架初稿
2. 用腾讯月K + 年报 EPS 填 `pe_anchor.bottoms`
3. 用腾讯月K 后复权填 `drawdown_anchor.bottoms`
4. 定义 `sniper_range.condition_pe` / `condition_dd`
5. 审校后写入 `~/.hermes/state/bottom_profiles/{symbol}.json`

> 港股示例只需把 `symbol` 换成 5 位代码（如 `00700`）、`market` 设为 `HK`，其余 schema 完全一致。
