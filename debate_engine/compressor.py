"""
Context compression for debate rounds.

Problem: Full debate history grows ~1500 tokens per round. After 2-3 rounds,
context can exceed 20K+ tokens with analyst reports included.

Solution: After each round, compress the raw debate text into a structured
DebateRoundSummary (Pydantic model). Debaters receive compressed history +
only the last opponent's raw text. The Judge receives the full history
(only one node, so the cost is bounded).

Compression rate target: 80%+ reduction in debate history tokens.
"""

from __future__ import annotations

from typing import Optional

from .state import DebateRoundSummary


COMPRESSOR_SYSTEM = """You are a debate summarizer. Your job is to extract the STRUCTURE of a debate
round, not to rephrase it. Output ONLY valid JSON matching the schema below.

For each field:
- bull_core_thesis: One sentence. What is the Bull's single most important argument this round?
- bear_core_thesis: One sentence. What is the Bear's single most devastating counter?
- key_disagreements: List 2-5 dimensions where they fundamentally disagree.
  Be specific: not "valuation" but "whether 35x P/E is justified given 12% revenue growth"
- convergence_points: List any points both sides acknowledged (if none, empty list)
- bear_unique_risks: List 1-5 risks the Bear raised that the Bull did NOT address.
  This is the most important field — it captures what the debate revealed.
- evidence_quality_bull: "strong" if Bull cited specific numbers/dates/sources for most claims.
  "medium" if some claims were supported. "weak" if mostly vague statements.
- evidence_quality_bear: Same scale for Bear.

**Critical rules:**
- Be specific. "Valuation concerns" is not a key disagreement. "Bull argues 35x P/E justified 
  by growth; Bear argues P/E will contract to 20x as growth decelerates to 8%" is.
- bear_unique_risks MUST capture risks the Bull sidestepped entirely. If none, say so.
- Do not editorialize. Extract what was actually argued, not what you think should have been.

Output ONLY this JSON structure, nothing else:
{{"round_number": <n>, "bull_core_thesis": "...", "bear_core_thesis": "...", 
 "key_disagreements": ["...", "..."], "convergence_points": ["..."] or [], 
 "bear_unique_risks": ["..."] or [], "evidence_quality_bull": "strong|medium|weak", 
 "evidence_quality_bear": "strong|medium|weak"}}"""


def build_compressor_prompt(
    bull_argument: str,
    bear_argument: str,
    round_number: int,
) -> str:
    """Build the prompt for compressing one debate round."""
    return f"""{COMPRESSOR_SYSTEM}

Round {round_number}:

Bull Analyst:
{bull_argument}

Bear Analyst:
{bear_argument}

Extract the structured summary as JSON."""


def build_risk_compressor_prompt(
    aggressive_argument: str,
    conservative_argument: str,
    neutral_argument: str,
    round_number: int,
) -> str:
    """Build the prompt for compressing one risk debate round.

    For risk debates, the "sides" are mapped as:
    - bull_core_thesis → aggressive's strongest argument
    - bear_core_thesis → conservative's strongest counter
    - bear_unique_risks → risks the conservative raised that aggressive dismissed
    """
    return f"""{COMPRESSOR_SYSTEM}

Risk Debate Round {round_number}:

Aggressive Analyst:
{aggressive_argument}

Conservative Analyst:
{conservative_argument}

Neutral Analyst:
{neutral_argument}

For this three-way debate:
- Map "Bull" to the Aggressive analyst (risk-taking view)
- Map "Bear" to the Conservative analyst (risk-averse view)
- Include the Neutral analyst's evaluation in key_disagreements where they 
  identified which side had stronger evidence on specific dimensions

Extract the structured summary as JSON."""


