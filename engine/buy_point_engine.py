#!/usr/bin/env python3
"""
BuyPointEngine — 买入点判断引擎（顶层入口）
============================================

统一API，按市场+标的类型自动路由到对应方法论。

用法:
  python3 -m engine.buy_point_engine SPY          # 美股ETF → TrendETF
  python3 -m engine.buy_point_engine ADBE         # 美股价值 → ValueUS
  python3 -m engine.buy_point_engine HOOD         # 美股成长 → GrowthUS
  python3 -m engine.buy_point_engine 600519       # A股 → SniperAH
  python3 -m engine.buy_point_engine 09988        # 港股 → SniperAH

输出: JSON格式 BuyPointResult
"""

import os
import sys
import json
import argparse
from typing import Optional, Dict
from .methodologies.base import (
    BuyPointResult,
    Market,
    MethodologyType,
)


class BuyPointEngine:
    """
    买入点判断引擎 —— 自动路由到对应方法论。
    
    路由规则:
      - 6位数字 → A股 → SniperAH
      - 5位数字 → 港股 → SniperAH
      - 字母代码 → 美股 → 根据档案类型路由
        - ETF: SPY, QQQ → TrendETF
        - growth 类型档案 → GrowthUS
        - 默认 → ValueUS
    """
    
    # 美股ETF名单
    US_ETFS = {"SPY", "QQQ", "IWM", "DIA"}
    
    # 美股成长型名单（从quality_flags/growth标记推断）
    US_GROWTH_STOCKS = {"HOOD", "NVDA", "TSM"}  # 后续可从档案读取
    
    # 美股拐点/亏损型
    US_TURNAROUND_STOCKS = {"COIN", "CRWV", "BULL"}
    
    def __init__(self):
        self._methods = {}
    
    def _get_method(self, mtype: MethodologyType):
        """延迟加载方法论实例。"""
        if mtype not in self._methods:
            if mtype == MethodologyType.SNIPER_AH:
                from .methodologies.sniper_ah import SniperAHMethodology
                self._methods[mtype] = SniperAHMethodology()
            elif mtype == MethodologyType.TREND_ETF:
                from .methodologies.trend_etf import TrendETFMethodology
                self._methods[mtype] = TrendETFMethodology()
            elif mtype == MethodologyType.VALUE_US:
                from .methodologies.value_us import ValueUSMethodology
                self._methods[mtype] = ValueUSMethodology()
            elif mtype == MethodologyType.GROWTH_US:
                from .methodologies.growth_us import GrowthUSMethodology
                self._methods[mtype] = GrowthUSMethodology()
            elif mtype == MethodologyType.TURNAROUND_US:
                from .methodologies.turnaround_us import TurnaroundUSMethodology
                self._methods[mtype] = TurnaroundUSMethodology()
            else:
                raise ValueError(f"不支持的方法论: {mtype}")
        return self._methods[mtype]
    
    def analyze(self, symbol: str) -> BuyPointResult:
        """分析单个标的。"""
        mtype = self._route(symbol)
        method = self._get_method(mtype)
        return method.analyze(symbol)
    
    def analyze_batch(self, symbols: list[str]) -> list[BuyPointResult]:
        """批量分析。"""
        return [self.analyze(s) for s in symbols]
    
    def _route(self, symbol: str) -> MethodologyType:
        """自动路由：根据代码格式判断市场和方法论。"""
        s = symbol.upper()
        
        # A股: 6位纯数字
        if s.isdigit() and len(s) == 6:
            return MethodologyType.SNIPER_AH
        
        # 港股: 5位纯数字
        if s.isdigit() and len(s) == 5:
            return MethodologyType.SNIPER_AH
        
        # 美股ETF
        if s in self.US_ETFS:
            return MethodologyType.TREND_ETF
        
        # 美股成长型
        if s in self.US_GROWTH_STOCKS:
            return MethodologyType.GROWTH_US
        
        # 美股拐点型
        if s in self.US_TURNAROUND_STOCKS:
            return MethodologyType.TURNAROUND_US
        
        # 美股默认 → ValueUS
        return MethodologyType.VALUE_US
    
    def summary_text(self, result: BuyPointResult) -> str:
        """人类可读摘要。"""
        icon = "🎯" if result.in_range else ("👀" if result.confidence >= 4 else "⏳")
        lines = [
            f"{icon} {result.symbol} ({result.market.value}/{result.methodology.value})",
            f"  价格: ${result.current_price:.2f}" if result.current_price else "  价格: N/A",
            f"  估值: {'✅' if result.valuation_ok else '❌'} {result.valuation_detail}",
            f"  回撤: {'✅' if result.drawdown_ok else '❌'} {result.drawdown_detail}",
        ]
        if result.trend_detail:
            lines.append(f"  趋势: {'✅' if result.trend_ok else '❌'} {result.trend_detail}")
        lines.append(f"  信心: {result.confidence}/10 | 区间: {result.buy_zone}")
        lines.append(f"  → {result.recommendation}")
        if result.warnings:
            lines.append(f"  ⚠️ {'; '.join(result.warnings)}")
        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="BuyPointEngine — 买入点判断引擎")
    parser.add_argument("symbols", nargs="+", help="标的代码（如 SPY QQQ ADBE 600519）")
    parser.add_argument("--json", action="store_true", help="输出JSON格式")
    args = parser.parse_args()
    
    engine = BuyPointEngine()
    results = engine.analyze_batch(args.symbols)
    
    if args.json:
        output = []
        for r in results:
            d = {
                "symbol": r.symbol,
                "methodology": r.methodology.value,
                "in_range": r.in_range,
                "confidence": r.confidence,
                "price": r.current_price,
                "buy_zone": r.buy_zone,
                "valuation_ok": r.valuation_ok,
                "drawdown_ok": r.drawdown_ok,
                "trend_ok": r.trend_ok,
                "recommendation": r.recommendation,
                "warnings": r.warnings,
            }
            output.append(d)
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(engine.summary_text(r))
            print()


if __name__ == "__main__":
    main()
