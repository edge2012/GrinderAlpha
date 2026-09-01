"""DebateConfigProvider — 辩论引擎配置提供者接口
=================================================

抽象配置的获取，让 `load_config()` 不直接依赖私有文件路径
（`~/.hermes/investment/portfolio_config.json` + `~/.hermes/.env`）。

对齐 ParamProvider / ProfileProvider 模式：

- DebateConfigProvider (ABC): load() -> DebateConfig
- PortfolioConfigProvider: 私有实现（读 portfolio_config.json 的 debate_engine 节 + .env 凭证）
- EnvConfigProvider: 公开实现（只读环境变量 + 默认值，不碰任何 ~/.hermes 路径）
- get_config_provider(): 工厂，私有文件存在→Portfolio，否则→Env

设计原则：
- ABC 不直接 import 私有路径逻辑（私有路径只在 PortfolioConfigProvider 内）
- load_config() 通过工厂获取 provider，测试可 monkeypatch 注入
- source_name 暴露"当前在用哪套配置源"，延续"静默空壳可观测"约定
"""

from __future__ import annotations

import abc
import os
from typing import Optional

from .config import DebateConfig


class DebateConfigProvider(abc.ABC):
    """辩论引擎配置提供者接口。"""

    source_name: str = "unknown"  # 人类可读的来源标识

    @abc.abstractmethod
    def load(self) -> DebateConfig:
        """加载并返回完整的 DebateConfig。"""
        ...


class PortfolioConfigProvider(DebateConfigProvider):
    """私有实现 — 读 portfolio_config.json 的 debate_engine 节 + .env 凭证。

    生产环境用。文件缺失/解析错误时优雅回退到默认值（best-effort），
    不会崩溃——所以公开库环境即使误用此实现也能跑，只是拿默认值。
    """

    source_name = "private_portfolio_config.json"

    def __init__(
        self,
        config_path: Optional[str] = None,
        env_path: Optional[str] = None,
    ):
        self._config_path = config_path or os.path.expanduser(
            "~/.hermes/investment/portfolio_config.json"
        )
        self._env_path = env_path or os.path.expanduser("~/.hermes/.env")

    @property
    def available(self) -> bool:
        """私有配置文件是否存在（用于工厂判定）。"""
        return os.path.exists(self._config_path)

    def load(self) -> DebateConfig:
        # ── 1. 加载 .env 凭证（best-effort，不存在跳过）──
        _load_env_file(self._env_path)

        config = DebateConfig()

        # ── 2. portfolio_config.json 的 debate_engine 节覆盖默认值 ──
        try:
            import json
            if os.path.exists(self._config_path):
                with open(self._config_path) as f:
                    portfolio = json.load(f)
                debate_cfg = portfolio.get("debate_engine", {})
                for key, value in debate_cfg.items():
                    if hasattr(config, key):
                        setattr(config, key, value)
        except Exception:
            pass  # 配置缺失/损坏时回退默认值，不崩溃

        # ── 3. 环境变量覆盖（优先级最高）──
        _apply_env_overrides(config)

        # ── 4. 展开路径 ──
        config.log_dir = os.path.expanduser(config.log_dir)

        return config


class EnvConfigProvider(DebateConfigProvider):
    """公开实现 — 只读环境变量 + 默认值，不碰任何 ~/.hermes 私有路径。

    公开库用户无私有配置文件时用此实现：LLM key/base_url/模型名
    全部走环境变量或代码显式构造的 DebateConfig 覆盖。
    """

    source_name = "env_vars(defaults)"

    def load(self) -> DebateConfig:
        config = DebateConfig()
        _apply_env_overrides(config)
        config.log_dir = os.path.expanduser(config.log_dir)
        return config


def _load_env_file(env_path: str) -> None:
    """Load an env file into os.environ if not already set.

    Only sets variables NOT already in os.environ — respects existing
    environment overrides. Idempotent and never crashes.
    """
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass  # best-effort; never crash on env loading


def _apply_env_overrides(config: DebateConfig) -> None:
    """Apply environment variable overrides to a DebateConfig."""
    env_overrides = {
        "HERMES_DEBATE_MODE": ("mode", str),
    }
    for env_var, (attr, coerce) in env_overrides.items():
        val = os.environ.get(env_var)
        if val is not None:
            setattr(config, attr, coerce(val))


_config_provider: Optional[DebateConfigProvider] = None


def get_config_provider() -> DebateConfigProvider:
    """工厂：私有配置文件存在→PortfolioConfigProvider，否则→EnvConfigProvider。

    测试可通过 monkeypatch.setattr(config_provider, '_config_provider', mock) 注入。
    """
    global _config_provider
    if _config_provider is None:
        pp = PortfolioConfigProvider()
        _config_provider = pp if pp.available else EnvConfigProvider()
    return _config_provider
