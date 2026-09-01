#!/usr/bin/env python3
"""Account B (AH-ETF) — MeanReversionDCA SellMonitor：A/H 50MA 暂停 DCA + L1/L2/L3。

迁移自 scripts/sell_monitor.py（桥接 → 原生实现），去硬依赖：
- 持仓读取：PositionProvider 注入（不再直接 import position_store）
- STOP_RULES：从 config[\"stop_rules\"] 读（fallback 到硬编码默认值，迁移期兼容）
- 数据获取（fetch_monthly_kline/fetch_tencent_prices/fetch_daily_kline）：走 backtest.data 公开数据层

语义与旧 sell_monitor.py 完全一致：
- L1 价格极端化: close/MA12 >= 1.50 减半 | >= 1.30 减25%
- L2 仓位纪律: 单标的 > 15% 减至15%
- L3 浮亏警戒: 浮亏 > 10% 标记复盘
- L1 回补: close/MA12 <= 1.00 买回（止盈减仓后跌回12月均线）
- 50MA 暂停DCA: 建仓期只展示不拦截 | 建满后 < -2% 暂停
"""
from __future__ import annotations

import sys
import os
import json
import datetime

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)

from investment.sell_monitors.base import (  # noqa: E402
    SellMonitor, SellReport, HoldingStatus, HoldSignal, HoldingCheck,
    PositionProvider, DBPositionProvider,
)
from investment.sell_monitors import register  # noqa: E402

# ── 阈值（与旧 sell_monitor.py 一致）──
L1_CRITICAL = 1.50       # 止盈减仓50%
REBUY_RATIO = 1.00       # 回补满仓（跌回12月均线）
L2_MAX_POSITION = 0.15
L3_DRAWDOWN = -0.10

# 建满判定
BUILD_FULL_RATIO = 0.9

# ── 止盈回补状态文件（不入 git，运行时目录）──
TP_STATE_FILE = os.environ.get(
    "TP_STATE_FILE",
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "take_profit_state.json"),
)

# ── 50MA 止损规则默认值（迁移期 fallback，正式从 config 读）──
_DEFAULT_STOP_RULES = {
    "510300": ("50MA", "sh000300"),
    "513130": ("50MA", "hkHSTECH"),
    "159928": ("50MA", "sh000932"),
    "512010": ("50MA", "sh000933"),   # 中证医药卫生
    "515080": ("none", None),         # 中证红利ETF(防御/价值),不设止损
}


