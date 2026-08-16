#!/usr/bin/env python3
"""
底部加速系统 — 基于历史大底趋势线的建仓加速模块

核心逻辑：
  1. 只使用已确认的历史大底（不含当前数据）拟合对数线性趋势线
  2. 将趋势线投影到当前时间 → 理论底部
  3. 计算当前价格偏离趋势线的百分比 → 判定区域
  4. 根据区域输出 DCA 加速倍率

设计原则（2026-06-20）：
  - "击球区" = 当前价格触及或低于历史底部趋势线投影
  - 越跌越买，但不取消DCA——只改变每批大小和间隔
  - 各指数独立校准（创业板σ大，上证σ小）
"""

import math
import sys
import os
import logging
from datetime import datetime

# 策略参数外部化（不入 Git）
try:
    from strategy_param_loader import get_params as _get_strategy_params
    _SP = _get_strategy_params()
    if _SP is None:
        logging.getLogger("bottom_accelerator").error("策略参数加载失败，使用硬编码回退值")
except ImportError:
    _SP = None
from valuation_engine import evaluate_etf, ValZone, format_valuation_line

# ═══════════════════════════════════════════════════════════════
# 历史大底定义 — 仅已确认的底部，不含当前数据
# ═══════════════════════════════════════════════════════════════

BOTTOM_DEFINITIONS = {
    "沪深300": {
        "bottoms": [
            ("2005-06", 807),   # 股权分置改革底
            ("2008-10", 1606),  # 全球金融危机底
            ("2013-06", 2023),  # 钱荒底
            ("2016-01", 2821),  # 熔断底
            ("2019-01", 2935),  # 贸易战底
            ("2024-02", 3108),  # 地产危机底
        ],
    },
    "上证指数": {
        "bottoms": [
            ("2005-06", 998),
            ("2008-10", 1664),
            ("2013-06", 1849),
            ("2019-01", 2440),
            ("2024-02", 2635),
        ],
    },
    "创业板指": {
        "bottoms": [
            ("2012-12", 585),
            ("2018-10", 1185),
            ("2024-02", 1483),
        ],
    },
}

# ═══════════════════════════════════════════════════════════════
# ETF → 底层指数映射 + 价格换算系数
# ETF价格(元) × 系数 ≈ 指数点位
# ═══════════════════════════════════════════════════════════════

ETF_INDEX_MAP = {
    "510300": ("沪深300", 992),     # index ~4942 / ETF ~4.984
    "510050": ("上证指数", 1356),    # index ~4090 / ETF ~3.017
    "510500": ("上证指数", 810),     # 中证500 ~8828 / ETF ~10.9? 实际需校准
    "159915": ("创业板指", 996),     # index ~4252 / ETF ~4.269
    "588000": (None, None),
    "159920": (None, None),
    "513130": (None, None),
    "512480": (None, None),
    "516160": (None, None),
    "512010": (None, None),
    "159928": (None, None),
    "515880": (None, None),
    "515070": (None, None),
    "562500": (None, None),
    "512890": (None, None),
    "510880": (None, None),
}

# ═══════════════════════════════════════════════════════════════
# 区域阈值 — 以绝对偏离百分比为界（不用σ，太宽）
# 沪深300历史：底部平均偏离+2%，恐慌底-21%
# 上证历史：底部平均偏离+1%，恐慌底不深
# ═══════════════════════════════════════════════════════════════

def _load_zones():
    """从策略配置加载底部加速区域，失败时用硬编码回退"""
    if _SP:
        raw = _SP["bottom_accelerator"]["zones"]
        return [(z["name"], z["min_pct"], z["max_pct"], z["dca_multiplier"], z["short_label"]) for z in raw]
    return []  # NO FALLBACK
ZONES = _load_zones()


# ═══════════════════════════════════════════════════════════════
# 核心计算
# ═══════════════════════════════════════════════════════════════

