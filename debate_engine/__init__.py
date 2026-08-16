"""
Debate Engine — Multi-Agent Investment Debate Framework.

Architecture inspired by TradingAgents (TauricResearch), adapted for
the Hermes investment cognition system with three-layer defense against
debate degradation, asymmetric context compression, and zero-intrusion
rollback capability.

Usage:
    from debate_engine import DebateEngine, AnalysisInput, run_debate

    # Quick single analysis
    result = run_debate(AnalysisInput(
        ticker="0700.HK",
        company_name="Tencent",
        trade_date="2026-06-07",
        market_report="...",
        sentiment_report="...",
        news_report="...",
        fundamentals_report="...",
    ))

    # With config control
    engine = DebateEngine(config=custom_config)
    result = engine.run(input_data)

Modes:
    - "shadow": Run debate alongside baseline, store both for comparison
    - "active": Debate output replaces single-view analysis (after validation)
"""

from .config import DebateConfig, load_config, update_portfolio_config
from .state import (
    AnalysisInput, DebateResult, ScenarioDebateState, RiskDebateState,
    Rating, Confidence,
)
from .engine import DebateEngine, run_debate

__all__ = [
    "DebateEngine",
    "run_debate",
    "DebateConfig",
    "load_config",
    "update_portfolio_config",
    "AnalysisInput",
    "DebateResult",
    "ScenarioDebateState",
    "RiskDebateState",
    "Rating",
    "Confidence",
]
