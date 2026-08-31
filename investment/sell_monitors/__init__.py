#!/usr/bin/env python3
"""SellMonitor 消费者注册表 — account_id → SellMonitor 实例。"""
from __future__ import annotations

from .base import (
    SellMonitor, SellReport, HoldingStatus, HoldSignal, HoldingCheck,
    PositionProvider, DBPositionProvider, DictPositionProvider,
)

# 延迟导入避免循环依赖。注册表在实例化时填充。
_REGISTRY: dict[str, type[SellMonitor]] = {}


def register(account_id: str):
    """类装饰器：注册 SellMonitor 子类到 account_id。"""
    def deco(cls):
        _REGISTRY[account_id] = cls
        cls.account_id = account_id
        return cls
    return deco


def get_sell_monitor(account_id: str, config: dict) -> SellMonitor | None:
    """按 account_id 返回 SellMonitor 实例。未注册返回 None(账户跳过)。"""
    cls = _REGISTRY.get(account_id)
    if cls is None:
        return None
    return cls(config)


def registered_accounts() -> list[str]:
    return sorted(_REGISTRY.keys())


# 导入具体实现以触发注册
from .mean_reversion import MeanReversionSellMonitor   # noqa: E402  (Account B)
from .trend_following import TrendFollowingSellMonitor  # noqa: E402  (Account C)
from .index_dca import IndexDCASellMonitor               # noqa: E402  (Account A)

__all__ = [
    "SellMonitor", "SellReport", "HoldingStatus", "HoldSignal", "HoldingCheck",
    "PositionProvider", "DBPositionProvider", "DictPositionProvider",
    "register", "get_sell_monitor", "registered_accounts",
]
