"""
Debate engine configuration.

All configuration is centralized here. The engine reads from
portfolio_config.json's "debate_engine" key, with defaults provided below.

Configuration can be overridden via environment variables:
  HERMES_DEBATE_ENABLED=true/false
  HERMES_DEBATE_MODE=shadow/active
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class DebateConfig:
    """Debate engine configuration with sensible defaults."""

    # ── Operational mode ──
    mode: str = "shadow"  # "shadow" = run both paths, use baseline for decisions
                          # "active" = use debate output as the actual decision

    # ── LLM configuration ──
    # Models must match what the provider actually supports.
    # DeepSeek: deepseek-v4-pro (reasoning) or deepseek-v4-flash (fast)
    deep_model: str = "deepseek-v4-pro"    # Judge nodes (Research Manager, Portfolio Manager)
    quick_model: str = "deepseek-v4-flash"  # Debater nodes
    compressor_model: str = "deepseek-v4-flash"  # Context compression

    # ── Debate rounds ──
    max_scenario_rounds: int = 2    # Bull vs Bear rounds (2 = 4 exchanges total)
    max_risk_rounds: int = 1        # Aggressive vs Conservative vs Neutral rounds

    # ── Context management ──
    max_context_tokens: int = 16000       # Hard cap on context window
    compression_enabled: bool = True      # Enable round compression
    judge_sees_full_history: bool = True  # Judge nodes get uncompressed history

    # ── Quality thresholds (override quality.py defaults) ──
    quality_scenario_divergence_min: float = 0.35
    quality_bear_citation_min: int = 2
    quality_blind_overlap_max: float = 0.60
    blind_round_probability: float = 0.33  # Probability of inserting a blind round

    # ── Degradation handling ──
    max_retry_on_degradation: int = 1     # Max re-runs if quality FAILS
    fallback_to_single_view: bool = True  # Fall back to single-view analysis on FAIL

    # ── Output language ──
    output_language: str = "English"      # Debate reasoning language
    # "English" = higher reasoning quality (recommended)
    # "Chinese" = if output must be in Chinese throughout

    # ── Logging ──
    log_dir: str = "./logs"
    save_full_debate: bool = True         # Save full debate text for review
    capture_backtest: bool = False        # 默认 False：只写 JSON，不碰 backtest DB（脱敏/公开版安全）。
                                          # 内部系统需写 DB（决策追溯）时，显式设 True——在 portfolio_config
                                          # 的 debate_engine.capture_backtest 置 true 即可。
                                          # 脱敏切片：删 engine.py 的 _capture_backtest_db 方法 + run() 调用块。

    # ── LLM 后端（OpenAI 兼容，公开库用户可替换）──
    llm_base_url: str = ""                # 空=自动检测环境变量(LLM_BASE_URL>DEEPSEEK_BASE_URL>OPENAI_BASE_URL)
                                          # 最后回退 https://api.deepseek.com/v1。公开库用户可设 OPENAI_BASE_URL
                                          # 指向自己的端点，或用自定义 DebateConfig 显式传 llm_base_url。


def load_config() -> DebateConfig:
    """Load debate engine configuration via DebateConfigProvider.

    Priority: env vars > portfolio_config.json > defaults.

    委托给 config_provider 工厂：私有配置文件存在时读 portfolio_config.json
    + .env 凭证；否则（公开库环境）只读环境变量 + 默认值，不碰私有路径。
    """
    from .config_provider import get_config_provider
    return get_config_provider().load()


def update_portfolio_config(mode: str = None) -> None:
    """Update the debate_engine section in portfolio_config.json.

    仅私有侧使用：写回 ~/.hermes/investment/portfolio_config.json 的
    debate_engine 节。公开库无此文件，无需调用。
    """
    import json

    config_path = os.path.expanduser("~/.hermes/investment/portfolio_config.json")

    if os.path.exists(config_path):
        with open(config_path) as f:
            portfolio = json.load(f)
    else:
        portfolio = {}

    if "debate_engine" not in portfolio:
        portfolio["debate_engine"] = {}

    if mode is not None:
        portfolio["debate_engine"]["mode"] = mode

    with open(config_path, "w") as f:
        json.dump(portfolio, f, indent=2, ensure_ascii=False)
