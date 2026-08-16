#!/usr/bin/env python3
"""
Market State Engine — L2.5 市场状态聚合器
===========================================
读取所有L1/L2数据源的产出，聚合成统一的"市场姿势"语言。

设计原则:
  1. 旁路架构 — 读已有模块输出，不改任何一行
  2. 统一姿势语言 — 5个姿势覆盖所有市场
  3. 方向感知 — 不仅"在哪里"，还知道"往哪走"
  4. 早报就绪 — 直接产生可嵌入早报的文本段落

输入:
  - account_b_builder 的三层温度门禁（Cyclical/Momentum）
  - bottom_accelerator V6 的底部+估值双确认
  - market_data_layer 的 Tactical 温度

输出:
  - JSON 结构
  - 人类可读的早报段落

使用: python3 market_state_engine.py
"""

import sys
import os
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from enum import Enum
from datetime import datetime
class Posture(Enum):
    AGGRESSIVE = "⚔️ 进攻"
    SLIGHTLY_ACTIVE = "↗️ 偏积极"
    NORMAL = "➡️ 正常"
    SLIGHTLY_DEFENSIVE = "↘️ 偏防御"
    DEFENSIVE = "🛡️ 防御"
    UNKNOWN = "❓ 未知"


class MarketDirection(Enum):
    RISING_FAST = "↗️↗️ 快速上行"
    RISING = "↗️ 上行"
    SIDEWAYS = "→ 横盘"
    FALLING = "↘️ 下行"
    FALLING_FAST = "↘️↘️ 快速下行"
    UNKNOWN = "❓"


@dataclass
class MarketSnapshot:
    """单个市场的状态快照"""
    name: str
    code: str
    category: str
    price: Optional[float] = None
    ma20: Optional[float] = None
    deviation_pct: Optional[float] = None

    # 三层温度
    cyclical_pct: Optional[float] = None       # 距ATH%
    cyclical_zone: str = ""                    # 🟢/🟡/🔴
    momentum_months: Optional[int] = None      # 连涨月数
    momentum_zone: str = ""                    # 🟢/🟡/🔴
    tactical_change: Optional[float] = None    # 1月涨跌%

    # 底部+估值（V6）
    bottom_multiplier: Optional[float] = None
    bottom_zone: str = ""
    valuation_score: Optional[float] = None
    valuation_zone: str = ""

    # 方向感知
    direction: MarketDirection = MarketDirection.UNKNOWN
    direction_detail: str = ""                 # "近3月涨+5%，速度在加快"

    # 综合
    posture: Posture = Posture.UNKNOWN
    action: str = ""                           # 操作建议

    warnings: List[str] = field(default_factory=list)


@dataclass
class MarketState:
    """全市场聚合状态"""
    timestamp: str = ""
    overall_posture: Posture = Posture.UNKNOWN
    overall_summary: str = ""
    markets: List[MarketSnapshot] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)


# ─── Core ETF mapping ─────────────────────────────────────────────

CORE_ETFS = [
    # (code, tencent_code, name, category, account)
    ("510300", "sh510300", "沪深300", "宽基", "A+B"),
    ("510050", "sh510050", "上证50", "宽基", "B"),
    ("588000", "sh588000", "科创50", "宽基", "A"),
    ("159915", "sz159915", "创业板", "宽基", "A"),
    ("513130", "sh513130", "恒生科技", "宽基-港股", "A"),
    ("159920", "sz159920", "恒生ETF", "宽基-港股", "A"),
    ("512890", "sh512890", "红利低波", "因子", "B"),
]


def fetch_live_prices() -> Dict[str, float]:
    """腾讯API获取核心ETF实时价格"""
    import urllib.request
    tcodes = [t for _, t, _, _, _ in CORE_ETFS]
    url = "http://qt.gtimg.cn/q=" + ",".join(tcodes)
    try:
        raw = urllib.request.urlopen(url, timeout=5).read().decode("gbk")
        prices = {}
        for i, line in enumerate(raw.strip().split("\n")):
            if not line.strip():
                continue
            parts = line.split("~")
            if len(parts) >= 35:
                # Map by position to our tcodes list
                if i < len(tcodes):
                    prices[tcodes[i]] = float(parts[3])
        return prices
    except Exception:
        return {}


