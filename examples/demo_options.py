#!/usr/bin/env python3
"""Option pricing demo — Black-Scholes put (pure Python, zero deps).

Run:
    python3 examples/demo_options.py

Shows the Black-Scholes put price and delta with labelled inputs,
so you can read the numbers without guessing what they mean.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from investment.options_estimator import bs_put_price, bs_put_delta


def main() -> None:
    S, K, T_days, r, sigma = 94.0, 82.0, 30, 0.04, 0.60
    T = T_days / 365.0

    price = bs_put_price(S, K, T, r, sigma)
    delta = bs_put_delta(S, K, T, r, sigma)

    print("Black-Scholes 欧式看跌期权定价")
    print("-" * 40)
    print(f"  标的现价 S      = {S}")
    print(f"  行权价  K       = {K}")
    print(f"  到期时间 T      = {T_days} 天（{T:.4f} 年）")
    print(f"  无风险利率 r    = {r:.0%}")
    print(f"  波动率  sigma   = {sigma:.0%}")
    print("-" * 40)
    print(f"  Put 权利金      = {price:.4f}")
    print(f"  Put Delta       = {delta:.4f}")


if __name__ == "__main__":
    main()