def _months_from(first_date, target_date):
    """计算两个 YYYY-MM 日期之间相差的月数。"""
    y1, m1 = map(int, first_date.split("-"))
    y2, m2 = map(int, target_date.split("-"))
    return (y2 - y1) * 12 + (m2 - m1)


def fit_bottom_trendline(bottoms):
    """
    对历史底部做对数线性回归。

    Args:
        bottoms: [(date_str, value), ...]  如 [("2005-06", 807), ...]

    Returns:
        dict with keys:
            slope, intercept       — 回归参数  ln(y) = intercept + slope * x
            annual_growth_pct      — 底部年化抬升率 (%)
            r_squared              — R² 拟合优度
            sigma_pct              — 历史底部偏离趋势线的标准差 (%)
            avg_deviation_pct      — 平均偏离 (%)
            projected_now          — 投影到当前时间的趋势线值
            current_deviation_pct  — 当前偏离 (%)
            zone                   — 当前区域名称
            dca_multiplier         — DCA 加速倍率
    """
    if len(bottoms) < 2:
        return None

    first_date = bottoms[0][0]
    xs = [_months_from(first_date, d) for d, _ in bottoms]
    ys = [math.log(v) for _, v in bottoms]
    n = len(xs)

    sx, sy = sum(xs), sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sxx = sum(x * x for x in xs)

    if n * sxx - sx * sx == 0:
        return None

    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    intercept = (sy - slope * sx) / n

    # R²
    y_mean = sy / n
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

    # 年化增长率
    annual_growth_pct = (math.exp(slope * 12) - 1) * 100

    # 历史偏离统计
    deviations = []
    for i, (d, v) in enumerate(bottoms):
        trend = math.exp(intercept + slope * xs[i])
        deviations.append((v - trend) / trend * 100)

    avg_dev = sum(deviations) / len(deviations)
    variance = sum((d - avg_dev) ** 2 for d in deviations) / (len(deviations) - 1) if len(deviations) > 1 else 0
    sigma_pct = math.sqrt(variance)

    # 投影到当前时间
    now_str = datetime.now().strftime("%Y-%m")
    now_months = _months_from(first_date, now_str)
    projected_now = math.exp(intercept + slope * now_months)

    return {
        "slope": slope,
        "intercept": intercept,
        "annual_growth_pct": round(annual_growth_pct, 1),
        "r_squared": round(r_squared, 3),
        "sigma_pct": round(sigma_pct, 1),
        "avg_deviation_pct": round(avg_dev, 1),
        "projected_now": round(projected_now),
        "first_date": first_date,
    }


def classify_zone(deviation_pct):
    """
    根据偏离趋势线的绝对百分比判定当前区域。

    Returns:
        dict with: zone_name, label, dca_multiplier
    """
    for name, lo, hi, mult, label in ZONES:
        lo_ok = lo is None or deviation_pct >= lo
        hi_ok = hi is None or deviation_pct < hi
        if lo_ok and hi_ok:
            return {
                "zone_name": name,
                "label": label,
                "dca_multiplier": mult,
            }

    return {
        "zone_name": "正常区",
        "label": "1x",
        "dca_multiplier": 1.0,
    }


