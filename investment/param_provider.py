"""ParamProvider — 策略参数提供者接口
=====================================

抽象策略参数的获取，让消费者（引擎/构建器）不直接依赖
strategy_param_loader（私有，读 ~/.hermes/config/strategy_params.json）。

三部分：
- ParamProvider (ABC): 接口定义（section 访问 + key 访问 + 来源标识）
- JSONParamProvider: 私有实现，包装 strategy_param_loader（生产用）
- ExampleParamProvider: 公开实现，加载 strategy_params.example.json（全 0 占位，可跑非最优）
- get_param_provider(): 工厂，私有参数可用→JSON，否则→Example

设计原则（对齐 PositionProvider / DataAccess 抽象）：
- 接口 ABC 不 import strategy_param_loader（在 JSONParamProvider 内 lazy import）
- 消费者通过 get_param_provider() 获取，测试可 monkeypatch 注入
- source_name / is_example 暴露"当前在用哪套参数"，解决"静默空壳"可观测性
"""

from __future__ import annotations

import abc
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


class ParamProvider(abc.ABC):
    """策略参数提供者接口。"""

    source_name: str = "unknown"   # 人类可读的来源标识
    is_example: bool = False       # True=示例占位参数（非生产真实值）

    @abc.abstractmethod
    def get_section(self, name: str) -> Optional[Dict[str, Any]]:
        """返回指定配置段（如 "bottom_accelerator"），缺失返回 None。"""
        ...

    @abc.abstractmethod
    def get(self, section: str, key: str, default: Any = None) -> Any:
        """返回 section.key，缺失返回 default。"""
        ...


class JSONParamProvider(ParamProvider):
    """私有实现 — 包装 strategy_param_loader（读 ~/.hermes/config/strategy_params.json）。

    生产环境用。加载失败（文件缺失/解析错误/权限不足）时 available=False，
    工厂会回退到 ExampleParamProvider。
    """

    source_name = "private_strategy_params.json"
    is_example = False

    def __init__(self):
        self._params: Optional[Dict[str, Any]] = self._load()

    def _load(self) -> Optional[Dict[str, Any]]:
        try:
            _repo = os.path.expanduser("~/.hermes/investment-os")
            if _repo not in sys.path:
                sys.path.insert(0, _repo)
            from strategy_param_loader import get_params
            return get_params()
        except Exception:
            return None

    @property
    def available(self) -> bool:
        return self._params is not None

    def get_section(self, name: str) -> Optional[Dict[str, Any]]:
        if self._params is None:
            return None
        return self._params.get(name)

    def get(self, section: str, key: str, default: Any = None) -> Any:
        s = self.get_section(section)
        if s is None:
            return default
        return s.get(key, default)


class ExampleParamProvider(ParamProvider):
    """公开实现 — 加载 strategy_params.example.json（全 0 占位）。

    公开库用户无私有参数文件时用此实现：接口可跑、值全 0（非最优但不会崩）。
    is_example=True 供消费者明确提示"参数未配置，功能可能停用"。
    """

    source_name = "example_params(全0占位)"
    is_example = True

    def __init__(self, path: Optional[str] = None):
        self._params: Dict[str, Any] = self._load(path)

    def _load(self, path: Optional[str] = None) -> Dict[str, Any]:
        if path is None:
            # 默认：repo 根目录 strategy_params.example.json（公开库随包分发时同路径规则）
            path = str(Path(__file__).resolve().parent.parent / "strategy_params.example.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def get_section(self, name: str) -> Optional[Dict[str, Any]]:
        return self._params.get(name)

    def get(self, section: str, key: str, default: Any = None) -> Any:
        s = self.get_section(section)
        if s is None:
            return default
        return s.get(key, default)


_param_provider: Optional[ParamProvider] = None


def get_param_provider() -> ParamProvider:
    """工厂：私有参数可用→JSONParamProvider，否则→ExampleParamProvider。

    测试可通过 monkeypatch.setattr(param_provider, '_param_provider', mock) 注入。
    """
    global _param_provider
    if _param_provider is None:
        jp = JSONParamProvider()
        _param_provider = jp if jp.available else ExampleParamProvider()
    return _param_provider
