"""
Core debate orchestration engine.

Pipelines:
  shadow mode: runs debate alongside baseline, stores both for comparison
  active mode: debate output replaces single-view analysis

Flow:
  AnalysisInput → Scenario Debate (Bull vs Bear) → Scenario Judge
                → Trader (simulated) → Risk Debate (Agg/Con/Neu)
                → Portfolio Manager → DebateResult

The engine is deliberately self-contained — it does NOT import any 
existing analysis modules. It receives AnalysisInput (data already 
formatted by the caller) and returns DebateResult.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from .config import DebateConfig, load_config
from .state import (
    AnalysisInput, DebateResult, ScenarioDebateState, RiskDebateState,
    Rating, Confidence,
)
from .prompts import (
    build_bull_prompt, build_bear_prompt,
    build_research_manager_prompt,
    build_aggressive_prompt, build_conservative_prompt, build_neutral_prompt,
    build_portfolio_manager_prompt,
)
from .compressor import (
    compress_round, compress_risk_round,
    build_compressed_history, build_full_history_for_judge,
)
from .quality import (
    check_scenario_debate, generate_quality_report,
    check_blind_round,
)


class DebateEngine:
    """Orchestrates multi-agent investment debate.

    Usage:
        engine = DebateEngine()
        result = engine.run(input_data)

    In shadow mode, also call:
        engine.compare_with_baseline(result, baseline_analysis)
    """

    def __init__(self, config: Optional[DebateConfig] = None):
        self.config = config or load_config()
        self._init_llms()

    def _init_llms(self):
        """Initialize LLM clients (OpenAI-compatible, provider-agnostic).

        LLM 后端通过环境变量 / config.llm_base_url 配置，可替换为任意
        OpenAI 兼容端点（DeepSeek / OpenAI / 本地 vLLM 等）。不依赖任何
        ~/.hermes 私有路径或内部基础设施。
        """
        try:
            self.deep_llm = self._create_llm_client(self.config.deep_model)
            self.quick_llm = self._create_llm_client(self.config.quick_model)
            self.compressor_llm = self._create_llm_client(self.config.compressor_model)
        except Exception as e:
            raise RuntimeError(
                f"Failed to initialize LLM clients for debate engine: {e}\n"
                "Ensure langchain_openai is installed and an LLM API key "
                "is set (LLM_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY)."
            )

    def _create_llm_client(self, model: str):
        """Create an OpenAI-compatible LLM client (provider-agnostic).

        api_key 优先级: LLM_API_KEY > DEEPSEEK_API_KEY > OPENAI_API_KEY
        base_url 优先级: config.llm_base_url > LLM_BASE_URL > DEEPSEEK_BASE_URL
                         > OPENAI_BASE_URL > 默认 DeepSeek

        公开库用户设 OPENAI_API_KEY + OPENAI_BASE_URL（或 config.llm_base_url）
        即可指向自己的端点；私有侧默认行为（DeepSeek）零变化。
        """
        from langchain_openai import ChatOpenAI

        api_key = (
            os.environ.get("LLM_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
        base_url = (
            self.config.llm_base_url
            or os.environ.get("LLM_BASE_URL")
            or os.environ.get("DEEPSEEK_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.deepseek.com/v1"
        )

        return ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=0.3,  # Low but non-zero for debate diversity
            max_tokens=2048,
        )

    # ── Public API ──

    def _build_context(self, input_data: AnalysisInput) -> tuple[str, str, bool]:
        """Build instrument context and detect language variant.

        Returns (ctx_string, language, use_zh).
        """
        ctx = f"Company: {input_data.company_name} ({input_data.ticker})"

        # A-share supplementary context
        a_share_extra = ""
        if input_data.smart_money_report:
            a_share_extra += f"\n\n【主力资金动向】\n{input_data.smart_money_report}"
        if input_data.macro_report:
            a_share_extra += f"\n\n【宏观与板块环境】\n{input_data.macro_report}"
        if input_data.volume_price_report:
            a_share_extra += f"\n\n【量价与情绪】\n{input_data.volume_price_report}"
        if a_share_extra:
            ctx += a_share_extra

        from .zh_prompts import should_use_zh_prompts
        use_zh = should_use_zh_prompts(input_data.market, self.config.output_language)
        language = "Chinese" if use_zh else self.config.output_language

        return ctx, language, use_zh

    def run(self, input_data: AnalysisInput) -> DebateResult:
        """Run the complete debate pipeline.

        Args:
            input_data: Standardized analysis input with reports

        Returns:
            DebateResult with final decision and full debate state
        """
        # ── Phase 1: Scenario Debate (Bull vs Bear) ──
        scenario_state = self._run_scenario_debate(input_data)

        # ── Phase 2: Scenario Judge (Research Manager) ──
        research_plan = self._run_scenario_judge(input_data, scenario_state)

        # ── Phase 3: Simulated Trader ──
        trader_plan = self._run_trader(input_data, research_plan)

        # ── Phase 4: Risk Debate (Aggressive vs Conservative vs Neutral) ──
        risk_state = self._run_risk_debate(input_data, trader_plan)

        # ── Phase 5: Portfolio Manager (Final Decision) ──
        final_decision = self._run_portfolio_manager(
            input_data, research_plan, trader_plan, risk_state
        )

        # ── Phase 6: Quality Assessment ──
        quality_report = self._assess_quality(scenario_state, risk_state, input_data)

        # ── Build result ──
        result = DebateResult(
            ticker=input_data.ticker,
            trade_date=input_data.trade_date,
            rating=final_decision.get("rating", Rating.HOLD),
            executive_summary=final_decision.get("executive_summary", ""),
            investment_thesis=final_decision.get("investment_thesis", ""),
            price_target=final_decision.get("price_target"),
            time_horizon=final_decision.get("time_horizon"),
            key_risks=final_decision.get("key_risks", []),
            confidence=final_decision.get("confidence", Confidence.MEDIUM),
            scenario_debate=scenario_state,
            risk_debate=risk_state,
            quality_report=quality_report,
            degraded=quality_report.get("degradation_detected", False),
            degradation_reason=quality_report.get("degradation_reason", ""),
        )
        # Attach structured PM decision for backtest capture
        result._pm_decision = final_decision

        # ── Save full debate if configured ──
        if self.config.save_full_debate:
            self._save_debate_json(result)
            # Backtest DB capture is internal-only & opt-in (default off) so the
            # public/sanitized slice can drop it without touching the JSON path.
            if self.config.capture_backtest:
                self._capture_backtest_db(result)

        return result

    def compare_with_baseline(
        self, debate_result: DebateResult, baseline_analysis: str
    ) -> str:
        """Compare debate output with baseline single-view analysis.

        Returns one of: "same", "different_direction", "different_nuance"
        """
        # Simple heuristic: compare the rating direction
        debate_direction = self._rating_direction(debate_result.rating)
        
        # Extract baseline direction from analysis text
        baseline_direction = self._extract_direction_from_text(baseline_analysis)

        if debate_direction == baseline_direction:
            return "same"
        elif (
            (debate_direction == "bullish" and baseline_direction == "bearish") or
            (debate_direction == "bearish" and baseline_direction == "bullish")
        ):
            return "different_direction"
        else:
            return "different_nuance"

    # ── Phase 1: Scenario Debate ──

    def _run_scenario_debate(self, input_data: AnalysisInput) -> ScenarioDebateState:
        """Run Bull vs Bear debate for max_scenario_rounds rounds.

        Auto-selects Chinese prompts for A-share/HK markets.
        """
        state = ScenarioDebateState(max_rounds=self.config.max_scenario_rounds)
        ctx, language, use_zh = self._build_context(input_data)

        quality_rounds = []

        for round_num in range(1, state.max_rounds + 1):
            is_blind = (
                round_num > 1 and
                random.random() < self.config.blind_round_probability
            )

            compressed = build_compressed_history(state.round_summaries)
            last_bear = state.bear_history[-1] if state.bear_history else ""
            last_bull = state.bull_history[-1] if state.bull_history else ""

            # ── Bear ──
            if use_zh:
                from .zh_prompts import build_bear_zh_prompt
                bear_prompt = build_bear_zh_prompt(
                    ticker=input_data.ticker,
                    company_name=input_data.company_name,
                    market_report=input_data.market_report,
                    sentiment_report=input_data.sentiment_report,
                    news_report=input_data.news_report,
                    fundamentals_report=input_data.fundamentals_report,
                    smart_money_report=input_data.smart_money_report,
                    macro_report=input_data.macro_report,
                    volume_price_report=input_data.volume_price_report,
                    history=compressed,
                    last_bull_argument=last_bull,
                    is_blind_round=is_blind,
                )
            else:
                from .prompts import build_bear_prompt
                bear_prompt = build_bear_prompt(
                    instrument_context=ctx,
                    market_report=input_data.market_report,
                    sentiment_report=input_data.sentiment_report,
                    news_report=input_data.news_report,
                    fundamentals_report=input_data.fundamentals_report,
                    history=compressed,
                    last_bull_argument=last_bull,
                    target_label="stock",
                    language=language,
                    is_blind_round=is_blind,
                )
            bear_response = self.quick_llm.invoke(bear_prompt)
            bear_text = self._extract_text(bear_response)
            state.bear_history.append(bear_text)
            state.full_history.append(bear_text)

            # ── Bull ──
            if use_zh:
                from .zh_prompts import build_bull_zh_prompt
                bull_prompt = build_bull_zh_prompt(
                    ticker=input_data.ticker,
                    company_name=input_data.company_name,
                    market_report=input_data.market_report,
                    sentiment_report=input_data.sentiment_report,
                    news_report=input_data.news_report,
                    fundamentals_report=input_data.fundamentals_report,
                    smart_money_report=input_data.smart_money_report,
                    macro_report=input_data.macro_report,
                    volume_price_report=input_data.volume_price_report,
                    history=compressed,
                    last_bear_argument=bear_text,
                    is_blind_round=is_blind,
                )
            else:
                from .prompts import build_bull_prompt
                bull_prompt = build_bull_prompt(
                    instrument_context=ctx,
                    market_report=input_data.market_report,
                    sentiment_report=input_data.sentiment_report,
                    news_report=input_data.news_report,
                    fundamentals_report=input_data.fundamentals_report,
                    history=compressed,
                    last_bear_argument=bear_text,
                    target_label="stock",
                    language=language,
                    is_blind_round=is_blind,
                )
            bull_response = self.quick_llm.invoke(bull_prompt)
            bull_text = self._extract_text(bull_response)
            state.bull_history.append(bull_text)
            state.full_history.append(bull_text)

            # ── Quality check for this round ──
            quality = check_scenario_debate(bull_text, bear_text)
            quality_rounds.append(quality)

            # ── Blind round check (if applicable) ──
            if is_blind:
                blind_check = check_blind_round(bull_text, bear_text)
                if blind_check.get("degraded"):
                    # Blind round detected degradation — log it but continue
                    pass

            # ── Compress this round ──
            if self.config.compression_enabled:
                summary = compress_round(
                    self.compressor_llm, bull_text, bear_text, round_num
                )
                if summary:
                    state.round_summaries.append(summary)

            state.current_round = round_num * 2  # bull + bear each count as one exchange

            # ── Check if we should abort due to severe degradation ──
            if quality.get("overall") == "FAIL" and round_num > 1:
                # Already degraded — don't waste tokens on more rounds
                break

            # Small delay to avoid rate limiting
            time.sleep(0.5)

        # Store quality rounds for later aggregation
        state._quality_rounds = quality_rounds  # type: ignore

        return state

    # ── Phase 2: Scenario Judge ──

    def _run_scenario_judge(
        self, input_data: AnalysisInput, scenario_state: ScenarioDebateState
    ) -> str:
        """Research Manager judges the Bull vs Bear debate.

        Auto-selects Chinese prompts for A-share/HK markets.
        """
        _, language, use_zh = self._build_context(input_data)
        full_history = build_full_history_for_judge(scenario_state.full_history)

        if use_zh:
            from .zh_prompts import build_research_manager_zh_prompt
            prompt = build_research_manager_zh_prompt(
                ticker=input_data.ticker,
                company_name=input_data.company_name,
                debate_history=full_history,
            )
        else:
            from .prompts import build_research_manager_prompt
            prompt = build_research_manager_prompt(
                instrument_context=f"Company: {input_data.company_name} ({input_data.ticker})",
                debate_history=full_history,
                language=language,
            )

        response = self.deep_llm.invoke(prompt)
        text = self._extract_text(response)
        scenario_state.judge_decision = text
        return text

    # ── Phase 3: Trader ──

    def _run_trader(self, input_data: AnalysisInput, research_plan: str) -> str:
        """Simulate the Trader converting research plan into transaction proposal.

        This is a simplified version — the full Trader with Pydantic schemas
        from TradingAgents can be added later.
        """
        prompt = f"""You are a trading agent. Based on the Research Manager's investment plan,