def analyze_etf_bottom(etf_code, current_price, index_level=None):
    """
    分析单个 ETF 的底部加速状态。

    Args:
        etf_code: ETF代码 (如 "510300")
        current_price: ETF当前价格（元）
        index_level: 底层指数当前点位（可选，有则更准）

    Returns:
        dict 或 None（如果该ETF无底部趋势线数据）
    """
    index_name = ETF_INDEX_MAP.get(etf_code)
    if index_name is None:
        return None
    index_name, scale_factor = index_name if isinstance(index_name, tuple) else (index_name, 1000)
    if index_name is None:
        return None

    definition = BOTTOM_DEFINITIONS.get(index_name)
    if definition is None:
        return None

    trendline = fit_bottom_trendline(definition["bottoms"])
    if trendline is None:
        return None

    projected = trendline["projected_now"]
    if projected <= 0:
        return None

    # Convert ETF price to index-equivalent using scale factor
    if index_level is not None and index_level > 0:
        price_in_index_terms = index_level
    elif current_price > 100:
        price_in_index_terms = current_price
    else:
        price_in_index_terms = current_price * scale_factor

    deviation_pct = (price_in_index_terms - projected) / projected * 100
    zone = classify_zone(deviation_pct)

    return {
        "etf_code": etf_code,
        "index_name": index_name,
        "current_price": current_price,
        "price_in_index_terms": round(price_in_index_terms),
        "projected_bottom": projected,
        "deviation_pct": round(deviation_pct, 1),
        "sigma_pct": trendline["sigma_pct"],
        "annual_growth_pct": trendline["annual_growth_pct"],
        "r_squared": trendline["r_squared"],
        "zone": zone,
        "dca_multiplier": zone["dca_multiplier"],
    }


def format_bottom_line(result):
    """格式化底部加速状态为单行字符串（含估值双确认）。"""
    if result is None:
        return ""

    zone = result["zone"]
    line = (
        f"⭐ 底部: 距趋势线 {result['deviation_pct']:+.1f}%"
        f" ({result['projected_bottom']:,})"
        f" | {zone['label']} {zone['zone_name']}"
    )

    # 估值双确认
    val_result = result.get("valuation")
    if val_result and val_result.composite_zone != ValZone.INSUFFICIENT:
        val_line = format_valuation_line(val_result)
        dca_adjusted = result.get("dca_adjusted")
        adj_note = ""
        if dca_adjusted is not None and dca_adjusted != result["dca_multiplier"]:
            adj_note = f" → {dca_adjusted}x"
        line += f" | 📊 {val_line}{adj_note}"

    return line


def format_bottom_detail(result):
    """格式化底部加速状态为多行详情（含估值双确认）。"""
    if result is None:
        return []

    zone = result["zone"]
    lines = []
    lines.append(f"    ⭐ 底部加速: {zone['label']} {zone['zone_name']} (σ={result['sigma_pct']}%)")
    lines.append(f"       理论底部: {result['projected_bottom']:,}")
    lines.append(f"       当前偏离: {result['deviation_pct']:+.1f}%")
    lines.append(f"       趋势线: 年化+{result['annual_growth_pct']}% / R²={result['r_squared']}")

    # 估值双确认详情
    val_result = result.get("valuation")
    if val_result and val_result.composite_zone != ValZone.INSUFFICIENT:
        lines.append(f"    📊 估值确认: {val_result.composite_score}/10 {val_result.composite_zone.value}")
        if val_result.pe_percentile is not None:
            lines.append(f"       PE分位: {val_result.pe_percentile:.0f}%")
        if val_result.pb_percentile is not None:
            lines.append(f"       PB分位: {val_result.pb_percentile:.0f}%")
        if val_result.dividend_yield is not None:
            lines.append(f"       股息率: {val_result.dividend_yield:.2f}%")
        dca_adjusted = result.get("dca_adjusted")
        if dca_adjusted is not None and dca_adjusted != result["dca_multiplier"]:
            direction = "↑" if dca_adjusted > result["dca_multiplier"] else "↓"
            lines.append(f"       DCA调整: {result['dca_multiplier']}x {direction} {dca_adjusted}x (估值{direction}调)")

    return lines


# ═══════════════════════════════════════════════════════════════
# 估值双确认逻辑 — V6 新增
# ═══════════════════════════════════════════════════════════════

