"""
回测统一入口 — python -m backtest.run <name> [--symbol X]
========================================================

注册表路由（配合6 方案A）：每个回测注册一个 name，`run.py` 按 name 路由到对应核心。
数据经 backtest.data.get_history 统一获取（A/H→腾讯、US→Yahoo v8），
每个回测输出「长期收益 / 胜率 / 最大回撤 + 数据来源声明」。

用法：
    python -m backtest.run --list                     # 列出所有回测
    python -m backtest.run panic                      # 跑恐慌跌幅回测（3 指数）
    python -m backtest.run panic --symbol sh000300    # 只跑沪深300
    python -m backtest.run entry_signal --symbol sz399006
"""

import argparse
import sys

import numpy as np

from backtest import core, data


# ── 目标池（与原脚本一致）───────────────────────────────────────────

ENTRY_SIGNAL_TARGETS = [
    ("sz399006", "创业板指", "宽基", [-5.0, 2.0]),
    ("sh000688", "科创50", "宽基", [-5.0, 2.0]),
    ("sh000300", "沪深300", "宽基", [-5.0, 2.0]),
    ("hkHSTECH", "恒生科技", "宽基-港股", [-6.0, 3.0]),
    ("sh000905", "中证500", "宽基", [-5.0, 2.0]),
]

PANIC_INDICES = {
    "sh000300": ("沪深300", "大盘价值"),
    "sz399852": ("中证1000", "小盘成长"),
    "sz399006": ("创业板指", "成长"),
}

DCA_SYMBOLS = ["SPY", "QQQ", "SMH", "AIQ"]
DCA_STRATEGIES = [
    ("flat", "无门禁($150/w)"),
    ("trend_50", "趋势50MA"),
    ("trend_200", "趋势200MA"),
    ("mean_rev_50", "均值回归50MA"),
    ("mean_rev_200", "均值回归200MA"),
]

PANIC_BUCKETS = [
    (-999, -0.20), (-0.20, -0.15), (-0.15, -0.10),
    (-0.10, -0.07), (-0.07, -0.05), (-0.05, 0),
    (0, 0.05), (0.05, 0.10), (0.10, 999),
]
PANIC_LABELS = [
    "<-20%", "-20~-15%", "-15~-10%",
    "-10~-7%", "-7~-5%", "-5~0%",
    "0~5%", "5~10%", ">10%",
]


# ── 工具 ───────────────────────────────────────────────────────────

def _ah_closes(tcode, freq="day"):
    """A/H 拉取 close 列表 + 数据来源。"""
    market = "HK" if tcode.startswith("hk") else "A"
    df, src = data.get_history(tcode, market, freq)
    return df["close"].tolist(), src


def _pct(v):
    """v 为小数比例 → 百分比字符串（如 0.123 → '+12.3%'）。"""
    return f"{v:+.1%}" if v is not None else "N/A"


# ── 各回测 runner（返回 (结构化结果, 数据来源)）────────────────────

def run_entry_signal(symbol=None):
    """建仓信号回测（命题1-4）：左侧信号质量 + 下行风险。"""
    targets = [t for t in ENTRY_SIGNAL_TARGETS
               if not symbol or symbol in (t[0], t[1])]
    out = {}
    src = "腾讯 fqkline"
    for tcode, name, cat, (pmin, pmax) in targets:
        closes, src = _ah_closes(tcode, "day")
        if len(closes) < 120:
            out[name] = {"error": f"数据不足({len(closes)}条)"}
            continue
        sigs = core.compute_signals(closes, pmin, pmax)
        enriched = []
        for s in sigs:
            s.update(core.fwd_returns(closes, s["i"]))
            if s.get("fwd20") is not None:
                enriched.append(s)
        sigs = enriched
        # 长期收益 / 胜率 / 下行风险
        n120, avg120, win120 = core.stats([s.get("fwd120") for s in sigs])
        ndd, dd_avg, _ = core.stats([s.get("max_dd20") for s in sigs])
        out[name] = {
            "tcode": tcode, "cat": cat, "窗口": f"[{pmin},{pmax}]",
            "信号数": len(sigs),
            "120日收益%": avg120, "120日胜率%": win120,
            "20日最大回撤%": dd_avg,
        }
        print(f"[{name} {cat}] 信号{len(sigs)}个 | 120日均收益{avg120:+.1f}% "
              f"胜率{win120:.0f}% | 20日最大回撤{dd_avg:+.1f}%")
    return out, src


