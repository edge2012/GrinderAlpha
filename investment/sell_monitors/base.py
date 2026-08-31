#!/usr/bin/env python3
"""SellMonitor 抽象层 — 通用卖出监控 + DCA 止损控制。

对齐主机流程：scheduler 对每账户同时跑「建仓扫描(ScanEngine) + 卖出监控(SellMonitor)」，
两者的信号汇入 PortfolioEngine 出最终结论。

统一语义（A/B/C 三账户同一套，均线周期由 config 差异）：
- 跌深侧（暂停DCA）：跌破均线 → 暂停DCA不清仓（别下车）
    A/H: 50MA（Account B 08-13 战略回测最优）
    US : 200MA（Account C 2026-08-23 回测：200MA 净值最高且闪崩误杀最轻）
- 涨高侧（止盈减仓）：MA12 高位 → 减半/减25%（Account A 已有逻辑）
- 建仓期：只展示不拦截 DCA（回测证伪「建仓期准入门禁」）
- 静默规则：全无信号 → [SELL_SILENT]

⚠️ 边界（2026-08-23 确认）：暂不实现「升级保护本金」——用户指出此概念之前没有，
纯靠止损体系。先把主流程跑通，后续再优化。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class StopRule:
    """单个标的的止损规则。"""
    rule_type: str          # "50MA" | "200MA" | "price" | "none"
    param: Any              # 均线周期已含在 rule_type；price 类为退出价；none 为 None
    index_code: Optional[str] = None  # 用于拉 K 线的指数代码（A/H）或 None（US 直接标的价）


@dataclass
class HoldingStatus:
    """单标的持仓监控状态。"""
    code: str
    name: str
    rule_type: str
    is_full: bool
    price: Optional[float] = None
    ma: Optional[float] = None
    dist_pct: Optional[float] = None
    status: str = "⚠️"
    dca_action: str = "数据不足"


@dataclass
class HoldSignal:
    """单标的卖出信号（L1/L2/L3）。"""
    layer: str          # "L1" | "L2" | "L3"
    level: str          # "🔴" | "🟡" | "📋" | "🟢"
    msg: str


@dataclass
class HoldingCheck:
    """单标的全检查结果。"""
    code: str
    name: str
    shares: float
    cost: float
    price: float
    pct: float
    ma12_ratio: Optional[float]
    signals: list[HoldSignal] = field(default_factory=list)


@dataclass
class SellReport:
    """SellMonitor 扫描的完整输出。"""
    account_id: str
    statuses: list[HoldingStatus] = field(default_factory=list)   # DCA 暂停状态（所有持仓）
    checks: list[HoldingCheck] = field(default_factory=list)      # L1/L2/L3 卖出信号
    silent: bool = False                                          # True=无信号静默

    def to_dict(self) -> dict:
        """序列化为 dict（JSON 输出 / 跨层传递）。"""
        return {
            "account_id": self.account_id,
            "statuses": [s.__dict__ for s in self.statuses],
            "checks": [
                {
                    "code": c.code, "name": c.name, "shares": c.shares,
                    "cost": c.cost, "price": c.price, "pct": c.pct,
                    "ma12_ratio": c.ma12_ratio,
                    "signals": [s.__dict__ for s in c.signals],
                } for c in self.checks
            ],
            "silent": self.silent,
        }


# ═══════════════════════════════════════════════════════════════════
# PositionProvider — 持仓数据提供者接口
# ═══════════════════════════════════════════════════════════════════
# 策略/卖出监控通过此接口读持仓，不直接 import position_store。
# 私有实现 DBPositionProvider 包装 position_store（生产用）；
# 公开实现 DictPositionProvider 接受用户输入 dict（外部 agent 可用）。


class PositionProvider(abc.ABC):
    """持仓数据提供者接口 — 解耦卖出监控/策略与 position_store 硬依赖。

    market 参数：\"A\" = A股/港股通，\"US\" = 美股。
    """

    @abc.abstractmethod
    def get_holdings(self, market: str = "A") -> list[dict]:
        """返回持仓列表（Format.LIST 语义：list[dict]，每条含 code/name/shares/cost_basis/...）。"""
        ...

    @abc.abstractmethod
    def get_capital(self, market: str = "A") -> float:
        """返回指定市场的总资本 = 持仓成本 + 最新现金余额。"""
        ...


class DBPositionProvider(PositionProvider):
    """私有实现：包装 position_store（生产环境用）。

    延迟导入 position_store，避免 base.py 模块级硬依赖。
    """

    def __init__(self):
        import sys
        import os

        _repo = os.path.expanduser("~/.hermes/investment-os")
        if _repo not in sys.path:
            sys.path.insert(0, _repo)
        from position_store import get_holdings as _gh, Format, PositionStore

        self._get_holdings_fn = _gh
        self._Format = Format
        self._store = PositionStore()

    def get_holdings(self, market: str = "A") -> list[dict]:
        result = self._get_holdings_fn(self._Format.LIST, market)
        return list(result) if isinstance(result, list) else []

    def get_capital(self, market: str = "A") -> float:
        return self._store._get_capital_for_market(market)


class DictPositionProvider(PositionProvider):
    """公开实现：接受用户输入 dict（外部 agent 可用，不依赖 position_store/DB）。

    外部 agent 传入 holdings 列表（Format.LIST 语义的 list[dict]）和 capital 数值。
    market 参数被忽略（调用方自行确保数据归属正确市场）。
    """

    def __init__(self, holdings: list[dict] | None = None, capital: float = 0.0):
        self._holdings = holdings or []
        self._capital = capital

    def get_holdings(self, market: str = "A") -> list[dict]:
        return self._holdings

    def get_capital(self, market: str = "A") -> float:
        return self._capital


# ═══════════════════════════════════════════════════════════════════
# SellMonitor — 卖出监控抽象基类
# ═══════════════════════════════════════════════════════════════════


class SellMonitor(abc.ABC):
    """通用卖出监控抽象基类。

    子类实现（每账户一个）：
    - Account B: MeanReversionDCA — A/H 50MA + L1/L2/L3 + 止盈回补
    - Account C: TrendFollowing  — US 200MA 暂停 DCA
    - Account A: IndexDCA        — 止盈高位减仓（建仓不择时）
    """

    # 类属性：子类覆写
    account_id: str = ""

    # 桥接子类（如 MeanReversionSellMonitor）复用底层脚本模块的引用。
    # 声明为可选并标注 Any，供测试 mock 和类型系统识别。
    _sm: Any | None = None
    _aasm: Any | None = None
    # PositionProvider 注入（子类 __init__ 设置，生产用 DBPositionProvider）
    _positions: Any | None = None

    @abc.abstractmethod
    def scan(self, config: dict) -> SellReport:
        """扫描账户持仓 → 卖出信号 + DCA 暂停状态。config=该账户配置。"""
        raise NotImplementedError

    @abc.abstractmethod
    def format_output(self, report: SellReport) -> str:
        """将报告格式化为 Agent 可读文本（保留 [SELL_SILENT] 静默语义）。"""
        raise NotImplementedError
