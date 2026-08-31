#!/usr/bin/env python3
"""Account A (AH-FUND) — IndexDCA SellMonitor：场外基金跟投指数止盈监控。

迁移自 scripts/account_a_sell_monitor.py（桥接 → 原生实现），去硬依赖：
- a_map/hk_map（基金名→指数代码映射）：从 config[\"index_map\"] 读（fallback 到默认值）
- load_account_a()：从 config[\"account_a\"] 读持仓（原生实现）
- fetch_ma12_ratio()：直接拉腾讯月K（原生实现，不再桥接旧脚本）

设计与 Account B/C 差异（已确认架构决策）：
- Account A = 指数定投，日历驱动**不择时** → **无「暂停DCA」语义**（本来就照投）。
  所以 SellMonitor 侧 statuses 全部标记「无止损规则」（建仓不暂停），
  核心是**卖出侧止盈**：L1 close/MA12 ≥1.50 减50% / ≥1.30 减25% + L2 超配15%。

⚠️「暂停DCA / 升级保护本金」对 Account A 不适用——建仓端无止损语义（不择时），
只有卖出侧的止盈减仓。这是策略差异（index_dca vs mean_reversion_dca/trend_following）。
"""
from __future__ import annotations

import os
import json

from investment.sell_monitors.base import (  # noqa: E402
    SellMonitor, SellReport, HoldingStatus, HoldSignal, HoldingCheck,
)
from investment.sell_monitors import register  # noqa: E402

# 阈值（与 account_a_sell_monitor.py 一致）
L1_RED = 1.50     # 减50%
L1_YELLOW = 1.30  # 减25%
L1_EDGE = 1.25    # 关注线
L2_OVERWEIGHT = 0.15

# Account A config 路径
_CONFIG_PATH = os.environ.get("PORTFOLIO_CONFIG_PATH", "")

# ── 默认指数映射（迁移期 fallback，正式从 config 读）──
_DEFAULT_A_MAP = {
    "沪深300": ("sh000300", "沪深300"),
    "科创50": ("sh000688", "科创50"),
    "创业板": ("sz399006", "创业板指"),
    "中证红利": ("sh000015", "上证红利"),
    "消费": ("sh000932", "中证消费"),
    "医药": ("sh000933", "中证医药"),
}
_DEFAULT_HK_MAP = {
    "恒生科技": ("hkHSTECH", "恒生科技"),
    "恒生指数": ("hkHSI", "恒生指数"),
}