def run_tail_risk(symbol=None):
    """尾部风险补测：收益分布（左尾）+ 长周期兑现。"""
    targets = [t for t in ENTRY_SIGNAL_TARGETS
               if not symbol or symbol in (t[0], t[1])]
    out = {}
    src = "腾讯 fqkline"
    for tcode, name, cat, (pmin, pmax) in targets:
        closes, src = _ah_closes(tcode, "day")
        if len(closes) < 300:
            out[name] = {"error": f"数据不足({len(closes)}条)"}
            continue
        sigs = core.compute_signals(closes, pmin, pmax)
        for s in sigs:
            s.update(core.fwd_long(closes, s["i"]))
        deep = [s for s in sigs if s["drawdown_ath"] < -20]
        out[name] = {
            "信号总数": len(sigs), "深回撤样本": len(deep),
        }
        print(f"[{name}] 信号{len(sigs)} 深回撤{len(deep)}")
        for h in (120, 250, 500):
            d = core.dist_stats([s.get(f"fwd{h}") for s in deep])
            if d:
                out[name][f"{h}日"] = {
                    "均值%": round(d["avg"], 1), "中位%": round(d["med"], 1),
                    "最差%": round(d["worst"], 1), "P95%": round(d["p95"], 1),
                }
                print(f"    {h:>3}日: 均值{d['avg']:+.1f}% 中位{d['med']:+.1f}% "
                      f"最差{d['worst']:+.1f}% P95{d['p95']:+.1f}%")
    return out, src


def run_panic(symbol=None):
    """恐慌跌幅回测：单月极端跌幅后的前向反弹。"""
    indices = {k: v for k, v in PANIC_INDICES.items()
               if not symbol or symbol == k}
    out = {}
    src = "腾讯 fqkline"
    for tcode, (name, cat) in indices.items():
        monthly_df, src = data.get_history(tcode, "A", "month")
        monthly = [{"date": str(idx.date()), "close": c}
                   for idx, c in monthly_df["close"].items()]
        if len(monthly) < 30:
            out[name] = {"error": f"数据不足({len(monthly)}条)"}
            continue
        obs = core.collect_observations(monthly)
        buckets = core.bucket_stats(obs, PANIC_BUCKETS, PANIC_LABELS)
        # 极端跌幅（<-10%）vs 全样本
        extreme = [o for o in obs if o["month_ret"] < -0.10]
        out[name] = {"样本月": len(obs), "极端跌月": len(extreme)}
        print(f"[{name} {cat}] 观测{len(obs)}月 极端跌{len(extreme)}月")
        for label in ("<-20%", "-10~-7%", ">10%"):
            b = buckets.get(label)
            if b:
                out[name][label] = {
                    "n": b["n"],
                    "6月收益%": round(b["fwd6m_avg"] * 100, 1) if b["fwd6m_avg"] else None,
                    "6月胜率%": round(b["fwd6m_win"] * 100, 0) if b["fwd6m_win"] else None,
                }
                if b["fwd6m_avg"] is not None and b["fwd6m_win"] is not None:
                    print(f"    {label:<8} n={b['n']} 6月收益{b['fwd6m_avg']:+.1%} "
                          f"胜率{b['fwd6m_win']:.0%}")
                else:
                    print(f"    {label:<8} n={b['n']} 样本不足")
    return out, src


def run_dca_compare(symbol=None):
    """等额预算 DCA 对比：不同分配策略的长期收益/最大回撤。"""
    syms = [s for s in DCA_SYMBOLS if not symbol or s == symbol]
    out = {}
    src = "Yahoo Finance v8"
    for sym in syms:
        df, src = data.get_history(sym, "US", "day")
        if df.empty:
            out[sym] = {"error": "数据不可用(限流或缓存空)"}
            print(f"[{sym}] 数据不可用")
            continue
        row = {}
        print(f"[{sym}] {len(df)}根日线")
        for strat, label in DCA_STRATEGIES:
            m = core.simulate_equal_budget(df, strat)
            if "error" in m:
                continue
            row[strat] = {"roi%": m["roi"], "max_dd%": m["max_dd"],
                          "n_buys": m["n_buys"]}
            print(f"    {label:<14} ROI{m['roi']:+.1f}% 最大回撤{m['max_dd']:.1f}%")
        out[sym] = row
    return out, src


