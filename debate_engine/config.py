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
    log_dir: str = "debate_logs"
    save_full_debate: bool = True         # Save full debate text for review


def load_config() -> DebateConfig:
    """Load debate engine configuration from portfolio_config.json + env.

    Priority: env vars > portfolio_config.json > defaults

    Auto-loads a .env file (path via DOTENV_PATH, default ".env") for
    API credentials so the engine works without manual `export`.
    """
    # ── Auto-load .env for API credentials ──
    _load_dotenv()

    config = DebateConfig()

    # Try loading from portfolio_config.json
    try:
        import json
        config_path = os.environ.get("PORTFOLIO_CONFIG", os.path.join(os.path.dirname(__file__), "..", "portfolio_config.json"))
        if os.path.exists(config_path):
            with open(config_path) as f:
                portfolio = json.load(f)
            
            debate_cfg = portfolio.get("debate_engine", {})
            if debate_cfg:
                for key, value in debate_cfg.items():
                    if hasattr(config, key):
                        setattr(config, key, value)
    except Exception:
        pass  # Use defaults if config file is missing or malformed

    # Environment variable overrides
    env_overrides = {
        "HERMES_DEBATE_MODE": ("mode", str),
    }
    for env_var, (attr, coerce) in env_overrides.items():
        val = os.environ.get(env_var)
        if val is not None:
            setattr(config, attr, coerce(val))

    # Expand paths
    config.log_dir = os.path.expanduser(config.log_dir)

    return config


def _load_dotenv() -> None:
    """Load a .env file into os.environ if not already set.

    Only sets variables that are NOT already in os.environ — respects
    existing environment overrides. This is the idempotent, safe pattern:
    if the user has explicitly set an env var, we don't clobber it.
    """
    env_path = os.environ.get("DOTENV_PATH", ".env")
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
        pass  # .env loading is best-effort; never crash on it


def update_portfolio_config(mode: str = None) -> None:
    """Update the debate_engine section in portfolio_config.json."""
    import json
    
    config_path = os.environ.get("PORTFOLIO_CONFIG", os.path.join(os.path.dirname(__file__), "..", "portfolio_config.json"))
    
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
