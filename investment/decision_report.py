"""DecisionReport — 统一决策报告 Schema + 动作裁决。

公开库 V2 的核心契约：把 bottom_accelerator / sniper_ah / sell_monitor
三套碎片化输出，统一成一份「完整生命周期决策报告」——一个标的从
买点 → 加仓 → 平仓 → 止损 → 回补，全程用同一 schema 表达，每步带
「输入哪来 / 规则怎么判 / 判成什么」的推导链。

本模块零第三方依赖（纯 dataclass + enum），可独立 import / 单测。

对齐基准：infobase/investment-os/m1-decisionreport-contract.md（v2）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Action(str, Enum):
    """统一动作语义 — 覆盖完整生命周期。"""

    BUY = "BUY"        # 首次建仓
    ADD = "ADD"        # 加仓（DCA 越跌越买）
    HOLD = "HOLD"      # 持有
    TRIM = "TRIM"      # 减仓/止盈
    EXIT = "EXIT"      # 清仓/止损
    WAIT = "WAIT"      # 等待（无持仓，条件未满足）
    REBUY = "REBUY"    # 回补（止盈减仓后跌回）


# 动作优先级（决策点 5 草案）：止损 > 止盈 > 加仓 > 建仓 > 回补 > 持有/等待
_ACTION_PRIORITY = {
    Action.EXIT: 6,
    Action.TRIM: 5,
    Action.ADD: 4,
    Action.BUY: 3,
    Action.REBUY: 2,
    Action.HOLD: 1,
    Action.WAIT: 0,
}


@dataclass
class DataSourceInfo:
    """数据源新鲜度条目（呼应「诚实标注」）。"""

    name: str            # "腾讯行情" / "历史大底常量" / "底部档案" / "akshare 估值"
    kind: str            # "realtime" | "static" | "profile" | "derived"
    as_of: str           # 数据截至（实时="now"，静态="2005-06 起"，档案="2026-08-01"）
    note: str = ""       # 缺失/降级说明


@dataclass
class DimensionCheck:
    """单个判断维度。"""

    name: str            # "大底趋势" / "估值" / "回撤" / "仓位纪律" / "止损"
    ok: bool
    detail: str          # 人可读结果
    rule: str            # 规则（如 "偏离趋势线 -18% → 落入恐慌区"）
    data_source: str     # 数据源（如 "腾讯行情(实时)"）


@dataclass
class TraceStep:
    """推导链单步 — V2 的灵魂（可解释 = 差异化本体）。"""

    step: str            # "拉取历史大底" / "拟合趋势线" / "拉取实时价" / "判定区域"
    input: str           # 输入哪来
    rule: str            # 规则
    output: str          # 结果


@dataclass
class DecisionReport:
    """统一决策报告 — 覆盖完整生命周期。"""

    # ── 标的与路由 ──
    symbol: str                    # 510300 / 600519 / SPY
    market: str                    # "CN" | "HK" | "US"
    asset_class: str               # "etf" | "stock" | "option"
    methodology: str               # bottom_accelerator / sniper_ah / ...
    methodology_label: str         # "ETF定投" / "A股狙击"
    maturity: str                  # "完整" / "骨架(需回测)" / "降级(已证伪)"

    # ── 结论层 ──
    action: Action                 # 最终动作（见 resolve_action）
    confidence: int                # 1-10
    conclusion: str                # 一句话人话结论
    price_zone: str                # 人可读区间（如 "¥4.0-4.3（恐慌区）"）
    price_zone_low: Optional[float] = None
    price_zone_high: Optional[float] = None

    # ── 证据层 ──
    dimensions: List[DimensionCheck] = field(default_factory=list)

    # ── 风险层 ──
    risks: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # ── 推导链 ──
    trace: List[TraceStep] = field(default_factory=list)

    # ── 回测钩子 ──
    backtest_ref: Optional[str] = None

    # ── 时间戳 ──
    generated_at: str = ""
    as_of: str = ""

    # ── 数据源汇总 + schema 版本 ──
    data_sources: List[DataSourceInfo] = field(default_factory=list)
    schema_version: str = "1.0"


def resolve_action(candidates: List[Action], has_position: bool = False) -> Action:
    """按优先级裁决最终动作。

    - 有候选信号 → 返回最高优先级（EXIT > TRIM > ADD > BUY > REBUY）
    - 无信号 → 有持仓 HOLD，无持仓 WAIT
    """
    if candidates:
        return max(candidates, key=lambda a: _ACTION_PRIORITY[a])
    return Action.HOLD if has_position else Action.WAIT


def format_report(r: DecisionReport) -> str:
    """把 DecisionReport 渲染成人类可读的推导链文本。"""
    lines: List[str] = []
    sep = "═" * 56

    lines.append(f"📋 决策报告 | {r.symbol} {r.methodology_label}")
    lines.append(sep)
    lines.append("")

    # 结论层
    lines.append(f"结论  {r.conclusion}")
    lines.append(f"      动作 {r.action.value} · 信心 {r.confidence}/10 · 区间 {r.price_zone}")
    lines.append("")

    # 证据层
    if r.dimensions:
        lines.append("证据")
        for d in r.dimensions:
            mark = "✓" if d.ok else "✗"
            lines.append(f"  {mark} {d.name:<8} {d.detail}  [{d.data_source}]")
        lines.append("")

    # 推导链
    if r.trace:
        lines.append("推导")
        for t in r.trace:
            lines.append(f"  {t.step}")
            lines.append(f"    输入 {t.input}")
            lines.append(f"    规则 {t.rule}")
            lines.append(f"    结果 {t.output}")
        lines.append("")

    # 数据源
    if r.data_sources:
        src = " · ".join(
            f"{s.name}({s.kind},{s.as_of})" + (f"⚠{s.note}" if s.note else "")
            for s in r.data_sources
        )
        lines.append(f"数据  {src}")
        lines.append("")

    # 风险 / 告警
    if r.risks:
        lines.append("风险")
        for x in r.risks:
            lines.append(f"  ⚠ {x}")
        lines.append("")
    if r.warnings:
        lines.append("告警")
        for x in r.warnings:
            lines.append(f"  ⚠ {x}")
        lines.append("")

    # 回测钩子 + 时间戳
    if r.backtest_ref:
        lines.append(f"回测  python -m backtest.run {r.backtest_ref}")
    if r.as_of:
        lines.append(f"数据截至 {r.as_of} · schema v{r.schema_version}")

    return "\n".join(lines).rstrip() + "\n"
