"""
A/H 狙击方法论（SniperMethodology）
===================================

核心逻辑: 好公司+极端便宜→开枪。
- PE锚: 当前PE回到历史底部PE区间内
- 回撤锚: 回撤幅度触及历史极值
- 双条件满足→在射程

数据源: 底部档案（bottom_profiles/*.json）+ 腾讯API实时价格

Schema兼容: 同时支持新版档案（pe_trailing/current_dd_pct/pe_max/dd_min）
和旧版档案（pe_2024/current.dd_pct/condition_pe text）。
"""

import os
import re
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


class SniperAHMethodology(BaseMethodology):
    """A/H 狙击方法论 — PE锚 + 回撤锚 双确认。"""
    
    TYPE = MethodologyType.SNIPER_AH
    LABEL = "A/H 狙击"
    
    MARKET_PREFIX = {"A": "sh", "HK": "hk"}
    
    def analyze(self, symbol: str) -> BuyPointResult:
        market = self._detect_market(symbol)
        result = self._empty_result(symbol, market)
        
        profile = self._load_profile(symbol)
        if not profile:
            result.recommendation = "无底部档案，需先建立。"
            result.rationale = f"{symbol} 尚未建立底部档案，无法进行狙击分析。"
            result.warnings.append("缺少底部档案")
            return result
        
        price = self._get_price(symbol, market)
        if not price:
            result.recommendation = "无法获取实时价格。"
            result.warnings.append("价格数据不可用")
            return result
        result.current_price = price
        
        # ── 数据提取（兼容新旧schema）──
        pe_anchor = profile.get("pe_anchor", {})
        pe_range = pe_anchor.get("range", "") or pe_anchor.get("mature_range", "")
        pe_current = pe_anchor.get("current", {})
        pe_trailing = self._extract_pe(pe_current)
        
        sniper = profile.get("sniper_range", {})
        pe_max = self._extract_pe_max(sniper)
        pe_condition_text = sniper.get("condition_pe", "")
        
        dd_anchor = profile.get("drawdown_anchor", {})
        current_dd = self._calc_current_drawdown(dd_anchor, price)
        dd_min = self._extract_dd_min(sniper)
        dd_condition_text = sniper.get("condition_dd", "")
        
        # ── PE判断 ──
        if pe_trailing is not None and pe_max is not None:
            result.valuation_ok = pe_trailing <= pe_max
            result.valuation_detail = (
                f"PE {pe_trailing}x vs 狙击线≤{pe_max}x "
                f"(历史底部{pe_range})"
            )
        elif pe_trailing is not None and pe_range:
            result.valuation_ok = True
            result.valuation_detail = (
                f"PE {pe_trailing}x vs 历史底部{pe_range} "
                f"[无pe_max阈值，默认通过]"
            )
        elif pe_range:
            result.valuation_ok = True
            result.valuation_detail = (
                f"PE锚: {pe_range} "
                f"[无法获取当前PE，请更新档案pe_trailing字段]"
            )
        else:
            result.valuation_ok = True
            result.valuation_detail = "PE锚数据缺失，请在档案中补充pe_anchor"
        
        # ── 回撤判断 ──
        if current_dd is not None and dd_min is not None:
            result.drawdown_ok = current_dd <= dd_min
            result.drawdown_detail = (
                f"回撤 {current_dd:.1f}% vs 狙击线≤{dd_min}%"
            )
        elif current_dd is not None:
            result.drawdown_ok = True
            result.drawdown_detail = (
                f"回撤 {current_dd:.1f}% [无dd_min阈值，默认通过]"
            )
        else:
            result.drawdown_ok = True
            result.drawdown_detail = "回撤数据缺失，请在档案中补充drawdown_anchor.current_dd_pct"
        
        # ── 综合判断 ──
        buy_zone = (
            sniper.get("condition_price") or
            sniper.get("trigger_price_2025e") or
            sniper.get("trigger_price_2024") or
            sniper.get("trigger_price_fy2026e") or ""
        )
        if not buy_zone:
            parts = []
            if pe_condition_text:
                parts.append(pe_condition_text)
            if dd_condition_text:
                parts.append(dd_condition_text)
            buy_zone = " + ".join(parts) if parts else "见档案详情"
        
        if result.valuation_ok and result.drawdown_ok:
            result.in_range = True
            result.confidence = 8
            result.buy_zone = buy_zone
            result.recommendation = "🎯 在狙击区间，建议论证后开枪。"
            pe_part = (
                f"PE {pe_trailing}x≤{pe_max}x"
                if (pe_trailing is not None and pe_max is not None)
                else "PE满足"
            )
            dd_part = (
                f"回撤 {current_dd:.1f}%≤{dd_min}%"
                if (current_dd is not None and dd_min is not None)
                else "回撤满足"
            )
            result.rationale = (
                f"{pe_part} 且 {dd_part}，双满足。"
                "建议跑辩论引擎确认非价值陷阱。"
            )
        elif result.valuation_ok and not result.drawdown_ok:
            result.in_range = False
            result.confidence = 4
            result.buy_zone = buy_zone or f"等回撤扩至{dd_min or '更深'}%"
            result.recommendation = "👀 PE到位但回撤不够，等更好价格。"
            result.rationale = (
                f"PE已进入锚区间，回撤需进一步扩大"
                f"{f'至{dd_min}%' if dd_min else ''}。"
            )
        elif not result.valuation_ok and result.drawdown_ok:
            result.in_range = False
            result.confidence = 3
            result.buy_zone = buy_zone or f"等PE降至{pe_max or '更低'}x"
            result.recommendation = "👀 回撤到位但PE偏高，关注财报后重估。"
            result.rationale = (
                f"回撤已触及锚，PE需降至"
                f"{f'{pe_max}x' if pe_max else '历史低位'}以下。"
            )
        else:
            result.in_range = False
            result.confidence = 2
            result.buy_zone = buy_zone or "等待双条件"
            result.recommendation = "⏳ 不在射程，继续等待。"
            pe_info = f"{pe_trailing}x>{pe_max}x" if pe_trailing and pe_max else "?"
            dd_info = f"{current_dd:.0f}%>{dd_min}%" if current_dd and dd_min else "?"
            result.rationale = f"PE({pe_info}) + 回撤({dd_info})，双条件均不满足。"
        
        # ── 辩论状态 ──
        debate = profile.get("debate_result", {})
        if debate:
            result.debug["debate_rating"] = debate.get("rating")
            result.debug["debate_date"] = debate.get("date")
            if result.in_range:
                debate_note = debate.get("note", "")
                result.warnings.append(
                    f"辩论结论: {debate.get('rating', '?')}"
                    + (f" — {debate_note}" if debate_note else "")
                )
        
        return result
    
    # ═══════════════════════════════════════════════════════════════
    # Schema兼容层 — 新旧档案字段映射
    # ═══════════════════════════════════════════════════════════════
    
    @staticmethod
    def _extract_pe(pe_current: dict) -> Optional[float]:
        """提取当前PE — 兼容 pe_trailing / pe_2024 / pe_2025e / pe_fy2025e / pe_fy2026e."""
        for key in ("pe_trailing", "pe_2024", "pe_2025e", "pe_fy2025e", "pe_fy2026e"):
            val = pe_current.get(key)
            if val is not None:
                return float(val)
        return None
    
    @staticmethod
    def _extract_pe_max(sniper: dict) -> Optional[float]:
        """提取PE上限 — 兼容 pe_max 字段 / condition_pe 文本解析."""
        val = sniper.get("pe_max")
        if val is not None:
            return float(val)
        # 从 condition_pe 文本解析: "PE < 22x" → 22
        text = sniper.get("condition_pe", "")
        m = re.search(r"PE\s*[<≤]\s*(\d+(?:\.\d+)?)\s*x", text, re.IGNORECASE)
        if m:
            return float(m.group(1))
        return None
    
    @staticmethod
    def _extract_dd_min(sniper: dict) -> Optional[float]:
        """提取回撤下限 — 兼容 dd_min 字段 / condition_dd 文本解析."""
        val = sniper.get("dd_min")
        if val is not None:
            return float(val)
        # 从 condition_dd 文本解析: "回撤 > 35%" → -35
        text = sniper.get("condition_dd", "")
        m = re.search(r"[回撤]*\s*[>＞]\s*(\d+(?:\.\d+)?)\s*%", text)
        if m:
            return -float(m.group(1))
        return None
    
    # ═══════════════════════════════════════════════════════════════
    
    def _detect_market(self, symbol: str) -> Market:
        s = symbol.upper()
        if s.isdigit() and len(s) == 6:
            return Market.A
        if s.isdigit() and len(s) == 5:
            return Market.HK
        return Market.HK
    
    def _get_price(self, symbol: str, market: Market) -> Optional[float]:
        try:
            prefix = self.MARKET_PREFIX.get(market.value, "hk")
            url = f"http://qt.gtimg.cn/q={prefix}{symbol}"
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