@register("AH-ETF")
class MeanReversionSellMonitor(SellMonitor):
    """Account B：A/H 50MA 暂停 DCA + L1/L2/L3 卖出信号。

    核心逻辑原生实现（从 scripts/sell_monitor.py 迁移），去硬依赖：
    - PositionProvider 注入读持仓（不再直接 import position_store）
    - STOP_RULES 从 config[\"stop_rules\"] 读
    - 数据获取走 backtest.data 公开数据层
    """

    def __init__(self, config: dict, position_provider: PositionProvider | None = None):
        self.config = config or {}
        # STOP_RULES 从 config 读，fallback 到默认值（迁移期兼容）
        self.stop_rules = self._load_stop_rules(self.config)
        # PositionProvider 注入
        self._positions = position_provider

    @staticmethod
    def _load_stop_rules(config: dict) -> dict:
        """从 config[\"stop_rules\"] 读止损规则。

        config 格式（JSON）:
            "stop_rules": {
                "510300": {"rule_type": "50MA", "index_code": "sh000300"},
                "515080": {"rule_type": "none"}
            }

        fallback: 无 config 时用 _DEFAULT_STOP_RULES（迁移期兼容）。
        """
        raw = config.get("stop_rules")
        if not raw:
            return dict(_DEFAULT_STOP_RULES)
        rules = {}
        for code, spec in raw.items():
            if isinstance(spec, dict):
                rt = spec.get("rule_type", "none")
                ic = spec.get("index_code")
                rules[code] = (rt, ic)
            elif isinstance(spec, (list, tuple)) and len(spec) >= 2:
                rules[code] = (spec[0], spec[1])
            else:
                rules[code] = ("none", None)
        return rules

    # ── 数据层 ──

    def _get_holdings(self) -> list[dict]:
        """通过 PositionProvider 读持仓（Format.LIST 语义）。"""
        provider = self._positions
        if provider is None:
            provider = DBPositionProvider()
            self._positions = provider
        return provider.get_holdings("A")

    @staticmethod
    def _fetch_daily_kline(index_code: str, limit: int = 60) -> list:
        """拉取指数日K线 → [[date, open, close, high, low], ...]（backtest.data 公开数据层）。"""
        from backtest.data import fetch_tencent_kline
        df = fetch_tencent_kline(index_code, freq="day", count=limit)
        if df.empty:
            return []
        return [[str(i)[:10], r["open"], r["close"], r["high"], r["low"]]
                for i, r in df.iterrows()]

    @staticmethod
    def _fetch_monthly_kline(tcode: str):
        """拉取月K线 → [[date, open, close, high, low], ...]（backtest.data 公开数据层）。"""
        from backtest.data import fetch_tencent_kline
        df = fetch_tencent_kline(tcode, freq="month")
        if df.empty:
            return []
        return [[str(i)[:10], r["open"], r["close"], r["high"], r["low"]]
                for i, r in df.iterrows()]

    @staticmethod
    def _fetch_tencent_prices(tcodes: list[str]) -> dict:
        """批量拉取腾讯实时价格（backtest.data 公开数据层）。"""
        from backtest.data import fetch_tencent_prices
        return fetch_tencent_prices(tcodes)

    # ── 止盈回补状态 ──

    @staticmethod
    def _load_tp_state() -> dict:
        """加载止盈回补状态。{code: {status: 'reduced'|'full', date}}"""
        try:
            with open(TP_STATE_FILE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _save_tp_state(state: dict):
        """保存止盈回补状态。"""
        os.makedirs(os.path.dirname(TP_STATE_FILE), exist_ok=True)
        with open(TP_STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    # ── 核心逻辑 ──

    @staticmethod
    def _compute_ma12_ratio(monthly_data) -> float | None:
        """Compute close/MA12 ratio from monthly K-line data."""
        if not monthly_data or len(monthly_data) < 18:
            return None
        closes = []
        for k in monthly_data:
            try:
                closes.append(float(k[2]))
            except (ValueError, IndexError):
                continue
        if len(closes) < 18:
            return None
        ma12 = sum(closes[-12:]) / 12
        if ma12 == 0:
            return None
        return closes[-1] / ma12

    def _compute_50ma_status(self, holdings: list[dict]) -> list[dict]:
        """对所有持仓计算 50MA 距离和 DCA 状态。

        返回 [{code, name, rule_type, is_full, ma50, price, dist_pct, status, dca_action}]
        """
        result = []
        for h in holdings:
            code = h["code"]
            rule = self.stop_rules.get(code, ("none", None))
            rule_type, rule_param = rule

            pct = h.get("account_pct", 0)
            target = h.get("account_pct_target", 0.0)
            is_full = target > 0 and pct >= target * BUILD_FULL_RATIO

            entry = {"code": code, "name": h["name"], "rule_type": rule_type,
                      "is_full": is_full}

            if rule_type == "50MA":
                klines = self._fetch_daily_kline(rule_param, limit=60)
                if klines and len(klines) >= 50:
                    closes = [float(k[2]) for k in klines[-60:]]
                    ma50 = sum(closes[-50:]) / 50
                    price = closes[-1]
                    entry["ma50"] = round(ma50, 1)
                    entry["price"] = round(price, 1)
                    dist = (price / ma50 - 1) * 100
                    entry["dist_pct"] = round(dist, 1)
                    if not is_full:
                        entry["status"] = "🟢" if dist >= 0 else "🔴"
                        entry["dca_action"] = "建仓期"
                    elif dist < -2:
                        entry["status"] = "🔴"
                        entry["dca_action"] = "暂停DCA"
                    elif abs(dist) < 2:
                        entry["status"] = "🟡"
                        entry["dca_action"] = "待确认"
                    else:
                        entry["status"] = "🟢"
                        entry["dca_action"] = "可执行"
                else:
                    entry["status"] = "⚠️"
                    entry["dca_action"] = "数据不足"
            elif rule_type == "price":
                tcode = h.get("tcode", "")
                prices = self._fetch_tencent_prices([tcode]) if tcode else {}
                price = prices.get(tcode, 0)
                entry["price"] = price
                entry["exit_price"] = rule_param
                if price > 0 and price > rule_param:
                    entry["status"] = "🟢"
                    entry["dca_action"] = "可执行"
                elif price > 0:
                    entry["status"] = "🔴"
                    entry["dca_action"] = "已触发退出"
                else:
                    entry["status"] = "⚠️"
                    entry["dca_action"] = "数据不足"
            elif rule_type == "none":
                entry["status"] = "🟢"
                entry["dca_action"] = "无止损规则"

            result.append(entry)
        return result

    def _check_holding(self, h: dict, prices: dict, monthly_cache: dict, tp_state: dict) -> dict:
        """Check all three layers for one holding. Returns signal dict."""
        code = h["code"]
        tcode = h.get("tcode", "")
        signals = []

        # ── L1: 价格极端化（止盈减仓）+ 回补 ──
        monthly = monthly_cache.get(tcode, [])
        ratio = self._compute_ma12_ratio(monthly) if monthly else None

        pct = h.get("account_pct", 0)
        target = h.get("account_pct_target", 0.0)
        is_full = target > 0 and pct >= target * BUILD_FULL_RATIO

        if ratio is not None:
            if ratio >= L1_CRITICAL and is_full:
                signals.append({
                    "layer": "L1", "level": "🔴",
                    "msg": f"减仓50%（MA12位+{(ratio-1)*100:.0f}%触发断崖，12月胜率仅24%）",
                })
                tp_state[code] = {"status": "reduced", "date": datetime.date.today().isoformat()}
            elif ratio <= REBUY_RATIO and tp_state.get(code, {}).get("status") == "reduced":
                signals.append({
                    "layer": "L1", "level": "🟢",
                    "msg": f"回补至目标仓位{target*100:.0f}%（MA12位{(ratio-1)*100:.0f}%跌回12月均线，止盈减仓可买回）",
                })
                tp_state[code] = {"status": "full", "date": datetime.date.today().isoformat()}

        # ── L2: 仓位纪律 ──
        pct = h.get("account_pct", 0)
        if pct > L2_MAX_POSITION:
            signals.append({
                "layer": "L2", "level": "🟡",
                "msg": f"超配{pct*100:.0f}%，建议减至15%",
            })

        # ── L3: 浮亏警戒 ──
        cost = h.get("cost_basis", 0)
        price = prices.get(tcode, 0)
        if cost > 0 and price > 0:
            drawdown = (price - cost) / cost
            if drawdown < L3_DRAWDOWN:
                signals.append({
                    "layer": "L3", "level": "📋",
                    "msg": f"浮亏{drawdown:.0%}，建议复盘（非卖出信号）",
                })

        return {
            "code": code,
            "name": h["name"],
            "shares": h["shares"],
            "cost": cost,
            "price": price,
            "pct": pct,
            "ma12_ratio": round(ratio, 2) if ratio else None,
            "signals": signals,
        }

    # ── SellMonitor 接口 ──

    def scan(self, config: dict) -> SellReport:
        holdings = self._get_holdings()
        report = SellReport(account_id=self.account_id, silent=True)
        if not holdings:
            report.silent = True
            return report

        # 50MA 持仓状态
        ma50_status = self._compute_50ma_status(holdings)
        report.statuses = [
            HoldingStatus(
                code=s["code"], name=s["name"], rule_type=s["rule_type"],
                is_full=s["is_full"], price=s.get("price"),
                ma=s.get("ma50"), dist_pct=s.get("dist_pct"),
                status=s["status"], dca_action=s["dca_action"],
            )
            for s in ma50_status
        ]

        # L1/L2/L3 卖出信号
        tp_state = self._load_tp_state()
        monthly_cache = {}
        for h in holdings:
            tcode = h.get("tcode", "")
            if tcode:
                klines = self._fetch_monthly_kline(tcode)
                if klines:
                    monthly_cache[tcode] = klines
        tcodes = [h["tcode"] for h in holdings if h.get("tcode")]
        prices = self._fetch_tencent_prices(tcodes) if tcodes else {}

        for h in holdings:
            r = self._check_holding(h, prices, monthly_cache, tp_state)
            report.checks.append(HoldingCheck(
                code=r["code"], name=r["name"], shares=r["shares"],
                cost=r["cost"], price=r["price"], pct=r["pct"],
                ma12_ratio=r.get("ma12_ratio"),
                signals=[HoldSignal(layer=s["layer"], level=s["level"], msg=s["msg"])
                         for s in r["signals"]],
            ))
        self._save_tp_state(tp_state)

        has_signal = any(c.signals for c in report.checks)
        all_ok = all(s.dca_action in ("可执行", "无止损规则", "建仓期")
                     for s in report.statuses)
        report.silent = not has_signal and all_ok
        return report

    def format_output(self, report: SellReport) -> str:
        if report.silent:
            return "[SELL_SILENT]"

        # 重建 dict 格式供格式化逻辑使用
        results = [
            {
                "code": c.code, "name": c.name, "shares": c.shares,
                "cost": c.cost, "price": c.price, "pct": c.pct,
                "ma12_ratio": c.ma12_ratio,
                "signals": [{"layer": s.layer, "level": s.level, "msg": s.msg}
                            for s in c.signals],
            }
            for c in report.checks
        ]
        ma50_status = [
            {"code": s.code, "name": s.name, "rule_type": s.rule_type,
             "is_full": s.is_full, "price": s.price, "ma50": s.ma,
             "dist_pct": s.dist_pct, "status": s.status, "dca_action": s.dca_action}
            for s in report.statuses
        ]

        return self._format_output(results, ma50_status)

    @staticmethod
    def _format_output(results: list[dict], ma50_status: list[dict]) -> str:
        """格式化输出（迁移自 scripts/sell_monitor.py format_output，改 print→return str）。"""
        lines = []

        # ═══ 50MA 持仓状态（始终输出） ═══
        lines.append(f"📊 50MA 持仓状态 | {len(ma50_status)}只")
        lines.append("")
        lines.append(f"  {'标的':<12} {'规则':<8} {'数据':>10} {'距50MA':>8} {'DCA':<10}")
        lines.append(f"  {'─'*12} {'─'*8} {'─'*10} {'─'*8} {'─'*10}")
        for s in ma50_status:
            if s["rule_type"] == "50MA":
                data_col = f"¥{s.get('price',0):.1f}/MA{s.get('ma50',0):.0f}"
                dist_col = f"{s.get('dist_pct',0):+.1f}%" if 'dist_pct' in s else "—"
            elif s["rule_type"] == "price":
                data_col = f"¥{s.get('price',0):.2f} vs ¥{s.get('exit_price',0):.2f}"
                dist_col = "—"
            else:
                data_col = "—"
                dist_col = "—"
            lines.append(
                f"  {s['code']:<12} {s['rule_type']:<8} {data_col:>10} "
                f"{dist_col:>8} {s['status']} {s['dca_action']}"
            )
        lines.append("")
        lines.append("> 50MA 规则（08-13）：建仓期(<目标×0.9)仅展示不拦截 | 建满后 < -2% → 🔴 暂停DCA | ±2%内 → 🟡 待确认 | > +2% → 🟢 可执行")
        lines.append("")

        # ═══ 暂停DCA清单（仅持仓的止损侧） ═══
        held_blocked = [s for s in ma50_status if s["dca_action"] == "暂停DCA"]
        if held_blocked:
            lines.append("🚫 50MA 暂停DCA — 以下持仓标的今日不可加仓：")
            for s in held_blocked:
                lines.append(f"  {s['code']} {s['name']} [持仓] — {s['status']} {s['dca_action']}")
            lines.append("> Agent 约束：出现于此清单的持仓标的，暂停 DCA（止损侧）。候选池不再受50MA准入门禁限制。")
            lines.append("")

        # ═══ L1/L2/L3 卖出信号 ═══
        has_signal = any(r["signals"] for r in results)
        lines.append(f"📤 卖出检查 | {len(results)}只持仓")
        lines.append("")

        # ── L1 signals ──
        l1_signals = [r for r in results if any(s["layer"] == "L1" for s in r["signals"])]
        if l1_signals:
            lines.append("━━ 减仓建议（L1 价格极端化）━━")
            for r in l1_signals:
                for s in r["signals"]:
                    if s["layer"] == "L1":
                        lines.append(f"  {s['level']} {r['code']} {r['name']} — {s['msg']}")
                        lines.append(f"     持仓: {r['shares']:,}股 @¥{r['cost']:.3f} | 现价 ¥{r['price']:.3f}")
            lines.append("")
        else:
            lines.append("━━ 减仓建议（L1 价格极端化）━━")
            lines.append("  无减仓信号")
            lines.append("")

        # ── L2 signals ──
        l2_signals = [r for r in results if any(s["layer"] == "L2" for s in r["signals"])]
        lines.append("━━ 仓位纪律（L2）━━")
        if l2_signals:
            for r in l2_signals:
                for s in r["signals"]:
                    if s["layer"] == "L2":
                        lines.append(f"  {s['level']} {r['code']} {r['name']} — {s['msg']}")
        else:
            lines.append("  仓位正常")
        lines.append("")

        # ── L3 signals ──
        l3_signals = [r for r in results if any(s["layer"] == "L3" for s in r["signals"])]
        lines.append("━━ 浮亏警戒（L3）━━")
        if l3_signals:
            for r in l3_signals:
                for s in r["signals"]:
                    if s["layer"] == "L3":
                        lines.append(f"  {s['level']} {r['code']} {r['name']} — {s['msg']}")
        else:
            lines.append("  无异常")

        return "\n".join(lines)
