"""
美股价值型个股方法论（ValueUSMethodology）
==========================================

核心逻辑: PE百分位 + 回撤深度 — A/H狙击的温和版。
- PE锚: PE回到自身历史P25（不是A/H那种极端P10）
- 回撤锚: >20%深度回撤
- 与A/H狙击的区别: 阈值更宽，不等"错杀"，等"合理偏便宜"

适用标的: ADBE, META, MSFT, CRM（成熟盈利型）
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


class ValueUSMethodology(BaseMethodology):
    """美股价值型 — PE百分位 + 回撤深度。"""
    
    TYPE = MethodologyType.VALUE_US
    LABEL = "美股价值型"
    
    # 阈值（比A/H狙击更宽）
    PE_PERCENTILE_BUY = 25    # PE历史百分位 P25
    DD_MIN = -20              # 至少回撤20%
    DD_IDEAL = -30            # 理想回撤30%+
    ATH_HOT_ZONE = 5          # 距ATH 5%内 = 太热
    
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
        if not price:
            result.warnings.append("实时价格不可用")
        result.current_price = price or 0
        
        # 3. PE判断 — 用档案中的PE锚
        pe_anchor = profile.get("pe_anchor", {})
        pe_range = pe_anchor.get("range", "")  # 如 "14.3-22x"
        pe_current = pe_anchor.get("current", {})
        pe_trailing = pe_current.get("pe_trailing")
        
        sniper = profile.get("sniper_range", {})
        pe_max = sniper.get("pe_max")
        dd_min = sniper.get("dd_min")
        
        # 档案存在但sniper_range不完整 → 推导默认阈值
        if pe_max is None and pe_range:
            pe_max = self._derive_pe_max_from_range(pe_range)
        if dd_min is None:
            dd_min = self.DD_MIN
        
        if pe_trailing and pe_max:
            result.valuation_ok = pe_trailing <= pe_max
            result.valuation_detail = f"PE {pe_trailing}x vs 锚≤{pe_max}x (历史{pe_range})"
        elif pe_trailing and pe_range:
            pe_max = self._derive_pe_max_from_range(pe_range)
            result.valuation_ok = pe_trailing <= pe_max
            result.valuation_detail = f"PE {pe_trailing}x vs 推导≤{pe_max}x (历史{pe_range})"
        elif pe_trailing:
            # 无PE范围参照 → 保守：默认不通过，标记需建锚
            result.valuation_ok = False
            result.valuation_detail = f"PE {pe_trailing}x — 无历史锚，请在档案补充pe_anchor.range"
        else:
            result.valuation_ok = False
            result.valuation_detail = f"PE锚: {pe_range or '待建'} [当前PE缺失]"
        
        # 4. 回撤判断（实时价 ÷ ATH 实时算，数据缺失降级到档案静态快照）
        dd_anchor = profile.get("drawdown_anchor", {})
        current_dd = self._calc_current_drawdown(dd_anchor, price)
        dd_min = sniper.get("dd_min")
        
        if current_dd is not None and dd_min is not None:
            result.drawdown_ok = current_dd <= dd_min
            result.drawdown_detail = f"回撤 {current_dd}% vs 锚≤{dd_min}%"
        elif current_dd is not None:
            result.drawdown_ok = current_dd <= self.DD_MIN
            result.drawdown_detail = f"回撤 {current_dd}% vs 通用≤{self.DD_MIN}%"
        else:
            result.drawdown_ok = False
            result.drawdown_detail = "回撤数据缺失 — 请在档案补充drawdown_anchor.current_dd_pct"
        
        # 5. 趋势（辅助）
        trend_score = 0
        if current_dd is not None:
            if current_dd < -self.ATH_HOT_ZONE:
                trend_score += 1  # 不在ATH热区
            if current_dd < self.DD_MIN:
                trend_score += 1  # 显著回撤
        
        result.trend_ok = trend_score >= 1
        result.trend_detail = f"距ATH {current_dd}%"
        
        # 6. 品质检查
        quality = profile.get("quality_flags", {})
        quality_issues = []
        if quality:
            gm = quality.get("gross_margin")
            if gm and gm < 0.4:
                quality_issues.append(f"毛利率偏低({gm:.0%})")
        
        # 7. 综合判断
        # Build buy_zone text safely
        pe_cond = f"PE<{pe_max}x" if pe_max else pe_range or "PE合理"
        dd_cond = f"DD<{dd_min}%" if dd_min else f"DD<{self.DD_MIN}%"
        buy_zone_default = f"{pe_cond} + {dd_cond}"
        
        if result.valuation_ok and result.drawdown_ok:
            result.in_range = True
            result.confidence = 7
            result.buy_zone = sniper.get("condition_price") or buy_zone_default
            result.recommendation = "✅ 价值区间，可考虑建仓。"
            result.rationale = f"PE进入历史底部区间+回撤显著。品质检查{'通过' if not quality_issues else ': ' + '; '.join(quality_issues)}。"
        elif result.valuation_ok and not result.drawdown_ok:
            result.in_range = False
            result.confidence = 4
            result.buy_zone = sniper.get("condition_price") or f"PE已到位，等回撤扩至{dd_min or self.DD_MIN}%"
            result.recommendation = "👀 PE便宜但回撤不够，设提醒等跌。"
            result.rationale = f"PE已进入锚区间，需回撤进一步扩大。"
        elif not result.valuation_ok and result.drawdown_ok:
            result.in_range = False
            result.confidence = 3
            result.buy_zone = sniper.get("condition_price") or f"回撤已到位，等PE降至{pe_max or '历史低位'}x"
            result.recommendation = "👀 回撤到位但PE偏贵，关注财报。"
            result.rationale = f"回撤已触及锚，PE需修复。"
        else:
            result.in_range = False
            result.confidence = 2
            result.buy_zone = sniper.get("condition_price") or buy_zone_default
            result.recommendation = "⏳ 双条件不满足，继续等待。"
            result.rationale = "PE+回撤均未进入舒适区。"
        
        if quality_issues:
            result.warnings.extend(quality_issues)
        
        debate = profile.get("debate_result", {})
        if debate:
            result.debug["debate_rating"] = debate.get("rating")
            result.debug["debate_date"] = debate.get("date")
        
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
    
    @staticmethod
    def _derive_pe_max_from_range(pe_range: str) -> float:
        """从PE范围文本推导上限阈值。'15-22x' → 用上沿作为阈值（range已定义便宜区间）。"""
        import re
        m = re.findall(r"(\d+(?:\.\d+)?)", pe_range)
        if len(m) >= 2:
            return float(m[1])  # 上沿 — range已定义的是底部区间，PE在区间内即合格
        if len(m) == 1:
            return float(m[0])
        return 999  # 解析失败，不拦截