def adjust_dca_with_valuation(bottom_multiplier: float, val_zone: ValZone) -> float:
    """
    根据估值确认调整DCA倍率。

    原则：底部便宜+估值便宜→加码；底部便宜+估值贵→降级。
    趋势线和估值是独立维度——两者都"便宜"时置信度最高。

    Returns:
        调整后的DCA倍率
    """
    adjustments = {
        # (bottom_mult, val_zone) → adjusted_mult
        (3.0, ValZone.CHEAP): 4.0,
        (3.0, ValZone.SOMEWHAT_CHEAP): 3.5,
        (3.0, ValZone.NORMAL): 2.5,
        (3.0, ValZone.SOMEWHAT_EXPENSIVE): 2.0,
        (3.0, ValZone.EXPENSIVE): 1.5,
        (2.0, ValZone.CHEAP): 3.0,
        (2.0, ValZone.SOMEWHAT_CHEAP): 2.5,
        (2.0, ValZone.NORMAL): 2.0,
        (2.0, ValZone.SOMEWHAT_EXPENSIVE): 1.5,
        (2.0, ValZone.EXPENSIVE): 1.0,
        (1.5, ValZone.CHEAP): 2.5,
        (1.5, ValZone.SOMEWHAT_CHEAP): 2.0,
        (1.5, ValZone.NORMAL): 1.5,
        (1.5, ValZone.SOMEWHAT_EXPENSIVE): 1.0,
        (1.5, ValZone.EXPENSIVE): 1.0,
        (1.0, ValZone.CHEAP): 2.0,
        (1.0, ValZone.SOMEWHAT_CHEAP): 1.5,
        (1.0, ValZone.NORMAL): 1.0,
        (1.0, ValZone.SOMEWHAT_EXPENSIVE): 1.0,
        (1.0, ValZone.EXPENSIVE): 1.0,
    }
    return adjustments.get((bottom_multiplier, val_zone), bottom_multiplier)


def analyze_etf_combined(etf_code, current_price, index_level=None):
    """
    组合分析：底部趋势线 + 估值双确认。

    Returns:
        与 analyze_etf_bottom 相同结构，额外包含:
          - valuation: ValuationResult
          - dca_adjusted: 估值调整后的DCA倍率
          - confirmation: "confirmed" | "upgraded" | "downgraded" | "conflict"
    """
    # 1. 底部趋势线分析
    result = analyze_etf_bottom(etf_code, current_price, index_level)
    if result is None:
        return None

    # 2. 估值分析
    try:
        val_result = evaluate_etf(etf_code)
        result["valuation"] = val_result
    except Exception:
        # 估值失败不影响底部趋势线输出
        result["valuation"] = None
        result["dca_adjusted"] = result["dca_multiplier"]
        result["confirmation"] = "valuation_unavailable"
        return result

    if val_result.composite_zone == ValZone.INSUFFICIENT:
        result["dca_adjusted"] = result["dca_multiplier"]
        result["confirmation"] = "valuation_insufficient"
        return result

    # 3. 双确认调整
    bottom_mult = result["dca_multiplier"]
    adjusted = adjust_dca_with_valuation(bottom_mult, val_result.composite_zone)
    result["dca_adjusted"] = adjusted

    if adjusted > bottom_mult:
        result["confirmation"] = "upgraded"
    elif adjusted < bottom_mult:
        result["confirmation"] = "downgraded"
    else:
        result["confirmation"] = "confirmed"

    return result

if __name__ == "__main__":
    print("=== 底部加速系统 V6 自检 (含估值双确认) ===\n")
    from account_b_builder import fetch_tencent_prices
    tcodes_map = {
        "510300": "sh510300", "510050": "sh510050", "510500": "sh510500",
        "159915": "sz159915",
    }
    for etf in ["510300", "510050", "510500", "159915"]:
        tcode = tcodes_map.get(etf)
        if tcode is None:
            continue
        prices = fetch_tencent_prices([tcode])
        price = prices.get(tcode)
        if price is None:
            print(f"{etf}: 无法获取价格")
            continue

        result = analyze_etf_combined(etf, price)
        if result:
            print(f"━━━ {etf} ━━━")
            print(format_bottom_line(result))
            for l in format_bottom_detail(result):
                print(l)
            conf = result.get("confirmation", "N/A")
            print(f"    🔍 双确认: {conf}")
        print()