def get_gate_data(code: str) -> dict:
    """从月K线获取长周期门禁数据"""
    try:
        from account_b_builder import fetch_monthly_kline, check_long_cycle_gate
        tcode_map = {c: t for c, t, _, _, _ in CORE_ETFS}
        tcode = tcode_map.get(code, "")
        if not tcode:
            return {}
        klines = fetch_monthly_kline(tcode)
        if not klines:
            return {}
        gate = check_long_cycle_gate(klines)
        # Map gate keys to our standard names
        mapped = {
            "cyclical_pct": gate.get("ath_dist_pct"),
            "cyclical_zone": gate.get("cycl_status", ""),
            "momentum_months": gate.get("rising_months"),
            "momentum_zone": gate.get("mom_status", ""),
        }
        # Derive direction from klines
        direction = _derive_direction(klines)
        mapped["direction"] = direction
        return mapped
    except Exception:
        return {}


def _derive_direction(klines: list) -> dict:
    """从月K线推导摆动方向和速度。kline格式: [date, open, close, high, low, vol]"""
    if len(klines) < 4:
        return {"direction": MarketDirection.UNKNOWN}

    closes = [float(k[2]) for k in klines[-6:]]  # index 2 = close, convert to float
    if len(closes) < 4:
        return {"direction": MarketDirection.UNKNOWN}

    # 近期vs远期
    recent = closes[-3:]  # Recent 3 months
    older = closes[-6:-3] if len(closes) >= 6 else closes[:3]

    recent_avg = sum(recent) / len(recent)
    older_avg = sum(older) / len(older)
    change_3m = (recent_avg / older_avg - 1) * 100 if older_avg > 0 else 0

    # 速度：最近1月 vs 前2月平均
    this_month = closes[-1]
    prev_2_avg = sum(closes[-3:-1]) / 2 if len(closes) >= 3 else closes[-2]
    acceleration = (this_month / prev_2_avg - 1) * 100 if prev_2_avg > 0 else 0

    if change_3m > 5:
        direction = MarketDirection.RISING_FAST if acceleration > 2 else MarketDirection.RISING
    elif change_3m > 0:
        direction = MarketDirection.RISING
    elif change_3m > -5:
        direction = MarketDirection.SIDEWAYS
    elif change_3m > -15:
        direction = MarketDirection.FALLING
    else:
        direction = MarketDirection.FALLING_FAST

    return {
        "direction": direction,
        "change_3m_pct": round(change_3m, 1),
        "acceleration": round(acceleration, 1),
    }


def get_valuation_snapshot(code: str) -> dict:
    """获取估值快照"""
    try:
        from valuation_engine import evaluate_etf
        result = evaluate_etf(code)
        return {
            "score": result.composite_score,
            "zone": result.composite_zone.value,
            "pe_pct": result.pe_percentile,
            "pb_pct": result.pb_percentile,
            "div_yield": result.dividend_yield,
            "confidence": result.confidence,
        }
    except Exception:
        return {}


def get_bottom_snapshot(code: str, price: float) -> dict:
    """获取底部加速快照"""
    try:
        from bottom_accelerator import analyze_etf_combined
        result = analyze_etf_combined(code, price)
        if result:
            return {
                "multiplier": result["dca_multiplier"],
                "adjusted": result["dca_adjusted"],
                "zone": result["zone"]["zone_name"],
                "deviation_pct": result["deviation_pct"],
                "confirmation": result.get("confirmation", "N/A"),
            }
    except Exception:
        pass
    return {}


