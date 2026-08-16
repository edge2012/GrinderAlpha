#!/usr/bin/env python3
"""
期权体系演示 — Account C 核心引擎串联
======================================
Black-Scholes 定价 → 支撑位提取 → 最优行权价

零第三方依赖，纯 Python 标准库，直接跑：
    python3 engine/examples/demo_options.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from engine.options_estimator import bs_put_price
from engine.support_levels import get_support_levels


def main():
    # ── 1. 支撑位提取（SPY 示例档案，data/bottom_profiles/SPY.json）──
    print("=== 1. 支撑位提取 (support_levels.py v3) ===")
    support = get_support_levels("SPY", current_price=771)
    print(f"  近底 S1: ${support.s1} — {support.s1_label}")
    if support.s2:
        print(f"  深底 S2: ${support.s2:.0f} — {support.s2_label}")
    print(f"  最优 SP 行权价 : {support.optimal_sp_range}")
    print(f"  最优 Spread 卖腿: {support.optimal_spread_range}")
    print(f"  距近底: {support.distance_to_s1_pct:+.1f}%")
    print()

    # ── 2. Black-Scholes 定价（纯 Python，无 scipy）──
    print("=== 2. Black-Scholes 定价 (options_estimator.py) ===")
    put = bs_put_price(S=94, K=82, T=30 / 365, r=0.04, sigma=0.60)
    print(f"  HOOD 30 天 Put @82: ${put:.2f}")
    print("  (sigma=0.60 是 52w 范围估算 IV — 仅演示定价公式，非真实报价)")
    print()
    print("  注：真实场景主路径走 cboe_options.py 读 CBOE 真实 bid/ask 中间价，")
    print("      BS 仅是降级兜底（详见 cboe_options.py 的 docstring）。")


if __name__ == "__main__":
    main()
