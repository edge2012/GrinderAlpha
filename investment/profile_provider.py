"""
ProfileProvider — 底部档案访问抽象接口
========================================

抽象底部档案（bottom_profiles/*.json）的读取，让 sniper_ah 等消费者
不直接依赖 `~/.hermes/state/bottom_profiles` 文件系统路径。

设计目标（呼应 8/26 SP 系统化立项「缺档不哑火」）：
- 无档案时不再死路，返回「生成器初稿 → 审校 → 建档」完整引导路径
- 私有库注入全量档案；公开库可指向自带教学示例目录，逻辑同构

三部分：
- ProfileProvider (ABC): get_profile（核心读）+ get_guidance（缺档引导）
- FileProfileProvider: 文件实现，读 {data_dir}/{symbol}.json
- generate_draft / build_guidance: 模块级工具，公开库可直接复用

设计原则（对齐 DataAccess / ParamProvider 抽象）：
- 接口 ABC 不 import 具体文件系统实现
- 消费者通过构造参数注入，默认延迟创建 FileProfileProvider
- get_profile 失败返回 None 不抛异常（与 sniper_ah 既有语义一致）
"""

import abc
import json
import os
from typing import Optional

DEFAULT_PROFILE_DIR = os.environ.get(
    "BOTTOM_PROFILE_DIR",
    os.path.join(os.path.dirname(__file__), "..", "data", "bottom_profiles"),
)


class ProfileProvider(abc.ABC):
    """底部档案访问抽象接口。"""

    source_name: str = ""

    @abc.abstractmethod
    def get_profile(self, symbol: str) -> Optional[dict]:
        """加载底部档案 dict，无档案返回 None。"""
        ...

    @abc.abstractmethod
    def get_guidance(self, symbol: str) -> dict:
        """无档案时的建档引导。

        返回 {status, symbol, steps[], draft, target_dir}，
        steps 为「生成初稿 → 补数据 → 审校 → 建档」的完整路径。
        """
        ...


def generate_draft(symbol: str, name: str = "", market: str = "") -> dict:
    """A/H 个股底部档案骨架（初稿）。

    对齐真实档案 schema（pe_anchor / drawdown_anchor / forward_returns /
    sniper_range），占位待人工填充。A股 6 位代码、港股 5 位代码同一 schema。
    """
    return {
        "symbol": symbol,
        "name": name,
        "market": market,
        "mature_period": "待填充：成熟期起点（如 2018至今，此前 PE 锚参考价值低）",
        "pe_anchor": {
            "mature_range": "待填充（如 20x ~ 28x）",
            "bottoms": [],
            "trend": "待填充",
            "current": {"pe_trailing": None, "date": ""},
        },
        "drawdown_anchor": {
            "bottoms": [],
            "current": {"price": None, "ath": None, "ath_date": "", "dd_pct": None},
        },
        "forward_returns": {"from_bottoms": [], "avg_fwd_12m": None},
        "sniper_range": {
            "condition_pe": "待填充（如 PE < 22x）",
            "condition_dd": "待填充（如 回撤 > 35%）",
            "current_status": "待填充",
        },
        "last_updated": "",
        "data_source": "待填充（腾讯月K + 年报 EPS）",
        "methodology_note": "待填充",
    }


def build_guidance(symbol: str, target_dir: Optional[str] = None) -> dict:
    """无档案时的建档引导路径：生成初稿 → 补数据 → 审校 → 建档。"""
    steps = [
        "① 生成初稿：generate_draft(symbol, name, market) 产出档案骨架",
        "② 补 PE 历史底部：pe_anchor.bottoms（成熟期大底 PE，不复权价 ÷ 年度 EPS）",
        "③ 补回撤历史：drawdown_anchor.bottoms（峰值→谷底 dd_pct / recovery_months，后复权）",
        "④ 定义狙击线：sniper_range.condition_pe / condition_dd（好公司 + 极端便宜才开枪）",
        "⑤ 审校口径：PE 用不复权、回撤用后复权，避免混用（参考 examples/bottom_profiles/ 教学示例）",
        "⑥ 建档：写入 bottom_profiles/{symbol}.json，re-run analyze 即可进入狙击分析",
    ]
    return {
        "status": "missing",
        "symbol": symbol,
        "steps": steps,
        "draft": generate_draft(symbol),
        "target_dir": target_dir or DEFAULT_PROFILE_DIR,
    }


class FileProfileProvider(ProfileProvider):
    """文件实现 — 读 {data_dir}/{symbol}.json。

    私有库默认 data_dir = ~/.hermes/state/bottom_profiles（全量档案）。
    公开库可指向自带教学示例目录（examples/bottom_profiles/），逻辑同构。
    """

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or DEFAULT_PROFILE_DIR
        self.source_name = "file"

    def get_profile(self, symbol: str) -> Optional[dict]:
        path = os.path.join(self.data_dir, f"{symbol}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def get_guidance(self, symbol: str) -> dict:
        return build_guidance(symbol, self.data_dir)