@register("AH-FUND")
class IndexDCASellMonitor(SellMonitor):
    """Account A：止盈高位减仓（建仓不择时 → 无暂停DCA）。

    核心逻辑原生实现（从 scripts/account_a_sell_monitor.py 迁移），去硬依赖：
    - a_map/hk_map 从 config[\"index_map\"] 读（fallback 到默认值）
    - load_account_a() / fetch_ma12_ratio() 原生实现
    """

    def __init__(self, config: dict):
        self.config = config or {}
        # 指数映射从 config 读，fallback 到默认值（迁移期兼容）
        self.a_map, self.hk_map = self._load_index_maps()

    @staticmethod
    def _load_index_maps() -> tuple[dict, dict]:
        """从 config[\"index_map\"] 读指数映射。

        config 格式（JSON）:
            "index_map": {
                "a_share": {"沪深300": {"tcode": "sh000300", "display": "沪深300"}, ...},
                "hk": {"恒生科技": {"tcode": "hkHSTECH", "display": "恒生科技"}, ...}
            }

        fallback: 无 config 时用 _DEFAULT_A_MAP/_DEFAULT_HK_MAP（迁移期兼容）。
        """
        try:
            with open(_CONFIG_PATH) as f:
                cfg = json.load(f)
            idx_map = cfg.get("account_a", {}).get("index_map", {})
            if idx_map:
                a_map = {}
                for name, spec in idx_map.get("a_share", {}).items():
                    if isinstance(spec, dict):
                        a_map[name] = (spec.get("tcode", ""), spec.get("display", name))
                    elif isinstance(spec, (list, tuple)) and len(spec) >= 2:
                        a_map[name] = (spec[0], spec[1])
                hk_map = {}
                for name, spec in idx_map.get("hk", {}).items():
                    if isinstance(spec, dict):
                        hk_map[name] = (spec.get("tcode", ""), spec.get("display", name))
                    elif isinstance(spec, (list, tuple)) and len(spec) >= 2:
                        hk_map[name] = (spec[0], spec[1])
                return a_map, hk_map
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return dict(_DEFAULT_A_MAP), dict(_DEFAULT_HK_MAP)

    # ── 数据层 ──

    def _load_account_a(self) -> tuple[list[dict], float]:
        """从 config 读 Account A 持仓 → (positions, total_value)。

        原生实现（迁移自 scripts/account_a_sell_monitor.py load_account_a()）。
        公开库无 portfolio_config.json 时优雅降级返回空（不抛 FileNotFoundError）。
        """
        try:
            with open(_CONFIG_PATH) as f:
                config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return [], 0.0
        acct_a = config.get("account_a", {})

        positions = []
        total = acct_a.get("total_value_approx", 0)

        # Parse a_holdings_etf
        a_etfs = acct_a.get("a_holdings_etf", {})
        for index_name, info in a_etfs.items():
            if index_name in self.a_map:
                tcode, display = self.a_map[index_name]
                value = info.get("value_approx", 0)
                note = info.get("note", "")
                positions.append({
                    "index_name": display,
                    "tcode": tcode,
                    "fund_detail": note,
                    "value": value,
                })

        # Parse hk_holdings_etf
        hk_etfs = acct_a.get("hk_holdings_etf", {})
        for index_name, info in hk_etfs.items():
            if index_name in self.hk_map:
                tcode, display = self.hk_map[index_name]
                value = info.get("value_approx", 0)
                note = info.get("note", "")
                positions.append({
                    "index_name": display,
                    "tcode": tcode,
                    "fund_detail": note,
                    "value": value,
                })

        return positions, total

    @staticmethod
    def _fetch_ma12_ratio(tcode: str) -> float | None:
        """Fetch monthly K-line and compute close/MA12 ratio.

        走 backtest.data 公开数据层（腾讯月K），与 Account B 月K同源。
        """
        from backtest.data import fetch_tencent_kline
        df = fetch_tencent_kline(tcode, freq="month", count=30)
        if df.empty or len(df) < 18:
            return None

        closes = df["close"].tolist()
        ma12 = sum(closes[-12:]) / 12
        if ma12 == 0:
            return None
        return closes[-1] / ma12

    # ── SellMonitor 接口 ──

    def scan(self, config: dict) -> SellReport:
        report = SellReport(account_id=self.account_id, silent=True)
        positions, total = self._load_account_a()
        if not positions:
            report.silent = True
            return report

        for pos in positions:
            ratio = self._fetch_ma12_ratio(pos["tcode"])
            if ratio is None:
                continue
            pct = pos["value"] / total * 100 if total > 0 else 0
            signals = []

            # L1 止盈减仓
            if ratio >= L1_RED:
                signals.append(HoldSignal(layer="L1", level="🔴",
                    msg=f"减仓50%（MA12位+{(ratio-1)*100:.0f}%，12月胜率仅24%）"))
            elif ratio >= L1_YELLOW:
                signals.append(HoldSignal(layer="L1", level="🟡",
                    msg=f"减仓25%（MA12位+{(ratio-1)*100:.0f}%，P90分位）"))

            # L2 超配
            if pct >= L2_OVERWEIGHT * 100:
                signals.append(HoldSignal(layer="L2", level="🟡",
                    msg=f"超配{pct:.0f}%，建议关注"))

            report.checks.append(HoldingCheck(
                code=pos["tcode"], name=pos["index_name"], shares=0,
                cost=0, price=0, pct=pct / 100, ma12_ratio=ratio,
                signals=signals,
            ))

            # statuses：Account A 无需建立 index DCA 状态（不择时 → 无止损暂停）
            report.statuses.append(HoldingStatus(
                code=pos["tcode"], name=pos["index_name"], rule_type="none",
                is_full=False, status="🟢", dca_action="无止损规则(指数定投)"))

        report.silent = not any(c.signals for c in report.checks)
        return report

    def format_output(self, report: SellReport) -> str:
        if report.silent:
            return "[SELL_SILENT]"
        lines = []
        lines.append(f"📤 Account A 卖出监控 | {len(report.checks)}只")
        lines.append("")
        for c in report.checks:
            if not c.signals:
                continue
            for s in c.signals:
                if s.layer == "L1":
                    lines.append(f"  {s.level} {c.name} — {s.msg}")
                    lines.append(f"     MA12比: {c.ma12_ratio:.2f}x | 仓位 {c.pct*100:.1f}%")
                elif s.layer == "L2":
                    lines.append(f"  🟡 {c.name} — {s.msg}")
        lines.append("")
        lines.append("━━ 全量速览 ━━")
        lines.append(f"  {'指数':<10} {'MA12比':>7} {'占比':>6} {'操作'}")
        for c in report.checks:
            r = c.ma12_ratio or 0
            if r >= L1_RED:
                action = "🔴 减50%"
            elif r >= L1_YELLOW:
                action = "🟡 减25%"
            elif r >= L1_EDGE:
                action = "⚠️ 关注"
            else:
                action = "持有"
            lines.append(f"  {c.name:<10} {r:.2f}x {c.pct*100:>5.1f}% {action}")
        return "\n".join(lines)
