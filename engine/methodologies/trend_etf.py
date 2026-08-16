"""
美股指数ETF趋势跟踪方法论（TrendETFMethodology）
=================================================

核心逻辑: 趋势向上+估值不极端→在场。不等"极端便宜"。
- 趋势维度: 价格 vs 10月均线 + VIX区间
- 估值维度: 距ATH回撤（不等PE锚，指数PE自我修正）
- 三层温度: Cyclical/Momentum/Tactical

数据源: Alpha Vantage月K + 腾讯实时价格
"""

import os
import sys
import urllib.request
import json
import time
from typing import Optional, List, Tuple
from .base import (
    BaseMethodology,
    BuyPointResult,
    Market,
    MethodologyType,
)

AV_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")


class TrendETFMethodology(BaseMethodology):
    """美股指数ETF趋势跟踪 — SPY/QQQ 等宽基ETF。"""
    
    TYPE = MethodologyType.TREND_ETF
    LABEL = "美股ETF趋势"
    
    # 阈值（后续可外置到 strategy_params）
    MA_LOOKBACK = 10          # 10月均线
    PULLBACK_IDEAL = 8        # 理想回调 8-12%
    PULLBACK_MIN = 5          # 至少回调 5%
    ATH_HOT_ZONE = 3          # 距ATH 3%内 = 太热
    
    def analyze(self, symbol: str) -> BuyPointResult:
        symbol = symbol.upper()
        result = self._empty_result(symbol, Market.US)
        
        # 1. 月K数据
        prices = self._get_monthly_prices(symbol)
        if not prices:
            result.recommendation = "无法获取月K数据。"
            result.warnings.append("Alpha Vantage数据不可用")
            return result
        
        # 2. 实时价格
        current_px = self._get_realtime_price(symbol)
        if current_px:
            result.current_price = current_px
        
        # 3. 趋势判断
        ma10 = self._calc_ma(prices[-self.MA_LOOKBACK:])
        last_close = prices[-1] if prices else 0
        price = current_px or last_close
        
        above_ma = price > ma10
        deviation = (price / ma10 - 1) * 100
        
        result.trend_ok = above_ma
        result.trend_detail = (
            f"价格 ${price:.0f} {'>' if above_ma else '<'} 10MA ${ma10:.0f} "
            f"(偏离{deviation:+.1f}%)"
        )
        
        # 4. 估值维度 — 用距ATH回撤替代PE锚
        ath = max(prices)
        dd_from_ath = (1 - price / ath) * 100
        
        if dd_from_ath > -self.ATH_HOT_ZONE:
            result.valuation_ok = False
            result.valuation_detail = (
                f"距ATH仅{dd_from_ath:+.1f}%（<{self.ATH_HOT_ZONE}%），处于过热区间"
            )
        elif dd_from_ath < -self.PULLBACK_IDEAL:
            result.valuation_ok = True
            result.valuation_detail = (
                f"距ATH {dd_from_ath:+.1f}%，回调充分，估值舒适"
            )
        else:
            result.valuation_ok = False
            result.valuation_detail = (
                f"距ATH {dd_from_ath:+.1f}%，回调不足（理想>{self.PULLBACK_IDEAL}%）"
            )
        
        # 5. 回撤维度
        ath_idx = prices.index(ath)
        if ath_idx < len(prices) - 1:
            result.drawdown_ok = dd_from_ath < -self.PULLBACK_MIN
            result.drawdown_detail = f"距ATH {dd_from_ath:+.1f}%，{'满足' if result.drawdown_ok else '不足'}最低{self.PULLBACK_MIN}%回调要求"
        else:
            result.drawdown_ok = False
            result.drawdown_detail = "当前即ATH，无历史参照。"
        
        # 6. 三层温度
        cyclical = dd_from_ath
        months_up = self._count_up_months(prices)
        mom_change = ((prices[-1] / prices[-2] - 1) * 100) if len(prices) >= 2 else 0
        
        temp_issues = []
        if cyclical > -self.ATH_HOT_ZONE:
            temp_issues.append(f"Cyclical🔴: 距ATH {cyclical:+.1f}%")
        if months_up >= 4:
            temp_issues.append(f"Momentum🔴: 连涨{months_up}月")
        
        # 7. 综合判断
        buy_zone_low = price * 0.90
        buy_zone_high = price * 0.95
        
        if result.trend_ok and result.valuation_ok:
            result.in_range = True
            result.confidence = 7
            result.buy_zone = f"${buy_zone_low:.0f}-{buy_zone_high:.0f}（回调5-10%区间）"
            result.recommendation = "✅ 趋势向上+回调充分，可建仓。"
            result.rationale = (
                f"{symbol}趋势向上（价格>10MA），距ATH回调{abs(dd_from_ath):.1f}%提供安全边际。"
            )
        elif result.trend_ok and not result.valuation_ok:
            result.in_range = False
            result.confidence = 4
            result.buy_zone = f"${buy_zone_low:.0f}-{buy_zone_high:.0f}（等回调至10MA附近${ma10:.0f}）"
            result.recommendation = "⚠️ 趋势对但价格偏热，等回调。"
            result.rationale = (
                f"趋势向上确认，但{result.valuation_detail}。"
                f"建议等回调至${ma10:.0f}附近再建仓。"
            )
        else:
            result.in_range = False
            result.confidence = 2
            result.buy_zone = f"${(ath*0.80):.0f}以下（深度回调区）"
            result.recommendation = "⛔ 趋势转弱，暂不建仓。"
            result.rationale = f"价格跌破10MA，趋势信号转空。等待价格收复10MA。"
        
        # 风险
        if temp_issues:
            result.risks = temp_issues
        result.warnings = [f"前向12月收益: {self._calc_12m_fwd(prices):+.1f}%"]
        
        result.debug = {
            "ma10": round(ma10, 1),
            "ath": round(ath, 1),
            "dd_from_ath": round(dd_from_ath, 1),
            "months_up": months_up,
            "mom_change": round(mom_change, 1),
        }
        
        return result
    
    def _get_monthly_prices(self, symbol: str) -> List[float]:
        """从 Alpha Vantage 拉取月K收盘价。带重试+退避。"""
        if not AV_KEY:
            return []
        for attempt in range(3):
            try:
                url = (
                    f"https://www.alphavantage.co/query"
                    f"?function=TIME_SERIES_MONTHLY_ADJUSTED"
                    f"&symbol={symbol}&apikey={AV_KEY}"
                )
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                resp = urllib.request.urlopen(req, timeout=15).read().decode("utf-8")
                data = json.loads(resp)
                
                # AV限流返回的不是错误码而是 Note
                if "Note" in data:
                    wait = 3 * (attempt + 1)
                    time.sleep(wait)
                    continue
                
                ts = data.get("Monthly Adjusted Time Series", {})
                if not ts:
                    if attempt < 2:
                        time.sleep(2)
                        continue
                    return []
                
                dates = sorted(ts.keys())
                return [float(ts[d]["5. adjusted close"]) for d in dates]
            except Exception:
                if attempt < 2:
                    time.sleep(2)
                    continue
        return []
    
    def _get_realtime_price(self, symbol: str) -> Optional[float]:
        """腾讯API实时价格。"""
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
    def _calc_ma(prices: List[float]) -> float:
        return sum(prices) / len(prices) if prices else 0
    
    @staticmethod
    def _count_up_months(prices: List[float]) -> int:
        n = 0
        for i in range(len(prices) - 1, 0, -1):
            if prices[i] > prices[i - 1]:
                n += 1
            else:
                break
        return n
    
    def _calc_12m_fwd(self, prices: List[float]) -> float:
        if len(prices) < 13:
            return 0
        return (prices[-1] / prices[-13] - 1) * 100
