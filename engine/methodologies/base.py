"""
BuyPointEngine — 抽象基类 + 输出 Schema
========================================

所有方法论继承 BaseMethodology，实现 analyze() 返回 BuyPointResult。

设计约束:
1. 顶层只定义输出 schema，不定义怎么算
2. 底层方法论各自独立 — A/H 不改美股逻辑，美股不改A/H逻辑
3. 新增市场只需实现新 Methodology 子类，顶层不变
"""

import os
import json
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from enum import Enum
from abc import ABC, abstractmethod


class Market(str, Enum):
    A = "A"
    HK = "HK"
    US = "US"


class MethodologyType(str, Enum):
    SNIPER_AH = "sniper_ah"
    TREND_ETF = "trend_etf"
    VALUE_US = "value_us"
    GROWTH_US = "growth_us"
    TURNAROUND_US = "turnaround_us"


@dataclass
class BuyPointResult:
    """统一输出 Schema — 所有方法论返回此结构。"""
    symbol: str
    market: Market
    methodology: MethodologyType
    
    # 核心判断
    in_range: bool               # 是否在买入区间
    confidence: int              # 1-10，信心分
    
    # 当前状态
    current_price: float
    buy_zone: str                # 人可读买入区间，如 "$680-710（10MA附近）"
    
    # 估值维度
    valuation_ok: bool
    valuation_detail: str
    
    # 回撤维度
    drawdown_ok: bool
    drawdown_detail: str
    
    # 趋势维度（美股专用，A/H 可选）
    trend_ok: Optional[bool] = None
    trend_detail: Optional[str] = None
    
    # 风险
    risks: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    # 建议
    recommendation: str = ""
    rationale: str = ""
    
    # 调试
    debug: Dict = field(default_factory=dict)


class BaseMethodology(ABC):
    """
    买入点判断的抽象基类。
    
    每个子类实现 analyze()，负责:
    1. 拉取数据（行情/PE/回撤/趋势）
    2. 按自己的方法论判定
    3. 返回 BuyPointResult
    """
    
    TYPE: MethodologyType
    LABEL: str = ""
    
    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or os.path.expanduser("~/.hermes/state/bottom_profiles")
    
    @abstractmethod
    def analyze(self, symbol: str) -> BuyPointResult:
        """分析标的，返回 BuyPointResult。"""
        ...
    
    def _load_profile(self, symbol: str) -> Optional[dict]:
        """加载底部档案（A/H + US 通用）。"""
        path = os.path.join(self.data_dir, f"{symbol}.json")
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)

    def _calc_current_drawdown(self, drawdown_anchor: dict,
                               current_price: Optional[float]) -> Optional[float]:
        """实时计算当前回撤%（负值，-33.7 = 距ATH 33.7%）。

        优先用「实时价 ÷ ATH」实时计算——回撤锚与当前价同口径，价格变动后判定永远新鲜，
        不再依赖档案里的静态 current_dd_pct 快照。

        ath 兼容新旧 schema：drawdown_anchor.ath（新版）或 drawdown_anchor.current.ath（旧版 A/H）。
        降级路径（实时价或 ATH 缺失时）：静态快照 current_dd_pct（新版负值）或 current.dd_pct（旧版正值）。
        """
        ath = drawdown_anchor.get('ath') or drawdown_anchor.get('current', {}).get('ath')
        if current_price and ath:
            return round((current_price - ath) / ath * 100, 1)

        # 降级：静态快照（兼容新旧 schema，归一化为负值）
        val = drawdown_anchor.get('current_dd_pct')
        if val is None:
            val = drawdown_anchor.get('current', {}).get('dd_pct')
        if val is not None:
            val = float(val)
            return -abs(val) if val > 0 else val
        return None

    def _empty_result(self, symbol: str, market: Market) -> BuyPointResult:
        """生成空白结果骨架。"""
        return BuyPointResult(
            symbol=symbol,
            market=market,
            methodology=self.TYPE,
            in_range=False,
            confidence=0,
            current_price=0.0,
            buy_zone="待分析",
            valuation_ok=False,
            valuation_detail="未评估",
            drawdown_ok=False,
            drawdown_detail="未评估",
        )
