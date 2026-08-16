"""
美股成长型个股方法论（GrowthUSMethodology）
===========================================

核心逻辑: PEG + 收入增速 — PE锚无效（高增长公司永远"贵"）。
- PEG<1.0: 增长正在覆盖估值
- 回撤>30%: 成长股杀估值时才有机会
- 收入增速未崩塌: 基本面不能同步恶化

适用标的: HOOD, NVDA（高增长/扭亏为盈）
"""

import os
import sys
import urllib.request
import json
from typing import Optional
from .base import (
    BaseMethodology,
    BuyPointResult,
    Market,
    MethodologyType,
)


class GrowthUSMethodology(BaseMethodology):
    """美股成长型 — PEG + 收入增速 + 回撤。"""
    
    TYPE = MethodologyType.GROWTH_US
    LABEL = "美股成长型"
    
    # 阈值
    PEG_MAX = 1.0            # PEG < 1.0 = 增长覆盖估值
    DD_MIN = -30             # 至少回撤30%（成长股波动大）
    DD_IDEAL = -40           # 理想回撤40%+
    REVENUE_GROWTH_MIN = 10  # 收入增速至少10%
    
    def analyze(self, symbol: str) -> BuyPointResult:
        symbol = symbol.upper()
        result = self._empty_result(symbol, Market.US)
        
        # 1. 底部档案
        profile = self._load_profile(symbol)
        if not profile:
            result.recommendation = "无底部档案，需先建立。"
            result.warnings.append("缺少底部档案")
            return result
        
        # 2. 实时价格
        price = self._get_realtime_price(symbol)
        result.current_price = price or 0
        
        # 3. PEG / 增速判断
        quality = profile.get("quality_flags", {})
        eps_growth = quality.get("eps_growth")
        
        pe_anchor = profile.get("pe_anchor", {})
        pe_current = pe_anchor.get("current", {})
        pe_trailing = pe_current.get("pe_trailing")
        
        sniper = profile.get("sniper_range", {})
        pe_max = sniper.get("pe_max")
        dd_min = sniper.get("dd_min")
        
        # sniper_range不完整 → 推导默认阈值
        if dd_min is None:
            dd_min = self.DD_MIN
        
        # PEG计算
        peg = None
        if pe_trailing and eps_growth:
            peg = pe_trailing / (eps_growth * 100)
        
        if peg is not None and peg <= self.PEG_MAX:
            result.valuation_ok = True
            result.valuation_detail = f"PEG {peg:.2f} ≤ {self.PEG_MAX}（PE {pe_trailing}x / 增速 {eps_growth:.0%}）"
        elif pe_trailing and pe_max and pe_trailing <= pe_max:
            result.valuation_ok = True
            result.valuation_detail = f"PE {pe_trailing}x ≤ 档案锚 {pe_max}x（PEG无数据，退用PE锚）"
        elif peg is not None:
            result.valuation_ok = False
            result.valuation_detail = f"PEG {peg:.2f} > {self.PEG_MAX}（PE {pe_trailing}x / 增速 {eps_growth:.0%}），估值偏高"
        elif pe_trailing and pe_max:
            result.valuation_ok = False
            result.valuation_detail = f"PE {pe_trailing}x > 锚{pe_max}x（无PEG/增速数据）"
        else:
            # 无PE数据、无PEG数据、无增速数据 → 无法判断
            result.valuation_ok = False
            result.valuation_detail = (
                f"数据不足 — PE:{pe_trailing or '?'}x 增速:{eps_growth or '?'} "
                f"请在档案补充 quality_flags.eps_growth + pe_anchor"
            )
        
        # 4. 回撤判断（实时价 ÷ ATH 实时算，数据缺失降级到档案静态快照）
        dd_anchor = profile.get("drawdown_anchor", {})
        current_dd = self._calc_current_drawdown(dd_anchor, price)
        
        if current_dd is not None and dd_min is not None:
            result.drawdown_ok = current_dd <= dd_min
            result.drawdown_detail = f"回撤 {current_dd}% vs 锚≤{dd_min}%"
        elif current_dd is not None:
            result.drawdown_ok = current_dd <= self.DD_MIN
            result.drawdown_detail = f"回撤 {current_dd}% vs 通用≤{self.DD_MIN}%"
        else:
            result.drawdown_ok = False
            result.drawdown_detail = "回撤数据缺失 — 请在档案补充drawdown_anchor.current_dd_pct"
        
        # 5. 品质 — 成长股额外检查
        warnings = []
        gross_margin = quality.get("gross_margin")
        fcf = quality.get("fcf")
        
        if gross_margin and gross_margin < 0.5:
            warnings.append(f"毛利率偏低({gross_margin:.0%})，成长型需高毛利护城河")
        if not fcf:
            warnings.append("无FCF数据，需确认现金流健康度")
        
        # 6. 综合判断
        buy_zone = sniper.get("condition_price", "")
        buy_zone_detail = sniper.get("condition_pe", "")
        
        if result.valuation_ok and result.drawdown_ok:
            result.in_range = True
            result.confidence = 6  # 成长型信心分偏低——高不确定
            result.buy_zone = buy_zone or f"PEG<{self.PEG_MAX} + 回撤<{dd_min or -30}%"
            result.recommendation = "✅ 成长价值区间，注意仓位控制（高波动）。"
            result.rationale = f"PEG合理+回撤显著。成长型：高回报伴随高不确定，建议小仓位(<5%)。"
        elif result.valuation_ok and not result.drawdown_ok:
            result.in_range = False
            result.confidence = 3
            result.buy_zone = f"PEG已合理，等回撤扩至{dd_min or -30}%"
            result.recommendation = "👀 PEG合理但回撤不够，设提醒。"
            result.rationale = f"估值已进入舒适区，需回撤进一步扩大。"
        elif not result.valuation_ok and result.drawdown_ok:
            result.in_range = False
            result.confidence = 3
            result.buy_zone = f"回撤已到位，等PEG降至{self.PEG_MAX}"
            result.recommendation = "👀 回撤到位但PEG偏高，等增速兑现或价格回落。"
            result.rationale = f"回撤显著但估值仍偏高——等增速兑现（PE消化）或进一步下跌。"
        else:
            result.in_range = False
            result.confidence = 2
            result.buy_zone = f"{buy_zone_detail or f'PEG<{self.PEG_MAX}+回撤<{dd_min or -30}%'}"
            result.recommendation = "⏳ 双条件不满足，继续等待。"
            result.rationale = "PEG+回撤均未进入舒适区。成长型需耐心等深度回调。"
        
        result.warnings = warnings
        result.debug = {"peg": round(peg, 2) if peg else None, "eps_growth": eps_growth}
        
        debate = profile.get("debate_result", {})
        if debate:
            result.debug["debate_rating"] = debate.get("rating")
        
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
