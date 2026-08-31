"""
回测核心 — 纯计算函数（无 I/O、无私有依赖）
==========================================

从 5 个回测脚本抽取的可复用核心逻辑，全部是「输入数据 → 输出结果」的纯函数，
与数据源解耦，可单独测试（配合6 Stage2「抽核心」交付物）：

- 建仓信号：compute_signals / fwd_returns / stats（entry_signal_backtest.py）
- 尾部风险：fwd_long / dist_stats（tail_risk.py）
- 恐慌反弹：collect_observations / bucket_stats（panic_backtest.py）
- 期权定价：bs_put（normal_years_2023_2026.py）
- DCA 对比：simulate_equal_budget（sector_etf_equal_budget.py）

⚠️ 逻辑与原脚本逐字对齐（仅去掉 print / I/O），回测结果须与原脚本一致。
"""

from collections import defaultdict

import numpy as np
import pandas as pd


# ── 建仓信号（命题1-4）──────────────────────────────────────────────

def ma(closes, i, n):
    return sum(closes[i - n + 1:i + 1]) / n


def compute_signals(closes, pmin, pmax):
    """左侧建仓信号。返回 [{i, close, dev20, dist50, drawdown_ath}]。

    条件① MA20 窗口 dev20 ∈ [pmin, pmax]；条件② 5MA ≥ 10MA。
    纪律：False→True 转变算首次（信号去重）。
    """
    sigs = []
    n = len(closes)
    for i in range(50, n):
        ma20 = ma(closes, i, 20)
        ma50 = ma(closes, i, 50)
        ma5 = ma(closes, i, 5)
        ma10 = ma(closes, i, 10)
        dev20 = (closes[i] - ma20) / ma20 * 100
        c1 = pmin <= dev20 <= pmax
        c2 = ma5 >= ma10
        L = c1 and c2
        # 去重：False→True 转变
        prev_ma20 = ma(closes, i - 1, 20)
        prev_dev20 = (closes[i - 1] - prev_ma20) / prev_ma20 * 100
        prev_c1 = pmin <= prev_dev20 <= pmax
        prev_ma5 = ma(closes, i - 1, 5)
        prev_ma10 = ma(closes, i - 1, 10)
        prev_L = prev_c1 and (prev_ma5 >= prev_ma10)
        if L and not prev_L:
            dist50 = (closes[i] - ma50) / ma50 * 100
            ath = max(closes[:i + 1])
            drawdown_ath = (closes[i] - ath) / ath * 100
            sigs.append({"i": i, "close": closes[i], "dev20": dev20,
                         "dist50": dist50, "drawdown_ath": drawdown_ath})
    return sigs


def fwd_returns(closes, i, horizons=(20, 60, 120)):
    """未来收益 + 20 日最大回撤（下行风险）。"""
    out = {}
    for h in horizons:
        out[f"fwd{h}"] = (closes[i + h] / closes[i] - 1) * 100 if i + h < len(closes) else None
    if i + 20 < len(closes):
        window = closes[i:i + 21]
        out["max_dd20"] = (min(window) / closes[i] - 1) * 100
    else:
        out["max_dd20"] = None
    return out


def stats(vals):
    """(样本数, 均值%, 胜率%)。"""
    vals = [v for v in vals if v is not None]
    if not vals:
        return (0, 0.0, 0.0)
    avg = sum(vals) / len(vals)
    win = sum(1 for v in vals if v > 0) / len(vals) * 100
    return (len(vals), round(avg, 2), round(win, 1))


# ── 尾部风险（补测：收益分布 + 长周期）──────────────────────────────

def fwd_long(closes, i, horizons=(20, 60, 120, 250, 500)):
    out = {}
    for h in horizons:
        out[f"fwd{h}"] = (closes[i + h] / closes[i] - 1) * 100 if i + h < len(closes) else None
    return out


def dist_stats(vals):
    """收益分布统计（左尾/中位/P95）。无样本返回 None。"""
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)

    def pct(p):
        idx = min(n - 1, int(n * p))
        return vals[idx]

    return {
        "n": n, "worst": vals[0], "p5": pct(0.05), "p25": pct(0.25),
        "med": pct(0.50), "p75": pct(0.75), "p95": pct(0.95), "best": vals[-1],
        "avg": sum(vals) / n,
    }


# ── 恐慌反弹（单月极端跌幅后的前向收益）────────────────────────────

def collect_observations(monthly):
    """monthly: [{date, close}] → [{date, month_ret, fwd1m/3m/6m}]。"""
    closes = [m["close"] for m in monthly]
    dates = [m["date"] for m in monthly]
    n = len(closes)
    if n < 30:
        return []
    obs = []
    for i in range(1, n):
        month_ret = (closes[i] - closes[i - 1]) / closes[i - 1]
        fwd1m = (closes[i + 1] - closes[i]) / closes[i] if i + 1 < n else None
        fwd3m = (closes[i + 3] - closes[i]) / closes[i] if i + 3 < n else None
        fwd6m = (closes[i + 6] - closes[i]) / closes[i] if i + 6 < n else None
        obs.append({"date": dates[i], "month_ret": month_ret,
                    "fwd1m": fwd1m, "fwd3m": fwd3m, "fwd6m": fwd6m})
    return obs


