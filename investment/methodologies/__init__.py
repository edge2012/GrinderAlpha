"""
BuyPointEngine — 方法论工厂
============================
统一顶层API，底层按市场/策略分叉。

方法论注册表:
  - SniperAH: A/H 狙击（PE锚+回撤锚，等极端便宜）
  - TrendETF: 美股指数ETF趋势跟踪（MA位置+VIX区间）
  - ValueUS: 美股价值型个股（PE百分位+回撤深度）
  - GrowthUS: 美股成长型个股（PEG+增速+回撤）

新增方法论: 继承 BaseMethodology，实现 analyze()，注册到 REGISTRY。
"""

from investment.methodologies.base import (
    BaseMethodology,
    BuyPointResult,
    Market,
    MethodologyType,
)

REGISTRY: dict[str, type] = {}

def register(method: type) -> type:
    """注册方法论到全局注册表。"""
    if not issubclass(method, BaseMethodology):
        raise TypeError(f"{method} must be a BaseMethodology subclass")
    REGISTRY[method.TYPE.value] = method
    return method

# 延迟导入避免循环依赖 — 在 BuyPointEngine.analyze() 中首次调用时加载
