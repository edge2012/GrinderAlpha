#!/usr/bin/env python3
"""Option pricing demo — Black-Scholes put (pure Python, zero deps).

Run:
    python3 examples/demo_options.py
    python3 examples/demo_options.py --S 100 --K 90 --T-days 30 --r 0.05 --sigma 0.50

Parameters:
    S      = 标的现价
    K      = 行权价
    T-days = 到期天数
    r      = 无风险利率（0.04 = 4%）
    sigma  = 波动率（0.60 = 60%）
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from investment.options_estimator import bs_put_price, bs_put_delta


def main() -> None:
    p = argparse.ArgumentParser(description="Black-Scholes 欧式看跌期权定价")
    p.add_argument("--S", type=float, default=94.0, help="标的现价")
    p.add_argument("--K", type=float, default=82.0, help="行权价")
    p.add_argument("--T-days", type=int, default=30, help="到期天数")
    p.add_argument("--r", type=float, default=0.04, help="无风险利率（0.04=4%）")
    p.add_argument("--sigma", type=float, default=0.60, help="波动率（0.60=60%）")
    a = p.parse_args()

    T = a.T_days / 365.0
    price = bs_put_price(a.S, a.K, T, a.r, a.sigma)
    delta = bs_put_delta(a.S, a.K, T, a.r, a.sigma)

    print("Black-Scholes 欧式看跌期权定价")
    print("-" * 40)
    print(f"  标的现价 S      = {a.S}")
    print(f"  行权价  K       = {a.K}")
    print(f"  到期时间 T      = {a.T_days} 天（{T:.4f} 年）")
    print(f"  无风险利率 r    = {a.r:.0%}")
    print(f"  波动率  sigma   = {a.sigma:.0%}")
    print("-" * 40)
    print(f"  Put 权利金      = {price:.4f}")
    print(f"  Put Delta       = {delta:.4f}")


if __name__ == "__main__":
    main()