def bucket_stats(observations, buckets, labels):
    """按月收益分桶 → 每桶前向收益统计（纯返回 dict，不打印）。"""
    groups = defaultdict(list)
    for o in observations:
        ret = o["month_ret"]
        for i, (lo, hi) in enumerate(buckets):
            if lo <= ret < hi:
                groups[i].append(o)
                break

    results = {}
    for i in sorted(groups.keys()):
        items = groups[i]

        def _stat(key):
            vals = [x[key] for x in items if x[key] is not None]
            if len(vals) < 3:
                return None
            return (sum(vals) / len(vals), sum(1 for v in vals if v > 0) / len(vals))

        f1, f3, f6 = _stat("fwd1m"), _stat("fwd3m"), _stat("fwd6m")
        results[labels[i]] = {
            "n": len(items),
            "fwd1m_avg": f1[0] if f1 else None, "fwd1m_win": f1[1] if f1 else None,
            "fwd3m_avg": f3[0] if f3 else None, "fwd3m_win": f3[1] if f3 else None,
            "fwd6m_avg": f6[0] if f6 else None, "fwd6m_win": f6[1] if f6 else None,
        }
    return results


# ── 期权保护定价（正常年份回测）────────────────────────────────────

def bs_put(S, K, T, r, sigma):
    """Black-Scholes 看跌期权理论价。"""
    from scipy.stats import norm
    if T <= 0 or sigma <= 0:
        return max(K - S, 0)
    d1 = (np.log(S / K) + (r + sigma ** 2 / 2) * T) / (sigma * np.sqrt(T))
    return max(K * np.exp(-r * T) * norm.cdf(-(d1 - sigma * np.sqrt(T)))
               - S * norm.cdf(-d1), 0)


# ── 等额预算 DCA 对比（板块 ETF）───────────────────────────────────

WEEKLY_BUDGET = 150.0  # 周预算基准


def simulate_equal_budget(df, strategy, start_year=0):
    """df: DataFrame(close, DatetimeIndex) → 周频回测，返回 metrics dict。"""
    d = df.copy()
    d["ma50"] = d["close"].rolling(50).mean()
    d["ma200"] = d["close"].rolling(200).mean()
    weekly = d.resample("W-FRI").last().dropna(subset=["close"])
    weekly = weekly[weekly.index.year >= start_year]
    if weekly.empty:
        return {"error": "no data"}

    shares = 0.0
    cash = 0.0
    total_invested = 0.0
    n_buys = 0

    for _, row in weekly.iterrows():
        price = row["close"]
        below_50 = (not pd.isna(row["ma50"])) and (price < row["ma50"])
        below_200 = (not pd.isna(row["ma200"])) and (price < row["ma200"])
        above_50 = (not pd.isna(row["ma50"])) and (price >= row["ma50"])
        above_200 = (not pd.isna(row["ma200"])) and (price >= row["ma200"])

        if strategy == "flat":
            invest = WEEKLY_BUDGET
        elif strategy == "mean_rev_50":
            invest = 300.0 if below_50 else 75.0
        elif strategy == "mean_rev_200":
            invest = 300.0 if below_200 else 100.0
        elif strategy == "trend_50":
            if above_50:
                invest = 220.0
            else:
                invest = 0.0
                cash += WEEKLY_BUDGET
                total_invested += WEEKLY_BUDGET
        elif strategy == "trend_200":
            if above_200:
                invest = 200.0
            else:
                invest = 0.0
                cash += WEEKLY_BUDGET
                total_invested += WEEKLY_BUDGET
        else:
            invest = 0.0

        if invest > 0:
            shares += invest / price
            total_invested += invest
            n_buys += 1

    final_price = weekly["close"].iloc[-1]
    holdings = shares * final_price
    total = holdings + cash
    net = total - total_invested
    roi = (net / total_invested) * 100 if total_invested else 0

    # 最大回撤
    prices = weekly["close"].values
    cum_shares = 0.0
    cum_cash = 0.0
    track_shares = []
    track_cash = []
    for _, row in weekly.iterrows():
        price = row["close"]
        below_50 = (not pd.isna(row["ma50"])) and (price < row["ma50"])
        below_200 = (not pd.isna(row["ma200"])) and (price < row["ma200"])
        above_50 = (not pd.isna(row["ma50"])) and (price >= row["ma50"])
        above_200 = (not pd.isna(row["ma200"])) and (price >= row["ma200"])

        if strategy == "flat":
            cum_shares += WEEKLY_BUDGET / price
        elif strategy == "mean_rev_50":
            cum_shares += (300.0 if below_50 else 75.0) / price
        elif strategy == "mean_rev_200":
            cum_shares += (300.0 if below_200 else 100.0) / price
        elif strategy == "trend_50":
            if above_50:
                cum_shares += 220.0 / price
            else:
                cum_cash += WEEKLY_BUDGET
        elif strategy == "trend_200":
            if above_200:
                cum_shares += 200.0 / price
            else:
                cum_cash += WEEKLY_BUDGET
        track_shares.append(cum_shares)
        track_cash.append(cum_cash)

    port_val = np.array(track_shares) * prices + np.array(track_cash)
    running_max = pd.Series(port_val).cummax()
    dd = port_val / running_max - 1
    max_dd = dd[np.isfinite(dd)].min() * 100 if np.isfinite(dd).any() else 0

    return {
        "strategy": strategy,
        "total": round(total, 0),
        "total_invested": round(total_invested, 0),
        "net": round(net, 0),
        "roi": round(roi, 1),
        "max_dd": round(max_dd, 1),
        "n_buys": n_buys,
        "n_weeks": len(weekly),
    }