def determine_posture(snapshot: MarketSnapshot) -> Posture:
    """根据多个维度判定单市场姿势"""
    # 先检查防御信号
    defensive_signals = 0
    active_signals = 0

    # Cyclical 🔴 = 强防御信号
    if snapshot.cyclical_zone == "🔴":
        defensive_signals += 2
    elif snapshot.cyclical_zone == "🟡":
        defensive_signals += 1

    # Momentum 🔴 = 防御信号
    if snapshot.momentum_zone == "🔴":
        defensive_signals += 2
    elif snapshot.momentum_zone == "🟡":
        defensive_signals += 1

    # 估值贵
    if snapshot.valuation_zone in ("expensive", "somewhat_expensive"):
        defensive_signals += 1
    elif snapshot.valuation_zone in ("cheap", "somewhat_cheap"):
        active_signals += 1

    # 底部加速
    if snapshot.bottom_multiplier and snapshot.bottom_multiplier >= 3:
        active_signals += 2
    elif snapshot.bottom_multiplier and snapshot.bottom_multiplier >= 2:
        active_signals += 1

    if defensive_signals >= 4:
        return Posture.DEFENSIVE
    elif defensive_signals >= 2:
        return Posture.SLIGHTLY_DEFENSIVE
    elif active_signals >= 2:
        return Posture.AGGRESSIVE if active_signals >= 3 else Posture.SLIGHTLY_ACTIVE
    else:
        return Posture.NORMAL


def generate_actions(state: MarketState) -> List[str]:
    """根据市场状态生成操作建议"""
    actions = []
    account_b_buy = []
    account_b_avoid = []
    account_a_harvest = []

    for m in state.markets:
        if m.posture in (Posture.DEFENSIVE, Posture.SLIGHTLY_DEFENSIVE):
            if "A" in getattr(m, '_account', ""):
                account_a_harvest.append(m.name)
            else:
                account_b_avoid.append(m.name)
        elif m.posture in (Posture.SLIGHTLY_ACTIVE, Posture.AGGRESSIVE):
            if "B" in getattr(m, '_account', ""):
                account_b_buy.append(m.name)

    if account_b_buy:
        actions.append(f"· Account B: {'/'.join(account_b_buy)} 可正常DCA")
    if account_b_avoid:
        actions.append(f"· Account B: {'/'.join(account_b_avoid)} 暂停新仓")
    if account_a_harvest:
        actions.append(f"· Account A: {'/'.join(account_a_harvest)} 已达底仓上限，超额可收割")

    if not actions:
        actions.append("· 正常节奏，无需调整")

    return actions