produce a concrete transaction proposal.

Company: {input_data.company_name} ({input_data.ticker})
Trade date: {input_data.trade_date}

Research Manager's plan:
{research_plan}

Provide:
1. **Action**: [Buy/Sell/Hold]
2. **Reasoning**: 2-3 sentences anchored in the research plan
3. **Entry Price**: [target price or "market"]
4. **Stop Loss**: [price or "none"]
5. **Position Sizing**: [e.g. "5% of portfolio"]

Respond in {self.config.output_language}."""

        response = self.quick_llm.invoke(prompt)
        return self._extract_text(response)

    # ── Phase 4: Risk Debate ──

    def _run_risk_debate(
        self, input_data: AnalysisInput, trader_plan: str
    ) -> RiskDebateState:
        """Run Aggressive vs Conservative vs Neutral risk debate.

        Round 1: Aggressive + Conservative run in PARALLEL (both see the same
        trader proposal, neither depends on the other). Neutral runs after
        seeing both. Subsequent rounds: sequential as they need each other's
        latest arguments.
        """
        state = RiskDebateState(max_rounds=self.config.max_risk_rounds)
        ctx = f"Company: {input_data.company_name} ({input_data.ticker})"

        for round_num in range(1, state.max_rounds + 1):
            compressed = build_compressed_history(state.round_summaries)
            last_agg = state.aggressive_history[-1] if state.aggressive_history else ""
            last_con = state.conservative_history[-1] if state.conservative_history else ""
            last_neu = state.neutral_history[-1] if state.neutral_history else ""

            is_first_round = (round_num == 1 and not last_agg and not last_con)

            if is_first_round:
                # ── Round 1: Aggressive + Conservative in PARALLEL ──
                agg_prompt = build_aggressive_prompt(
                    instrument_context=ctx,
                    market_report=input_data.market_report,
                    sentiment_report=input_data.sentiment_report,
                    news_report=input_data.news_report,
                    fundamentals_report=input_data.fundamentals_report,
                    trader_decision=trader_plan,
                    history=compressed,
                    last_conservative="",
                    last_neutral="",
                    language=self.config.output_language,
                )
                con_prompt = build_conservative_prompt(
                    instrument_context=ctx,
                    market_report=input_data.market_report,
                    sentiment_report=input_data.sentiment_report,
                    news_report=input_data.news_report,
                    fundamentals_report=input_data.fundamentals_report,
                    trader_decision=trader_plan,
                    history=compressed,
                    last_aggressive="",
                    last_neutral="",
                    language=self.config.output_language,
                )

                with ThreadPoolExecutor(max_workers=2) as executor:
                    agg_future = executor.submit(
                        lambda: self._extract_text(self.quick_llm.invoke(agg_prompt))
                    )
                    con_future = executor.submit(
                        lambda: self._extract_text(self.quick_llm.invoke(con_prompt))
                    )
                    agg_text = agg_future.result()
                    con_text = con_future.result()

                state.aggressive_history.append(agg_text)
                state.conservative_history.append(con_text)
                state.full_history.extend([agg_text, con_text])
                state.latest_speaker = "Conservative"

            else:
                # ── Subsequent rounds: sequential (need each other's latest) ──
                agg_prompt = build_aggressive_prompt(
                    instrument_context=ctx,
                    market_report=input_data.market_report,
                    sentiment_report=input_data.sentiment_report,
                    news_report=input_data.news_report,
                    fundamentals_report=input_data.fundamentals_report,
                    trader_decision=trader_plan,
                    history=compressed,
                    last_conservative=last_con,
                    last_neutral=last_neu,
                    language=self.config.output_language,
                )
                agg_text = self._extract_text(self.quick_llm.invoke(agg_prompt))
                state.aggressive_history.append(agg_text)
                state.full_history.append(agg_text)
                state.latest_speaker = "Aggressive"

                con_prompt = build_conservative_prompt(
                    instrument_context=ctx,
                    market_report=input_data.market_report,
                    sentiment_report=input_data.sentiment_report,
                    news_report=input_data.news_report,
                    fundamentals_report=input_data.fundamentals_report,
                    trader_decision=trader_plan,
                    history=compressed,
                    last_aggressive=agg_text,
                    last_neutral=last_neu,
                    language=self.config.output_language,
                )
                con_text = self._extract_text(self.quick_llm.invoke(con_prompt))
                state.conservative_history.append(con_text)
                state.full_history.append(con_text)
                state.latest_speaker = "Conservative"

            # ── Neutral always runs after seeing both Aggressive + Conservative ──
            neu_prompt = build_neutral_prompt(
                instrument_context=ctx,
                market_report=input_data.market_report,
                sentiment_report=input_data.sentiment_report,
                news_report=input_data.news_report,
                fundamentals_report=input_data.fundamentals_report,
                trader_decision=trader_plan,
                history=compressed,
                last_aggressive=agg_text if not is_first_round else state.aggressive_history[-1],
                last_conservative=con_text if not is_first_round else state.conservative_history[-1],
                language=self.config.output_language,
            )
            neu_text = self._extract_text(self.quick_llm.invoke(neu_prompt))
            state.neutral_history.append(neu_text)
            state.full_history.append(neu_text)
            state.latest_speaker = "Neutral"

            # ── Compress ──
            if self.config.compression_enabled:
                summary = compress_risk_round(
                    self.compressor_llm,
                    state.aggressive_history[-1],
                    state.conservative_history[-1],
                    neu_text,
                    round_num,
                )
                if summary:
                    state.round_summaries.append(summary)

            state.current_round = round_num * 3
            time.sleep(0.5)

        return state

    # ── Phase 5: Portfolio Manager ──

    def _run_portfolio_manager(
        self,
        input_data: AnalysisInput,
        research_plan: str,
        trader_plan: str,
        risk_state: RiskDebateState,
    ) -> dict:
        """Portfolio Manager makes the final decision.

        Strategy: use regular LLM call + regex parsing as the primary path.
        Structured output (json_mode) is unreliable on DeepSeek — the model
        often writes the correct rating in text but json_mode returns the
        default (Hold) with empty key_risks.

        Regex parsing handles Chinese rating words (增持/买入/持有/减持/卖出)
        and extracts key_risks from natural language discussion.
        """
        from .state import PortfolioManagerDecision

        ctx, language, use_zh = self._build_context(input_data)
        full_history = build_full_history_for_judge(risk_state.full_history)

        if use_zh:
            from .zh_prompts import build_pm_zh_prompt
            prompt = build_pm_zh_prompt(
                ticker=input_data.ticker,
                company_name=input_data.company_name,
                research_plan=research_plan,
                trader_plan=trader_plan,
                risk_debate_history=full_history,
            )
        else:
            from .prompts import build_portfolio_manager_prompt
            prompt = build_portfolio_manager_prompt(
                instrument_context=ctx,
                research_plan=research_plan,
                trader_plan=trader_plan,
                risk_debate_history=full_history,
                language=language,
            )

        # ── Primary: unstructured call + regex parse (handles Chinese reliably) ──
        try:
            response = self.deep_llm.invoke(prompt)
            full_text = self._extract_text(response)
            risk_state.judge_decision = full_text
            result = self._parse_pm_decision(full_text)

            # ── Enhancement: try structured output as supplement ──
            # Only used to fill in fields regex might miss (confidence enum, etc.)
            # We do NOT rely on structured output for rating or key_risks.
            try:
                structured_llm = self.deep_llm.with_structured_output(
                    PortfolioManagerDecision,
                    method="json_mode",
                )
                decision: PortfolioManagerDecision = structured_llm.invoke(prompt)

                # Merge: prefer regex for text-derived fields,
                # structured for enum-constrained fields
                if not result.get("price_target") and decision.price_target:
                    result["price_target"] = decision.price_target
                if not result.get("time_horizon") and decision.time_horizon:
                    result["time_horizon"] = decision.time_horizon
                if result.get("confidence") == Confidence.MEDIUM and \
                   decision.confidence != Confidence.MEDIUM:
                    result["confidence"] = decision.confidence
            except Exception:
                pass  # Structured output is optional; regex is sufficient

            return result

        except Exception as e:
            # Last-resort fallback: return hold with error context
            risk_state.judge_decision = f"PM decision failed: {e}"
            return {
                "rating": Rating.HOLD,
                "executive_summary": "",
                "investment_thesis": f"Portfolio Manager analysis unavailable: {e}",
                "price_target": None,
                "time_horizon": None,
                "key_risks": [],
                "confidence": Confidence.LOW,
            }

    # ── Quality Assessment ──

    def _assess_quality(
        self,
        scenario_state: ScenarioDebateState,
        risk_state: RiskDebateState,
        input_data: AnalysisInput,
    ) -> dict:
        """Generate quality report for the complete debate.

        Runs both scenario and risk debate quality checks.
        """
        from .quality import check_risk_debate

        scenario_results = getattr(scenario_state, '_quality_rounds', [])

        # Run risk debate quality checks
        risk_results = []
        if risk_state.aggressive_history and risk_state.conservative_history:
            for i in range(len(risk_state.aggressive_history)):
                if i < len(risk_state.conservative_history):
                    risk_results.append(
                        check_risk_debate(
                            risk_state.aggressive_history[i],
                            risk_state.conservative_history[i],
                        )
                    )

        report = generate_quality_report(
            scenario_results=scenario_results,
            risk_results=risk_results if risk_results else None,
        )

        return {
            "overall": report.overall,
            "scenario_divergence": report.scenario_divergence,
            "risk_divergence": report.risk_divergence,
            "bear_citations": report.bear_citations,
            "bull_citations": report.bull_citations,
            "bear_unique_risks": report.bear_unique_risks,
            "blind_overlap": report.blind_overlap,
            "warnings": report.warnings,
            "degradation_detected": report.degradation_detected,
            "degradation_reason": report.degradation_reason,
        }

    # ── Helpers ──

    def _extract_text(self, response) -> str:
        """Extract text from LLM response, handling various return types."""
        if hasattr(response, 'content'):
            return response.content
        elif isinstance(response, str):
            return response
        return str(response)

    def _parse_pm_decision(self, text: str) -> dict:
        """Parse Portfolio Manager's decision text into structured fields.

        Handles both English and Chinese formats. Chinese rating words
        (增持/买入/持有/减持/卖出) are mapped to the Rating enum.
        Extracts key_risks, adopted/rejected arguments for backtest tracking.
        """
        import re

        result = {
            "rating": Rating.HOLD,
            "executive_summary": "",
            "investment_thesis": text,
            "price_target": None,
            "time_horizon": None,
            "key_risks": [],
            "adopted_arguments": [],
            "rejected_arguments": [],
            "confidence": Confidence.MEDIUM,
        }

        # ── Rating extraction (English + Chinese) ──
        rating_patterns = [
            # English format: **Rating**: Buy
            (r'\*\*Rating\*\*[:\s]*(\w+)', "en"),
            (r'\*\*Recommendation\*\*[:\s]*(\w+)', "en"),
            # Chinese format: **评级**：增持  or - **评级**：增持
            (r'\*\*评级\*\*[：:\s]*([^\n*]+)', "zh"),
            (r'评级[：:]\s*([^\n]{1,6})', "zh"),
            # Bold inline: **增持**
            (r'\*\*(增持|买入|持有|减持|卖出|Buy|Sell|Hold|Overweight|Underweight)\*\*', "inline"),
        ]

        # Chinese → Rating mapping
        ZH_RATING_MAP = {
            "买入": Rating.BUY, "买": Rating.BUY,
            "增持": Rating.OVERWEIGHT, "加仓": Rating.OVERWEIGHT,
            "持有": Rating.HOLD, "观望": Rating.HOLD,
            "减持": Rating.UNDERWEIGHT, "减仓": Rating.UNDERWEIGHT,
            "卖出": Rating.SELL, "卖": Rating.SELL,
        }

        for pattern, ptype in rating_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                raw = match.group(1).strip()
                if ptype == "zh" or ptype == "inline":
                    # Try Chinese mapping first
                    mapped = ZH_RATING_MAP.get(raw)
                    if mapped:
                        result["rating"] = mapped
                        break
                    # Fall through to English
                try:
                    result["rating"] = Rating(raw.capitalize())
                    break
                except ValueError:
                    continue

        # ── Executive summary (English + Chinese) ──
        summary_patterns = [
            r'\*\*执行摘要\*\*[：:\s]*(.*?)(?=\*\*|#{1,3}\s|\n\n\*\*)',
            r'\*\*Executive Summary\*\*[:\s]*(.*?)(?=\*\*|#{1,3}\s|\n\n\*\*)',
            r'执行摘要[：:]\s*(.*?)(?=\*\*|#{1,3}\s|\n\n\*\*)',
        ]
        for pat in summary_patterns:
            m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
            if m:
                result["executive_summary"] = m.group(1).strip()
                break

        # ── Investment thesis (English + Chinese) ──
        thesis_patterns = [
            r'\*\*投资逻辑\*\*[：:\s]*(.*?)(?=\*\*目标价|\*\*价格|#{1,3}\s|\Z)',
            r'\*\*Investment Thesis\*\*[:\s]*(.*?)(?=\*\*Price|\*\*Target|#{1,3}\s|\Z)',
            r'投资逻辑[：:]\s*(.*?)(?=\*\*目标价|\*\*价格|#{1,3}\s|\Z)',
        ]
        for pat in thesis_patterns:
            m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
            if m:
                thesis = m.group(1).strip()
                if len(thesis) > 50:  # Must be substantial
                    result["investment_thesis"] = thesis
                    break

        # ── Key risks ──
        risk_section_patterns = [
            r'\*\*关键风险\*\*[：:\s]*(.*?)(?=\*\*|#{1,3}\s|\n\n\Z|\Z)',
            r'\*\*使本决策失效的条件\*\*[：:\s]*(.*?)(?=\*\*|#{1,3}\s|\Z)',
            r'\*\*Key Risks\*\*[:\s]*(.*?)(?=\*\*|#{1,3}\s|\n\n\Z|\Z)',
            r'\*\*Risks\*\*[:\s]*(.*?)(?=\*\*|#{1,3}\s|\n\n\Z|\Z)',
        ]
        risk_section = None
        for pat in risk_section_patterns:
            m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
            if m:
                risk_section = m.group(1).strip()
                break

        if risk_section:
            # Extract numbered/bulleted risks
            risk_items = re.findall(
                r'(?:^|\n)\s*(?:\d+[\.\)、]\s*|\*\*\d+[\.\)、]\*\*\s*|[-•]\s*)(.*?)(?=\n\s*(?:\d+[\.\)、]|\*\*\d+|\*\*使|\*\*关键|\*\*总结|[-•])|\Z)',
                risk_section, re.DOTALL
            )
            if not risk_items:
                # Try alternate pattern: **N. title** body
                risk_items = re.findall(
                    r'\*\*\d+[\.\)、]\s*(.*?)\*\*[：:\s]*(.*?)(?=\n\s*\*\*\d+|\Z)',
                    risk_section, re.DOTALL
                )
                risk_items = [
                    f"{title.strip()}: {body.strip()}"[:200]
                    for title, body in risk_items if title.strip()
                ]

            result["key_risks"] = [
                r.strip()[:200] for r in risk_items if len(r.strip()) > 10
            ][:5]  # Cap at 5 risks

        # Fallback: if no structured risks section found, look for risk mentions
        if not result["key_risks"]:
            risk_mentions = re.findall(
                r'(?:风险|risk|止损|stop.loss)[：:\s]*(.{10,150}?)(?=[。.\n]|$)',
                text, re.IGNORECASE
            )
            if risk_mentions:
                result["key_risks"] = risk_mentions[:4]

        # ── Key Arguments: Adopted / Rejected ──
        # Extract structured argument sections for backtest verification
        adopted = self._extract_argument_section(text, "adopted")
        if adopted:
            result["adopted_arguments"] = adopted
        rejected = self._extract_argument_section(text, "rejected")
        if rejected:
            result["rejected_arguments"] = rejected

        # ── Price target (English + Chinese) ──
        price_patterns = [
            r'\*\*目标价\*\*[：:\s]*\$?(\d+\.?\d*)',
            r'\*\*Price Target\*\*[:\s]*\$?(\d+\.?\d*)',
            r'目标价[：:]\s*\$?(\d+\.?\d*)',
            r'(?:target|目标).{0,10}?(\d{2,3}\.?\d*)\s*(?:港元|HKD|港币)',
        ]
        for pat in price_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                result["price_target"] = float(m.group(1))
                break

        # ── Time horizon ──
        time_patterns = [
            r'\*\*时间框架\*\*[：:\s]*(.*?)(?:\n|$)',
            r'\*\*Time Horizon\*\*[:\s]*(.*?)(?:\n|$)',
            r'\*\*持有期\*\*[：:\s]*(.*?)(?:\n|$)',
            r'持有期[：:]\s*(.*?)(?:\n|$)',
        ]
        for pat in time_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                result["time_horizon"] = m.group(1).strip()
                break

        # ── Confidence ──
        if re.search(r'(高信心|high.confidence|强烈|strong)', text, re.IGNORECASE):
            result["confidence"] = Confidence.HIGH
        elif re.search(r'(低信心|low.confidence|不确定|uncertain)', text, re.IGNORECASE):
            result["confidence"] = Confidence.LOW

        return result

    def _rating_direction(self, rating: Rating) -> str:
        """Map rating to directional signal."""
        if rating in (Rating.BUY, Rating.OVERWEIGHT):
            return "bullish"
        elif rating in (Rating.SELL, Rating.UNDERWEIGHT):
            return "bearish"
        return "neutral"

    def _extract_argument_section(self, text: str, arg_type: str) -> list[dict]:
        """Extract structured arguments from PM decision text.

        arg_type: 'adopted' or 'rejected'
        Returns list of {side, argument, evidence} dicts.
        """
        import re

        if arg_type == "adopted":
            patterns = [
                r'\*\*Key Arguments Adopted\*\*[:\s]*(.*?)(?=\*\*Key Arguments Rejected|\*\*Price|\*\*目标|\*\*Time|\*\*时间|\*\*Key Risks|\*\*关键风险|\Z)',
                r'\*\*采纳的关键论点\*\*[：:\s]*(.*?)(?=\*\*拒绝|\*\*未采纳|\*\*Price|\*\*目标|\*\*Time|\*\*时间|\*\*Key Risks|\*\*关键风险|\Z)',
            ]
        else:
            patterns = [
                r'\*\*Key Arguments Rejected\*\*[:\s]*(.*?)(?=\*\*Price|\*\*目标|\*\*Time|\*\*时间|\*\*Key Risks|\*\*关键风险|\Z)',
                r'\*\*拒绝的关键论点\*\*[：:\s]*(.*?)(?=\*\*Price|\*\*目标|\*\*Time|\*\*时间|\*\*Key Risks|\*\*关键风险|\Z)',
                r'\*\*未采纳的关键论点\*\*[：:\s]*(.*?)(?=\*\*Price|\*\*目标|\*\*Time|\*\*时间|\*\*Key Risks|\*\*关键风险|\Z)',
            ]

        section = None
        for pat in patterns:
            m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
            if m:
                section = m.group(1).strip()
                break

        if not section:
            return []

        # Extract bullet points or numbered items
        items = re.findall(
            r'(?:^|\n)\s*(?:[-•*]|\d+[\.\)、])\s*(.*?)(?=\n\s*(?:[-•*]|\d+[\.\)、])|\Z)',
            section, re.DOTALL
        )
        if not items:
            # Try to split by newlines
            items = [l.strip() for l in section.split('\n') if l.strip() and len(l.strip()) > 10]

        args = []
        for item in items[:10]:  # Cap at 10
            item = item.strip()
            # Try to identify side mentioned
            side = "unknown"
            if re.search(r'(?:Bull|多头|看多|乐观)', item, re.IGNORECASE):
                side = "bull"
            elif re.search(r'(?:Bear|空头|看空|悲观|保守|Conservative)', item, re.IGNORECASE):
                side = "bear"
            elif re.search(r'(?:Aggressive|激进)', item, re.IGNORECASE):
                side = "aggressive"
            elif re.search(r'(?:Neutral|中立)', item, re.IGNORECASE):
                side = "neutral"

            args.append({
                "side": side,
                "argument": item[:500],
                "evidence": "",
            })

        return args

    def _extract_direction_from_text(self, text: str) -> str:
        """Extract directional signal from unstructured analysis text."""
        text_lower = text.lower()
        bullish_signals = ["buy", "买入", "看好", "bullish", "增持", "加仓", "推荐"]
        bearish_signals = ["sell", "卖出", "看空", "bearish", "减持", "减仓", "回避"]

        bull_count = sum(1 for s in bullish_signals if s in text_lower)
        bear_count = sum(1 for s in bearish_signals if s in text_lower)

        if bull_count > bear_count:
            return "bullish"
        elif bear_count > bull_count:
            return "bearish"
        return "neutral"

    def _save_debate_json(self, result: DebateResult) -> None:
        """Save full debate text to disk.

        Pure standard library only — no internal project imports — so the
        public/sanitized slice can keep this method untouched.
        """
        log_dir = os.path.expanduser(self.config.log_dir)
        os.makedirs(log_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{result.ticker}_{result.trade_date}_{timestamp}.json"
        filepath = os.path.join(log_dir, filename)

        log_data = {
            "ticker": result.ticker,
            "trade_date": result.trade_date,
            "rating": result.rating.value,
            "executive_summary": result.executive_summary,
            "investment_thesis": result.investment_thesis,
            "key_risks": result.key_risks,
            "scenario_debate": {
                "bull_history": result.scenario_debate.bull_history,
                "bear_history": result.scenario_debate.bear_history,
                "judge_decision": result.scenario_debate.judge_decision,
            },
            "risk_debate": {
                "aggressive_history": result.risk_debate.aggressive_history,
                "conservative_history": result.risk_debate.conservative_history,
                "neutral_history": result.risk_debate.neutral_history,
                "judge_decision": result.risk_debate.judge_decision,
            },
            "quality_report": result.quality_report,
            "degraded": result.degraded,
            "degradation_reason": result.degradation_reason,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)

    def _capture_backtest_db(self, result: DebateResult) -> None:
        """Capture structured prediction to backtest DB (internal-only, opt-in).

        Isolated from _save_debate_json so the public/sanitized slice can drop
        this method cleanly, leaving no internal-module import in the JSON path.
        """
        try:
            from investment.backtest import DecisionLogger
            logger = DecisionLogger()

            # Build structured debate args from PM decision
            pm_decision = getattr(result, '_pm_decision', {})
            debate_args = []
            for arg in pm_decision.get("adopted_arguments", []):
                debate_args.append({
                    "role": arg.get("side", "unknown"),
                    "argument_text": arg.get("argument", ""),
                    "argument_type": "",
                    "evidence_cited": arg.get("evidence", ""),
                    "pm_adopted": True,
                })
            for arg in pm_decision.get("rejected_arguments", []):
                debate_args.append({
                    "role": arg.get("side", "unknown"),
                    "argument_text": arg.get("argument", ""),
                    "argument_type": "",
                    "evidence_cited": arg.get("evidence", ""),
                    "pm_adopted": False,
                })

            result_data = {
                "ticker": result.ticker,
                "ticker_name": result.ticker,
                "market": self._infer_market_from_ticker(result.ticker),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "prediction": {
                    "rating": result.rating.value,
                    "price_target": pm_decision.get("price_target"),
                    "time_horizon": pm_decision.get("time_horizon", "3m"),
                    "confidence": pm_decision.get("confidence", "medium"),
                    "executive_summary": result.executive_summary,
                    "investment_thesis": result.investment_thesis,
                    "key_risks": result.key_risks,
                },
                "snapshot_price": 0.0,  # Will be filled by engine if available
                "snapshot_market_state": "",
                "debate_args": debate_args,
                "debate_config": {
                    "rounds": result.scenario_debate.rounds if hasattr(
                        result.scenario_debate, 'rounds') else 2,
                    "quality_report": result.quality_report,
                },
            }
            logger.capture_debate_result(result_data)
        except Exception as e:
            print(f"[DebateEngine] Failed to capture to backtest DB: {e}",
                  file=sys.stderr)

    @staticmethod
    def _infer_market_from_ticker(ticker: str) -> str:
        if ".HK" in ticker.upper() or (ticker.isdigit() and len(ticker) <= 5):
            return "hk"
        if ".SS" in ticker.upper() or ".SZ" in ticker.upper():
            return "a_share"
        return "us"


def run_debate(
    input_data: AnalysisInput,
    config: Optional[DebateConfig] = None,
) -> DebateResult:
    """Convenience function to run a single debate analysis."""
    engine = DebateEngine(config=config)
    return engine.run(input_data)
