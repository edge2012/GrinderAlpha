"""
Pydantic state models for the debate engine.

These schemas define the structured representation of debate state,
enabling context compression and quality checks without raw-text parsing.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from dataclasses import dataclass, field

from pydantic import BaseModel, Field


# ── Rating types (aligned with TradingAgents 5-tier scale) ──

class Rating(str, Enum):
    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ── Single claim within a debate argument ──

class DebateClaim(BaseModel):
    """A single claim made by a debater, with evidence and strength."""
    claim: str = Field(description="The claim itself, one sentence")
    evidence: str = Field(description="Specific data or source backing this claim")
    evidence_type: str = Field(
        description="Type of evidence: financial/macro/technical/news/sentiment"
    )
    strength: str = Field(
        description="How strong the evidence is: strong/medium/weak"
    )


# ── Compressed debate round state ──

class DebateRoundSummary(BaseModel):
    """Compressed summary of one debate round, used for context management."""
    round_number: int
    bull_core_thesis: str = Field(description="Bull's one-sentence core argument this round")
    bear_core_thesis: str = Field(description="Bear's one-sentence core counter-argument")
    key_disagreements: list[str] = Field(
        description="Dimensions where Bull and Bear fundamentally disagree",
        max_length=5,
    )
    convergence_points: list[str] = Field(
        description="Points both sides acknowledge as true",
        max_length=5,
        default_factory=list,
    )
    bear_unique_risks: list[str] = Field(
        description="Risks Bear raised that Bull did not address",
        max_length=5,
        default_factory=list,
    )
    evidence_quality_bull: str = Field(description="Bull evidence quality: strong/medium/weak")
    evidence_quality_bear: str = Field(description="Bear evidence quality: strong/medium/weak")


# ── Full debate state (lives across rounds) ──

@dataclass
class ScenarioDebateState:
    """State for the Bull-vs-Bear scenario debate."""
    bull_history: list[str] = field(default_factory=list)
    bear_history: list[str] = field(default_factory=list)
    full_history: list[str] = field(default_factory=list)
    round_summaries: list[DebateRoundSummary] = field(default_factory=list)
    current_round: int = 0
    max_rounds: int = 2
    judge_decision: Optional[str] = None

    @property
    def is_complete(self) -> bool:
        return self.current_round >= self.max_rounds * 2  # bull+bear each = 2 per round


@dataclass
class RiskDebateState:
    """State for the Aggressive-Conservative-Neutral risk debate."""
    aggressive_history: list[str] = field(default_factory=list)
    conservative_history: list[str] = field(default_factory=list)
    neutral_history: list[str] = field(default_factory=list)
    full_history: list[str] = field(default_factory=list)
    round_summaries: list[DebateRoundSummary] = field(default_factory=list)
    latest_speaker: str = ""
    current_round: int = 0
    max_rounds: int = 1
    judge_decision: Optional[str] = None

    @property
    def is_complete(self) -> bool:
        return self.current_round >= self.max_rounds * 3  # 3 speakers per round


# ── Input to the debate engine ──

@dataclass
class AnalysisInput:
    """Standardized input that the debate engine consumes.

    Deliberately flat — no dependency on existing analysis modules.
    The caller is responsible for formatting data into this shape.
    """
    ticker: str
    company_name: str
    trade_date: str
    asset_type: str = "stock"  # "stock" or "crypto"

    # Analyst-equivalent reports (produced by existing pipeline)
    market_report: str = ""       # Technical / price data
    sentiment_report: str = ""    # Social / news sentiment
    news_report: str = ""         # Macro / global news
    fundamentals_report: str = "" # Financial statements

    # A-share / HK specific reports (Phase 3 — lightweight)
    smart_money_report: str = ""  # 主力资金流向 + 龙虎榜
    macro_report: str = ""        # 板块资金 + 政策面
    volume_price_report: str = "" # 量价关系 + 涨停板情绪

    # Market hint: "us" / "a_share" / "hk" → triggers language variant
    market: str = "us"

    # Optional: single-view analysis from existing pipeline (for comparison)
    baseline_analysis: str = ""


# ── Structured output for Portfolio Manager ──

class PortfolioManagerDecision(BaseModel):
    """Structured output from the Portfolio Manager — the final decision.

    Mirrors TradingAgents' PortfolioDecision Pydantic model. Used with
    structured output (native JSON mode) for consistent, machine-parseable
    decisions across runs.
    """
    rating: Rating = Field(description="Final position rating: Buy/Overweight/Hold/Underweight/Sell")
    executive_summary: str = Field(
        description="2-4 sentence action plan covering entry strategy, "
        "position sizing, key risk levels, and time horizon."
    )
    investment_thesis: str = Field(
        description="Detailed reasoning anchored in specific evidence from "
        "the analysts' debate. Include which side's evidence was stronger "
        "on each key dimension."
    )
    price_target: Optional[float] = Field(
        default=None, description="Target price in the instrument's quote currency."
    )
    time_horizon: Optional[str] = Field(
        default=None, description="Recommended holding period, e.g. '3-6 months'."
    )
    key_risks: list[str] = Field(
        default_factory=list,
        description="Top 2-4 risks that could invalidate this thesis."
    )
    confidence: Confidence = Field(
        default=Confidence.MEDIUM,
        description="Confidence level: high/medium/low based on evidence quality."
    )


# ── Output from the debate engine ──

@dataclass
class DebateResult:
    """Complete output of one debate run."""
    # Required fields (no defaults)
    ticker: str
    trade_date: str
    rating: Rating
    executive_summary: str
    investment_thesis: str
    scenario_debate: ScenarioDebateState
    risk_debate: RiskDebateState

    # Optional fields (with defaults) — MUST be after required fields
    price_target: Optional[float] = None
    time_horizon: Optional[str] = None
    key_risks: list = field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM
    quality_report: dict = field(default_factory=dict)
    baseline_divergence: Optional[str] = None  # "same" / "different_direction" / "different_nuance"

    # Degradation flag: if True, the debate degraded and this result should
    # be treated with caution (single-view fallback weight)
    degraded: bool = False
    degradation_reason: str = ""