def build_market_state() -> MarketState:
    """构建全市场状态"""
    state = MarketState(timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"))
    prices = fetch_live_prices()

    # Map tcode → code
    code_to_tcode = {c: t for c, t, _, _, _ in CORE_ETFS}

    for code, tcode, name, cat, account in CORE_ETFS:
        price = prices.get(tcode)
        snap = MarketSnapshot(name=name, code=code, category=cat, price=price)
        snap._account = account  # internal tracking

        # Gate data
        gate = get_gate_data(code)
        if gate:
            snap.cyclical_pct = gate.get("cyclical_pct")
            snap.cyclical_zone = gate.get("cyclical_zone", "")
            snap.momentum_months = gate.get("momentum_months")
            snap.momentum_zone = gate.get("momentum_zone", "")
            direction_info = gate.get("direction", {})
            snap.direction = direction_info.get("direction", MarketDirection.UNKNOWN)
            snap.direction_detail = (
                f"近3月{direction_info.get('change_3m_pct', '?')}%"
            )

        # Valuation
        val = get_valuation_snapshot(code)
        if val:
            snap.valuation_score = val.get("score")
            snap.valuation_zone = val.get("zone", "")

        # Bottom
        if price:
            bottom = get_bottom_snapshot(code, price)
            if bottom:
                snap.bottom_multiplier = bottom.get("multiplier")
                snap.bottom_zone = bottom.get("zone", "")

        # Determine posture
        snap.posture = determine_posture(snap)

        # Warnings
        if snap.cyclical_zone == "🔴":
            snap.warnings.append(f"距历史高点仅{abs(snap.cyclical_pct or 0):.0f}%")

        state.markets.append(snap)

    # Overall posture
    if state.markets:
        defense_count = sum(1 for m in state.markets
                          if m.posture in (Posture.DEFENSIVE, Posture.SLIGHTLY_DEFENSIVE))
        active_count = sum(1 for m in state.markets
                         if m.posture in (Posture.AGGRESSIVE, Posture.SLIGHTLY_ACTIVE))

        if defense_count >= len(state.markets) * 0.5:
            state.overall_posture = Posture.DEFENSIVE
        elif defense_count >= len(state.markets) * 0.3:
            state.overall_posture = Posture.SLIGHTLY_DEFENSIVE
        elif active_count >= len(state.markets) * 0.3:
            state.overall_posture = Posture.SLIGHTLY_ACTIVE
        else:
            state.overall_posture = Posture.NORMAL

    state.actions = generate_actions(state)
    return state


# ─── Output formatters ────────────────────────────────────────────

def format_briefing(state: MarketState) -> str:
    """生成早报前置段落"""
    lines = []
    lines.append(f"## 📍 今日市场姿势 — {state.timestamp}")
    lines.append("")

    posture_emoji = {
        Posture.AGGRESSIVE: "⚔️", Posture.SLIGHTLY_ACTIVE: "↗️",
        Posture.NORMAL: "➡️", Posture.SLIGHTLY_DEFENSIVE: "↘️",
        Posture.DEFENSIVE: "🛡️", Posture.UNKNOWN: "❓",
    }
    emoji = posture_emoji.get(state.overall_posture, "❓")
    lines.append(f"  A股整体:  {emoji} {state.overall_posture.value}")
    lines.append("")

    # Per-market detail
    for m in state.markets:
        cycl_str = f"{m.cyclical_zone}距ATH{abs(m.cyclical_pct or 0):.0f}%" if m.cyclical_zone else "?"
        dir_str = m.direction.value if m.direction != MarketDirection.UNKNOWN else ""
        val_str = f"📊{m.valuation_score:.0f}分" if m.valuation_score else ""

        action_icon = {Posture.DEFENSIVE: "🛑", Posture.SLIGHTLY_DEFENSIVE: "⚠️",
                       Posture.AGGRESSIVE: "🔥", Posture.SLIGHTLY_ACTIVE: "✅",
                       Posture.NORMAL: "  "}.get(m.posture, "  ")

        line = f"  {action_icon} {m.name:<6} {cycl_str:<14} {dir_str:<10} {val_str}"
        if m.warnings:
            line += f"  ⚠️{'/'.join(m.warnings)}"
        lines.append(line)
    lines.append("")

    # Actions
    lines.append("  ⚡ 今日行动指引:")
    for action in state.actions:
        lines.append(f"  {action}")

    return "\n".join(lines)


def format_json(state: MarketState) -> dict:
    """生成JSON格式（供dashboard等消费）"""
    return {
        "timestamp": state.timestamp,
        "overall_posture": state.overall_posture.value,
        "markets": [
            {
                "name": m.name,
                "code": m.code,
                "category": m.category,
                "price": m.price,
                "posture": m.posture.value,
                "cyclical_zone": m.cyclical_zone,
                "momentum_zone": m.momentum_zone,
                "valuation_zone": m.valuation_zone,
                "valuation_score": m.valuation_score,
                "bottom_zone": m.bottom_zone,
                "direction": m.direction.value,
                "warnings": m.warnings,
            }
            for m in state.markets
        ],
        "actions": state.actions,
    }


# ─── CLI ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import argparse

    parser = argparse.ArgumentParser(description="Market State Engine")
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    parser.add_argument("--cache", action="store_true", help="Write to dashboard cache file")
    args = parser.parse_args()

    state = build_market_state()
    data = format_json(state)
    json_str = json.dumps(data, ensure_ascii=False, indent=2)

    if args.cache:
        cache_path = os.path.expanduser("~/.hermes/data/market_state_cache.json")
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w") as f:
            f.write(json_str)
        print(f"Cache written: {cache_path}")

    if args.json:
        print(json_str)
    else:
        print(format_briefing(state))