def compress_round(
    llm,
    bull_argument: str,
    bear_argument: str,
    round_number: int,
) -> Optional[DebateRoundSummary]:
    """Compress one Bull-vs-Bear debate round into a structured summary.

    Args:
        llm: LangChain-compatible LLM (quick model is fine for compression)
        bull_argument: Full text of Bull's argument this round
        bear_argument: Full text of Bear's argument this round
        round_number: Round number (1-based)

    Returns:
        DebateRoundSummary or None if compression failed
    """
    import json

    prompt = build_compressor_prompt(bull_argument, bear_argument, round_number)
    
    try:
        response = llm.invoke(prompt)
        content = response.content if hasattr(response, 'content') else str(response)
        
        # Extract JSON from response (handle markdown code blocks)
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        
        data = json.loads(content.strip())
        
        return DebateRoundSummary(
            round_number=data.get("round_number", round_number),
            bull_core_thesis=data.get("bull_core_thesis", ""),
            bear_core_thesis=data.get("bear_core_thesis", ""),
            key_disagreements=data.get("key_disagreements", []),
            convergence_points=data.get("convergence_points", []),
            bear_unique_risks=data.get("bear_unique_risks", []),
            evidence_quality_bull=data.get("evidence_quality_bull", "medium"),
            evidence_quality_bear=data.get("evidence_quality_bear", "medium"),
        )
    except Exception:
        # Graceful degradation: return a minimal summary
        return DebateRoundSummary(
            round_number=round_number,
            bull_core_thesis=bull_argument[:200] + "..." if len(bull_argument) > 200 else bull_argument,
            bear_core_thesis=bear_argument[:200] + "..." if len(bear_argument) > 200 else bear_argument,
            key_disagreements=["Compression failed — see full debate history"],
            evidence_quality_bull="medium",
            evidence_quality_bear="medium",
        )


def compress_risk_round(
    llm,
    aggressive_argument: str,
    conservative_argument: str,
    neutral_argument: str,
    round_number: int,
) -> Optional[DebateRoundSummary]:
    """Compress one risk debate round (3 participants)."""
    import json

    prompt = build_risk_compressor_prompt(
        aggressive_argument, conservative_argument, neutral_argument, round_number
    )
    
    try:
        response = llm.invoke(prompt)
        content = response.content if hasattr(response, 'content') else str(response)
        
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        
        data = json.loads(content.strip())
        
        return DebateRoundSummary(
            round_number=data.get("round_number", round_number),
            bull_core_thesis=data.get("bull_core_thesis", ""),
            bear_core_thesis=data.get("bear_core_thesis", ""),
            key_disagreements=data.get("key_disagreements", []),
            convergence_points=data.get("convergence_points", []),
            bear_unique_risks=data.get("bear_unique_risks", []),
            evidence_quality_bull=data.get("evidence_quality_bull", "medium"),
            evidence_quality_bear=data.get("evidence_quality_bear", "medium"),
        )
    except Exception:
        return DebateRoundSummary(
            round_number=round_number,
            bull_core_thesis=aggressive_argument[:200] + "..." if len(aggressive_argument) > 200 else aggressive_argument,
            bear_core_thesis=conservative_argument[:200] + "..." if len(conservative_argument) > 200 else conservative_argument,
            key_disagreements=["Compression failed — see full debate history"],
            evidence_quality_bull="medium",
            evidence_quality_bear="medium",
        )


def build_compressed_history(
    summaries: list[DebateRoundSummary],
) -> str:
    """Build a compact history string from round summaries for debater context.

    Target: ~200 tokens per round vs ~1500 for raw text.
    """
    if not summaries:
        return ""

    parts = []
    for s in summaries:
        parts.append(
            f"Round {s.round_number}: "
            f"Bull: {s.bull_core_thesis} | "
            f"Bear: {s.bear_core_thesis}"
        )
        if s.key_disagreements:
            parts.append(f"  Disagreements: {'; '.join(s.key_disagreements)}")
        if s.bear_unique_risks:
            parts.append(f"  Bear's unaddressed risks: {'; '.join(s.bear_unique_risks)}")

    return "\n".join(parts)


def build_full_history_for_judge(
    raw_history: list[str],
) -> str:
    """Build the full debate history for the Judge node.

    The Judge sees ALL raw text, since it's only one node and
    the cost is bounded.
    """
    return "\n\n".join(raw_history)