def run_normal_years(symbol=None):
    """正常年份保护层成本：VIX 分层下的 Spread/Put 理论成本。"""
    src_parts = []
    # SPY 月线（Yahoo 日线 → 月频 resample，避免腾讯 US 月线缺陷）
    spy_df, spy_src = data.get_history("SPY", "US", "day")
    src_parts.append(spy_src)
    vix_df, vix_src = data.get_history("^VIX", "US", "day")
    src_parts.append(vix_src + "(^VIX)")
    if spy_df.empty or vix_df.empty:
        print("⚠️ 数据不可用（Yahoo 限流或缓存空），无法回测")
        return {"error": "数据不可用"}, " + ".join(src_parts)

    spy_m = spy_df["close"].resample("MS").last().dropna()
    vix = vix_df["close"]
    out = {"spread_ok_months": 0, "put_ok_months": 0, "total_months": 0,
           "spread_credits": [], "put_costs": []}

    for entry_date in spy_m.index:
        entry_str = entry_date.strftime("%Y-%m-%d")
        spy_px = float(spy_m.loc[entry_date])
        # 找 entry 前最近的 VIX
        prior = vix[vix.index <= entry_date]
        if prior.empty:
            continue
        vix_val = float(prior.iloc[-1])
        out["total_months"] += 1
        atm = vix_val / 100
        if vix_val < 20:  # Bull Put Spread 条件
            out["spread_ok_months"] += 1
            ks, kb = spy_px * 0.95, spy_px * 0.90
            ps = core.bs_put(spy_px, ks, 30 / 365, 0.04, atm * 1.4)
            pb = core.bs_put(spy_px, kb, 30 / 365, 0.04, atm * 1.55)
            out["spread_credits"].append((ps - pb) / spy_px * 100)
        if vix_val < 15:  # Long Put 条件
            out["put_ok_months"] += 1
            kp = spy_px * 0.95
            cost = core.bs_put(spy_px, kp, 90 / 365, 0.04, atm * 1.35) / spy_px * 100
            out["put_costs"].append(cost)

    total = out["total_months"]
    print(f"正常年份保护层：{total}个月")
    print(f"  Spread可行(VIX<20): {out['spread_ok_months']}/{total} "
          f"({out['spread_ok_months']/total*100:.0f}%)" if total else "  无样本")
    if out["spread_credits"]:
        med = float(np.median(out["spread_credits"]))
        print(f"  Spread信用中位: {med:.2f}% → 年化(月做1笔) {med*12:.1f}%")
    if out["put_costs"]:
        medc = float(np.median(out["put_costs"]))
        print(f"  Put成本中位: {medc:.2f}% → 年化(3月滚) {medc*4:.1f}%")
    return out, " + ".join(src_parts)


# ── 注册表 ─────────────────────────────────────────────────────────

REGISTRY = {
    "entry_signal": ("建仓信号回测（命题1-4，A/H 宽基）", run_entry_signal),
    "tail_risk": ("尾部风险补测（收益分布+长周期，A/H）", run_tail_risk),
    "panic": ("恐慌跌幅反弹回测（A/H 指数月K）", run_panic),
    "dca_compare": ("等额预算 DCA 对比（美股 ETF）", run_dca_compare),
    "normal_years": ("正常年份保护层成本（美股 SPY+VIX）", run_normal_years),
}


def _print_list():
    print("可用的回测：")
    for name, (desc, _) in REGISTRY.items():
        print(f"  {name:<14} {desc}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="回测统一入口")
    parser.add_argument("name", nargs="?", help="回测名称（见 --list）")
    parser.add_argument("--symbol", "-s", help="只跑指定标的（代码或名称）")
    parser.add_argument("--list", action="store_true", help="列出所有回测")
    args = parser.parse_args(argv)

    if args.list or not args.name:
        _print_list()
        return 0
    if args.name not in REGISTRY:
        print(f"未知回测: {args.name}（用 --list 查看）", file=sys.stderr)
        return 1

    desc, runner = REGISTRY[args.name]
    print(f"\n{'=' * 72}\n📊 {desc}\n{'=' * 72}")
    result, src = runner(args.symbol)
    print(f"\n数据来源: {src}")
    print(f"{'=' * 72}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
