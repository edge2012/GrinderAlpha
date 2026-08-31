#!/usr/bin/env python3
"""Account C (US-TREND) — TrendFollowing SellMonitor：SPY/QQQ 200MA 暂停 DCA。

设计依据（2026-08-23 Account C 止损回测）：
- 200MA 对 SPY/QQQ 净值最高（SPY +10.31 / QQQ +23.20），且闪崩误杀最轻
- 「别下车」哲学：跌破 200MA 只「暂停 DCA」不清仓，避免 2020 式 V 型闪崩误杀
- 建仓期：只展示不拦截（08-13 战略，专注建仓不择时）
- ⚠️ 暂不实现「升级保护本金」——用户确认此概念之前没有

与 Account B 差异：均线周期 200MA（vs A/H 50MA）+ 数据源 US 直接标的价（vs 映射指数）。
"""
from __future__ import annotations

import sys
import os


from investment.sell_monitors.base import (
    SellMonitor, SellReport, HoldingStatus, HoldSignal, HoldingCheck,
)
from investment.sell_monitors import register

# 建满判定：当前占比 ≥ 目标×0.9（复用 Account B 战略）
BUILD_FULL_RATIO = 0.9
# 200MA 暂停 DCA 阈值（建满后）：距 200MA < -2% → 暂停
PAUSE_THRESHOLD_PCT = -2.0
# 数据需要 ≥ 周期+缓冲 才能算均线
MA_PERIOD = 200
MIN_BARS = MA_PERIOD + 5


@register("US-TREND")
class TrendFollowingSellMonitor(SellMonitor):
    """Account C：SPY/QQQ 用 200MA 暂停 DCA。"""

    def __init__(self, config: dict, position_provider=None):
        self.config = config or {}
        # 从 config 读 target 分配（SPY 65% / QQQ 25%），用于建满判定
        self.target_allocation = (self.config.get("target_allocation")
                                  or {"SPY": 0.65, "QQQ": 0.25})
        # US 直接标的价（无映射指数，均线用标的自身日线）
        self.us_symbols = list(self.target_allocation.keys())
        # PositionProvider 注入：生产用 DBPositionProvider，测试/外部可注入 DictPositionProvider
        self._positions = position_provider

    # ── 数据层：拉 US 标的日线 ──
    # 走 backtest.data 公开数据层（Yahoo v8，重试 + 缓存降级），
    # 与 scan_engines 解耦（公开库不同步 scan_engines）。
    # 返回 list[float] 收盘序列，与旧签名保持一致。
    def _fetch_daily(self, symbol: str) -> list[float]:
        from backtest.data import fetch_yahoo_daily
        df = fetch_yahoo_daily(symbol, range_str="2y")
        return df["close"].tolist() if not df.empty else []

    def _fetch_price(self, symbol: str) -> float | None:
        """拉取 US 标的实时价。"""
        closes = self._fetch_daily(symbol)
        return closes[-1] if closes else None

    # ── 核心：均线判断（纯函数，不依赖网络，可测）──
    @staticmethod
    def _judge_ma_status(
        closes: list[float], target: float, actual_pct: float
    ) -> HoldingStatus:
        """由收盘价序列判断某标的的 200MA 暂停 DCA 状态。"""
        entry = HoldingStatus(code="", name="", rule_type="200MA", is_full=False)
        if len(closes) < MIN_BARS:
            entry.status = "⚠️"
            entry.dca_action = "数据不足"
            return entry

        price = closes[-1]
        ma = sum(closes[-MA_PERIOD:]) / MA_PERIOD
        dist = (price / ma - 1) * 100
        entry.price = round(price, 2)
        entry.ma = round(ma, 2)
        entry.dist_pct = round(dist, 1)

        # 建满判定：当前占比 ≥ 目标×0.9
        is_full = target > 0 and actual_pct >= target * BUILD_FULL_RATIO
        entry.is_full = is_full

        if not is_full:
            entry.status = "🟢" if dist >= 0 else "🔴"
            entry.dca_action = "建仓期"   # 建仓期只展示不拦截（08-13 战略）
        elif dist < PAUSE_THRESHOLD_PCT:
            entry.status = "🔴"
            entry.dca_action = "暂停DCA"
        elif abs(dist) < 2:
            entry.status = "🟡"
            entry.dca_action = "待确认"
        else:
            entry.status = "🟢"
            entry.dca_action = "可执行"
        return entry

    # ── AC 单标的 200MA 暂停 DCA 状态（拉数据后调纯函数）──
    def _compute_ma_status(
        self, code: str, name: str, target: float, actual_pct: float
    ) -> HoldingStatus:
        entry = self._judge_ma_status(self._fetch_daily(code), target, actual_pct)
        entry.code = code
        entry.name = name
        return entry

    # ── SellMonitor 接口 ──
    def scan(self, config: dict) -> SellReport:
        # 从 PositionProvider 读 Account C (US-TREND) 持仓
        provider = self._positions or self._get_default_provider()
        holdings = provider.get_holdings("US")
        report = SellReport(account_id=self.account_id, silent=True)

        if not holdings:
            report.silent = True
            return report

        for h in holdings:
            code = h.get("code", "")
            name = h.get("name", "")
            pct = h.get("account_pct", 0)
            target = self.target_allocation.get(code, 0.0)
            # 只监控 core etf（SPY/QQQ）——个股/期权持仓不在此止损范围
            if code not in self.us_symbols:
                continue
            status = self._compute_ma_status(code, name, target, pct)
            report.statuses.append(status)

        report.silent = all(
            s.dca_action in ("可执行", "建仓期") for s in report.statuses
        )
        return report

    @staticmethod
    def _get_default_provider():
        """延迟创建 DBPositionProvider（生产默认，避免模块级硬依赖 position_store）。"""
        from investment.sell_monitors.base import DBPositionProvider
        return DBPositionProvider()

    def format_output(self, report: SellReport) -> str:
        if report.silent:
            return "[SELL_SILENT]"
        lines = []
        lines.append(f"📊 200MA 持仓状态 (Account C) | {len(report.statuses)}只")
        lines.append("")
        lines.append(f"  {'标的':<8} {'规则':<8} {'数据':>16} {'距200MA':>8} {'DCA':<10}")
        lines.append(f"  {'─'*8} {'─'*8} {'─'*16} {'─'*8} {'─'*10}")
        for s in report.statuses:
            data = f"${s.price}/MA{s.ma:.0f}" if s.price else "—"
            dist = f"{s.dist_pct:+.1f}%" if s.dist_pct is not None else "—"
            lines.append(f"  {s.code:<8} {s.rule_type:<8} {data:>16} {dist:>8} "
                         f"{s.status} {s.dca_action}")
        lines.append("")
        lines.append("> 200MA 规则（2026-08-23 回测）：建仓期只展示不拦截 | "
                     "建满后 < -2% → 🔴 暂停DCA | ±2%内 → 🟡 待确认 | > +2% → 🟢 可执行")
        paused = [s for s in report.statuses if s.dca_action == "暂停DCA"]
        if paused:
            lines.append("")
            lines.append("🚫 200MA 暂停DCA — 以下持仓今日不可加仓（别下车，只暂停补仓）：")
            for s in paused:
                lines.append(f"  {s.code} {s.name} — {s.status} {s.dca_action}")
        return "\n".join(lines)
