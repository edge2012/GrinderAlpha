"""
美股拐点型个股方法论（TurnaroundUSMethodology）
==============================================

核心逻辑: 赌基本面拐点 — 亏损公司在收入转正/亏损收窄时出现机会。
- PE无意义（亏损），用收入趋势+现金消耗+深度回撤
- 此法风险最高，信心分默认偏低

适用标的: COIN, CRWV, BULL（亏损或周期底部）

⚠️ 当前为骨架 — 完整实现需回测验证拐点信号有效性。
   投资宪法要求：新方法论必须先回测再应用。
"""

import os
import sys
import urllib.request
import json
from typing import Optional
from methodologies.base import (
    BaseMethodology,
    BuyPointResult,
    Market,
    MethodologyType,
)


class TurnaroundUSMethodology(BaseMethodology):
    """美股拐点型 — 收入拐点 + 深度回撤 + 现金安全。"""
    
    TYPE = MethodologyType.TURNAROUND_US
    LABEL = "美股拐点型"
    
    # 阈值（待回测验证）
    DD_MIN = -50             # 深度回撤50%+ — 亏损公司不深不碰
    DD_IDEAL = -65           # 理想回撤65%+
    REVENUE_GROWTH_MIN = 5   # 收入至少不再加速下滑
    CASH_BURN_MONTHS = 24    # 现金至少够烧24月
    
    def analyze(self, symbol: str) -> BuyPointResult:
        symbol = symbol.upper()
        result = self._empty_result(symbol, Market.US)
        
        # 1. 底部档案
        profile = self._load_profile(symbol)
        if not profile:
            result.recommendation = (
                "⚠️ 无底部档案。拐点型分析需要: "
                "收入趋势(近4Q)、现金余额、现金消耗率、重大回撤记录。"
            )
            result.warnings.append("缺少底部档案")
            result.warnings.append("方法论状态: 骨架 — 待回测验证")
            return result
        
        # 2. 实时价格
        price = self._get_realtime_price(symbol)
        result.current_price = price or 0
        
        # 3. 收入趋势（档案中应有 revenue_trend 或 quality_flags）
        quality = profile.get("quality_flags", {})
        revenue_growth = quality.get("revenue_growth")
        
        if revenue_growth is not None and revenue_growth > 0:
            result.valuation_ok = True
            result.valuation_detail = (
                f"收入增速 {revenue_growth:.0%} > 0，可能已过拐点"
            )
        elif revenue_growth is not None:
            result.valuation_ok = False
            result.valuation_detail = (
                f"收入增速 {revenue_growth:.0%} < 0，仍在收缩"
            )
        else:
            result.valuation_ok = True  # 无数据不拦截
            result.valuation_detail = (
                "收入增速无数据 — 请在档案补充 quality_flags.revenue_growth"
            )
        
        # 4. 回撤（实时价 ÷ ATH 实时算，数据缺失降级到档案静态快照）
        dd_anchor = profile.get("drawdown_anchor", {})
        current_dd = self._calc_current_drawdown(dd_anchor, price)
        
        if current_dd is not None:
            result.drawdown_ok = current_dd <= self.DD_MIN
            result.drawdown_detail = (
                f"回撤 {current_dd:.1f}% vs 拐点线≤{self.DD_MIN}%"
            )
        else:
            result.drawdown_ok = True
            result.drawdown_detail = "回撤数据缺失"
        
        # 5. 现金安全性
        fcf = quality.get("fcf")
        cash_warning = None
        if fcf and "B" in str(fcf):
            # 简单判断: 有正向FCF或足够现金
            pass
        else:
            cash_warning = "现金/消耗数据缺失 — 拐点型核心风险是破产"
        
        # 6. 综合
        if result.valuation_ok and result.drawdown_ok and not cash_warning:
            result.in_range = True
            result.confidence = 5  # 拐点型信心分偏低 — 高度不确定
            result.buy_zone = f"回撤>{abs(self.DD_MIN)}% + 收入转正"
            result.recommendation = (
                "⚠️ 拐点信号触发，但此方法论待回测验证。"
                "建议极小仓位(<3%)或仅观察。"
            )
            result.rationale = (
                "收入可能已过拐点+深度回撤。"
                "注意：亏损公司有破产风险，此方法论尚未经过回测验证。"
            )
        else:
            result.in_range = False
            result.confidence = 1 if cash_warning else 2
            result.buy_zone = f"等收入转正 + 回撤>{abs(self.DD_MIN)}%"
            result.recommendation = (
                "⛔ 拐点条件不满足。亏损公司不建议左侧建仓。"
            )
            result.rationale = "等基本面拐点确认后再评估。"
        
        result.warnings.append(
            "⚠️ 拐点型方法论为骨架实现 — 阈值待回测，信号不可直接用于交易"
        )
        if cash_warning:
            result.warnings.append(cash_warning)
        
        result.debug["methodology_status"] = "stub — 待回测"
        return result
    
    def _get_realtime_price(self, symbol: str) -> Optional[float]:
        try:
            url = f"http://qt.gtimg.cn/q=us{symbol}"
            raw = urllib.request.urlopen(url, timeout=10).read().decode("gbk")
            for line in raw.strip().split("\n"):
                if '="' not in line:
                    continue
                data = line[line.index('="') + 2:].rstrip('";\n\r')
                parts = data.split("~")
                if len(parts) > 3 and parts[3]:
                    return float(parts[3])
        except Exception:
            pass
        return None
