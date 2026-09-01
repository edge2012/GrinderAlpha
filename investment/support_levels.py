#!/usr/bin/env python3
"""
支撑位提取器 v3 — 从底部档案读取真实价格支撑级别
===================================================
设计原则（v3，2026-08-13 修正支撑位语义）:
  支撑位 = 保护，不是行权价的锚。
  近底 S1 = 所有历史回撤底部中的最高价（离现价最近的硬支撑）
  深底 S2 = 次高回撤底部（更深的支撑，S1 击穿后的下一道防线）

  最优 SP 行权价    = min(20%OTM, S1) — 从现价向下算安全垫，落在支撑位下方
  最优 Spread 卖腿 = min(15%OTM, S1) — 支撑位成为行权价上方的反弹防线

  PE锚用于估值维度（BuyPointEngine已处理），不用于价格支撑。
  价格支撑 = 市场历史上实际触及并反弹的价格水平。

用法:
    from investment.support_levels import get_support_levels
    support = get_support_levels('HOOD', current_price=94)
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional, Dict, List


PROFILE_DIR = os.environ.get(
    "BOTTOM_PROFILE_DIR",
    os.path.join(os.path.dirname(__file__), "..", "data", "bottom_profiles"),
)


@dataclass
class SupportResult:
    """支撑位分析结果"""
    symbol: str
    s1: Optional[float]         # 近底价格（离现价最近的硬支撑）
    s1_label: str               # 如 "近底 $244 (ATH $371 × -34.1%, 2020-02→2020-03)"
    s2: Optional[float]         # 深底价格（更深的支撑，S1 击穿后的下一道防线）
    s2_label: str

    # 最优行权价（支撑位是保护：从现价向下算安全垫，落在支撑位下方）
    optimal_sp_range: str       # 如 "$221（近底$244在上方保护）"
    optimal_spread_range: str   # 如 "$221（近底$244在上方保护）"
    best_sp_strike: float       # 最优SP行权价 = min(20%OTM, S1)
    best_spread_sell: float     # 最优Spread卖腿 = min(15%OTM, S1)

    # 安全边际
    distance_to_s1_pct: Optional[float]  # 当前价距近底的距离%（正值=近底在当前价下方）

    profile_loaded: bool
    note: str = ""

    # 支撑位击穿检测（v4.3 新增）
    s1_broken: bool = False      # 现价跌破近底 → "支撑位=保护"语义失效，仅靠安全垫兜底
    s2_broken: bool = False      # 现价跌破深底 → 所有已知支撑位全部击穿


def _load_profile(symbol: str) -> Optional[dict]:
    path = os.path.join(PROFILE_DIR, f"{symbol}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def get_support_levels(symbol: str, current_price: float = None) -> SupportResult:
    """
    从底部档案提取真实价格支撑位。

    近底 S1 = 所有历史回撤底部中的最高价（离现价最近的硬支撑）
    深底 S2 = 次高回撤底部（如果有多个且明显低于 S1）

    最优 SP 行权价    = min(20%OTM, S1)
      → 20% 安全垫向下算，但不高于近底（支撑位成为行权价上方的保护）
    最优 Spread 卖腿 = min(15%OTM, S1)
      → 15% 安全垫向下算，支撑位成为卖腿上方的一道反弹防线
    """
    profile = _load_profile(symbol)

    result = SupportResult(
        symbol=symbol,
        s1=None, s1_label="无档案",
        s2=None, s2_label="",
        optimal_sp_range="未知", optimal_spread_range="未知",
        best_sp_strike=0, best_spread_sell=0,
        distance_to_s1_pct=None,
        profile_loaded=False,
    )

    if not profile:
        result.note = "无底部档案"
        return result

    result.profile_loaded = True
    da = profile.get('drawdown_anchor', {})
    # ath 兼容：drawdown_anchor.ath 或 drawdown_anchor.current.ath（旧格式 A/H）
    ath = da.get('ath') or da.get('current', {}).get('ath') or 0
    # drawdowns 兼容三种结构：major_drawdowns / bottoms / hk_bottoms
    drawdowns = (da.get('major_drawdowns') or da.get('bottoms') or da.get('hk_bottoms') or [])

    if not drawdowns:
        # 无真实回撤底可读——不做「当前回撤×1.5」的拍脑袋估算
        # （那会引入静态快照 current_dd_pct + 任意系数，与"支撑位=真实历史价格"的语义矛盾）
        result.note = "缺少回撤数据"
        return result

    # 直接读 trough_price（真实底部价）；旧档案无此字段时 fallback ath×(1-dd%) 临时兼容
    bottoms = []
    for dd in drawdowns:
        tp = dd.get('trough_price')
        if tp is None and ath:
            tp = ath * (1 - abs(dd.get('dd_pct', 0)) / 100)
        if tp is None:
            continue
        bottoms.append({
            'price': tp,
            'dd_pct': abs(dd.get('dd_pct', 0)),
            'peak': dd.get('peak') or dd.get('peak_date') or '?',
            'trough': dd.get('trough') or dd.get('trough_date') or '?',
        })

    # ─── 时代差异过滤（2026-08-19 修复）───
    # 长期成长股股价翻数倍后，历史回撤底部价（如 META 2018 $130）远低于现价，
    # 已不构成"离现价最近的硬支撑"。硬套会得到荒谬行权价（META Spread 卖腿算成
    # $130，正确 15%OTM≈$470），进而 CBOE 查到深度虚值合约 → IV 虚高 212% → 误判
    # "流动性不足"。
    #
    # 过滤原则（非拍脑袋）：支撑位必须比安全垫更浅才有约束力。SP 安全垫 20%、
    # Spread 15%，若历史底部价 < 现价 50%（比最深安全垫还深 2.5 倍），说明是
    # "时代差异"陈年底部，退回纯安全垫。数据验证：2022 及更早陈年底部全部 <50%，
    # 2025-2026 近期底部全部 >50%，阈值干净分离两类。
    if current_price:
        bottoms = [b for b in bottoms if b['price'] >= current_price * 0.5]
        if not bottoms and drawdowns:
            result.note = "历史底部均为时代差异（<现价50%），退回纯安全垫"
            return result

    # 按价格从高到低排序（最高的底 = 离现价最近的支撑）
    bottoms.sort(key=lambda b: b['price'], reverse=True)

    # 近底 S1 = 最高回撤底（离现价最近的硬支撑）
    if bottoms:
        b = bottoms[0]
        result.s1 = round(b['price'])
        result.s1_label = f"近底 ${result.s1:.0f} ({b['peak']}→{b['trough']}, -{b['dd_pct']:.1f}%)"

    # 深底 S2 = 次高回撤底（如果存在且明显低于 S1）
    if len(bottoms) >= 2 and result.s1:
        b2 = bottoms[1]
        if b2['price'] < result.s1 * 0.85:  # 至少低15%才算独立支撑
            result.s2 = round(b2['price'])
            result.s2_label = f"深底 ${b2['price']:.0f} (-{b2['dd_pct']:.1f}%, {b2['peak']})"

    # 计算最优行权价（支撑位 = 保护，行权价落在支撑位下方）
    if result.s1 and current_price:
        # 击穿检测：现价跌破近底 → "支撑位=保护"语义失效，仅靠安全垫兜底
        result.s1_broken = current_price < result.s1
        result.s2_broken = bool(result.s2) and current_price < result.s2

        # SP: 20% 安全垫向下算，但不高于近底（支撑位成为行权价上方的保护）
        sp_20pct = round(current_price * 0.80)
        result.best_sp_strike = min(sp_20pct, result.s1)

        if result.s1_broken:
            # 近底已被现价击穿 → 行权价取 20%OTM，仅安全垫兜底
            result.optimal_sp_range = f"${sp_20pct:.0f}（近底${result.s1:.0f}已击穿，仅安全垫兜底）"
        elif result.s1 > sp_20pct:
            # 近底在 20%OTM 上方 → 行权价取 20%OTM，近底成为行权价上方的保护
            result.optimal_sp_range = f"${sp_20pct:.0f}（近底${result.s1:.0f}在上方保护）"
        else:
            # 近底更深 → 行权价取近底，安全垫更厚
            result.optimal_sp_range = f"${result.s1:.0f}（近底=行权价，安全垫≥20%）"

        # Spread: 15% 安全垫向下算，支撑位成为卖腿上方防线
        spread_15pct = current_price * 0.85
        result.best_spread_sell = round(min(spread_15pct, result.s1))

        if result.s1_broken:
            result.optimal_spread_range = f"${spread_15pct:.0f}（近底${result.s1:.0f}已击穿，仅安全垫兜底）"
        elif result.s1 > spread_15pct:
            result.optimal_spread_range = f"${spread_15pct:.0f}（近底${result.s1:.0f}在上方保护）"
        else:
            result.optimal_spread_range = f"${result.s1:.0f}（近底=卖腿，安全垫≥15%）"

        # 当前价距近底的距离（正值=近底在当前价下方）
        result.distance_to_s1_pct = round(
            (current_price - result.s1) / result.s1 * 100, 1
        )

    return result


# ─── 自检 ───
if __name__ == '__main__':
    tests = [
        ('HOOD', 94), ('ADBE', 263), ('CRM', 197), ('META', 601),
        ('SPY', 771), ('QQQ', 718),
    ]
    for sym, px in tests:
        support = get_support_levels(sym, current_price=px)
        print(f"=== {sym} @${px} ===")
        print(f"  近底 S1: ${support.s1 or 'N/A'} — {support.s1_label}")
        if support.s2:
            print(f"  深底 S2: ${support.s2:.0f} — {support.s2_label}")
        print(f"  最优SP  : {support.optimal_sp_range}  → 行权价 ${support.best_sp_strike}")
        print(f"  最优Spread: {support.optimal_spread_range}  → 卖腿 ${support.best_spread_sell}")
        print(f"  距近底  : {support.distance_to_s1_pct:+.1f}%" if support.distance_to_s1_pct is not None else "  距近底  : N/A")
        if support.s1_broken:
            print(f"  ⚠️ 支撑位已击穿：现价跌破近底${support.s1:.0f}，仅安全垫兜底")
            if support.s2_broken:
                print(f"  ⚠️ 所有支撑位已击穿（深底${support.s2:.0f}也已跌破）")
        if support.note:
            print(f"  ⚠️ {support.note}")
        print()
