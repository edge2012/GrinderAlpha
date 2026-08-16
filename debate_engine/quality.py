"""
Quality checks for debate output — the automated second and third layers
of the three-layer defense against debate degradation.

Layer 1: Prompt-level constraints (in prompts.py)
Layer 2: Automated quality metrics (this module) 
Layer 3: Blind round insertion (triggered by this module)

All quality checks return a QualityReport dict. If the report flags
WARN or FAIL, the engine can trigger re-runs or fall back to single-view.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ── Quality thresholds ──

QUALITY_THRESHOLDS = {
    # Scenario debate (Bull vs Bear)
    "scenario_divergence_min": 0.35,     # cosine distance below this → degraded
    "bear_citation_min": 2,              # minimum data citations in Bear argument
    "bear_unique_risks_min": 1,          # minimum unique risk dimensions Bear must raise
    "bull_citation_min": 2,              # minimum data citations in Bull argument
    
    # Risk debate (Aggressive vs Conservative vs Neutral)
    "risk_divergence_min": 0.30,         # Aggressive vs Conservative divergence
    "risk_citation_min": 1,              # per debater minimum citations
    
    # Blind round
    "blind_overlap_max": 0.60,           # max allowed overlap in blind round
    "blind_round_frequency": 0.33,       # probability of blind round insertion (0-1)
}


# ── Data citation patterns ──

CITATION_PATTERNS = [
    # Percentages
    (r'\d+\.?\d*\s*%', 'percentage'),
    # Dollar amounts
    (r'\$\s*\d[\d,.]*\s*(?:million|billion|万亿|亿|万|M|B|K)?', 'monetary'),
    # Financial periods
    (r'(?:Q[1-4]\s*\d{4}|FY\s*\d{4}|FY\d{2})', 'period'),
    # Specific metrics
    (r'(?:P/E|P/B|EV/EBITDA|ROE|ROIC|EPS|D/E)\s*(?:ratio\s*)?[:：]?\s*\d+\.?\d*', 'ratio'),
    # Revenue/earnings mentions
    (r'(?:营收|收入|revenue|利润|earnings|毛利|gross margin|净利|net income|自由现金流|FCF)\s*[:：]?\s*\d+', 'financial_term'),
    # Growth rates  
    (r'(?:增长|growth|增速|decline|下降)\s*(?:rate\s*)?[:：]?\s*\d+\.?\d*\s*%', 'growth_rate'),
    # Dates
    (r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}', 'date'),
]

# Patterns indicating agreement / degradation
AGREEMENT_PATTERNS = [
    r'(?:the Bull has a point|同意|说得对|I agree|has merit|有一定道理|确实如此)',
    r'(?:on the other hand|另一方面|however.*also|虽然.*但是.*也)',
    r'(?:balanced view|平衡来看|从另一个角度)',
]


@dataclass
class QualityReport:
    """Output of quality checks on a debate."""
    overall: str = "PASS"  # PASS / WARN / FAIL
    scenario_divergence: Optional[float] = None
    risk_divergence: Optional[float] = None
    bear_citations: int = 0
    bull_citations: int = 0
    bear_unique_risks: int = 0
    blind_overlap: Optional[float] = None
    agreement_signals: int = 0
    warnings: list[str] = field(default_factory=list)
    degradation_detected: bool = False
    degradation_reason: str = ""


def count_citations(text: str) -> int:
    """Count how many data citations appear in debate text."""
    count = 0
    for pattern, _ in CITATION_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        count += len(matches)
    return count


def count_agreement_signals(text: str) -> int:
    """Count agreement/degradation signals in Bear's text."""
    count = 0
    for pattern in AGREEMENT_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        count += len(matches)
    return count


def compute_divergence(bull_text: str, bear_text: str) -> float:
    """Compute semantic divergence between two arguments.

    Uses a lightweight keyword-overlap approach as a proxy for embedding distance.
    Higher = more divergent (more adversarial). Range roughly 0.0-1.0.

    For production, this can be upgraded to use embedding models.
    """
    # Extract noun phrases and key terms from each
    def extract_key_terms(text: str) -> set[str]:
        # Extract capitalized words, financial terms, percentages
        terms = set()
        # Capitalized multi-word phrases (likely proper nouns / key concepts)
        for match in re.finditer(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text):
            terms.add(match.group().lower())
        # Financial metrics
        for match in re.finditer(r'\b(?:P/E|ROE|ROIC|EPS|D/E|EV|EBITDA|FCF|NIM)\b', text, re.IGNORECASE):
            terms.add(match.group().lower())
        # Key risk/reward terms
        for match in re.finditer(
            r'\b(?:growth|value|risk|margin|debt|cash flow|competitive|'
            r'moat|regulation|tariff|inflation|recession|bubble|'
            r'saturation|disruption|commodity|cyclical|secular)\b',
            text, re.IGNORECASE
        ):
            terms.add(match.group().lower())
        return terms

    bull_terms = extract_key_terms(bull_text)
    bear_terms = extract_key_terms(bear_text)

    if not bull_terms or not bear_terms:
        return 0.5  # Neutral if extraction fails

    # Jaccard distance
    intersection = len(bull_terms & bear_terms)
    union = len(bull_terms | bear_terms)
    similarity = intersection / union if union > 0 else 0

    # Invert to get divergence (1.0 = completely different vocabulary)
    return 1.0 - similarity


def check_scenario_debate(
    bull_argument: str,
    bear_argument: str,
) -> dict:
    """Run quality checks on one round of Bull vs Bear debate.

    Returns a dict with quality metrics.
    """
    divergence = compute_divergence(bull_argument, bear_argument)
    bear_citations = count_citations(bear_argument)
    bull_citations = count_citations(bull_argument)
    agreement_signals = count_agreement_signals(bear_argument)

    warnings = []

    if divergence < QUALITY_THRESHOLDS["scenario_divergence_min"]:
        warnings.append(
            f"Low divergence ({divergence:.2f} < {QUALITY_THRESHOLDS['scenario_divergence_min']}). "
            "Bull and Bear may be converging — debate is not adversarial enough."
        )

    if bear_citations < QUALITY_THRESHOLDS["bear_citation_min"]:
        warnings.append(
            f"Bear citations too low ({bear_citations} < {QUALITY_THRESHOLDS['bear_citation_min']}). "
            "Bear is not grounding arguments in data."
        )

    if bull_citations < QUALITY_THRESHOLDS["bull_citation_min"]:
        warnings.append(
            f"Bull citations too low ({bull_citations} < {QUALITY_THRESHOLDS['bull_citation_min']}). "
            "Bull is not grounding arguments in data."
        )

    if agreement_signals > 2:
        warnings.append(
            f"Bear showing {agreement_signals} agreement signals. "
            "Bear may be losing adversarial stance."
        )

    # Determine overall status
    critical_warnings = [w for w in warnings if "Low divergence" in w or "Bear citations too low" in w]
    
    if len(critical_warnings) >= 2:
        overall = "FAIL"
    elif critical_warnings:
        overall = "WARN"
    else:
        overall = "PASS"

    return {
        "overall": overall,
        "divergence": divergence,
        "bear_citations": bear_citations,
        "bull_citations": bull_citations,
        "agreement_signals": agreement_signals,
        "warnings": warnings,
    }


def check_risk_debate(
    aggressive_argument: str,
    conservative_argument: str,
) -> dict:
    """Run quality checks on one round of Aggressive vs Conservative risk debate.

    Uses the same divergence and citation metrics as scenario debates,
    but with risk-specific thresholds.
    """
    divergence = compute_divergence(aggressive_argument, conservative_argument)
    agg_citations = count_citations(aggressive_argument)
    con_citations = count_citations(conservative_argument)
    agreement_signals = count_agreement_signals(conservative_argument)

    warnings = []

    if divergence < QUALITY_THRESHOLDS["risk_divergence_min"]:
        warnings.append(
            f"Risk debate low divergence ({divergence:.2f} < {QUALITY_THRESHOLDS['risk_divergence_min']}). "
            "Aggressive and Conservative may not be truly opposing."
        )

    if agg_citations < QUALITY_THRESHOLDS["risk_citation_min"]:
        warnings.append(
            f"Aggressive citations low ({agg_citations} < {QUALITY_THRESHOLDS['risk_citation_min']}). "
            "Risk-taking argument not grounded in data."
        )

    if con_citations < QUALITY_THRESHOLDS["risk_citation_min"]:
        warnings.append(
            f"Conservative citations low ({con_citations} < {QUALITY_THRESHOLDS['risk_citation_min']}). "
            "Risk-averse argument not grounded in data."
        )

    if agreement_signals > 1:
        warnings.append(
            f"Conservative showing {agreement_signals} agreement signals. "
            "Risk debate may be converging."
        )

    # Determine overall
    critical_warnings = [
        w for w in warnings
        if "low divergence" in w.lower() or "citations low" in w.lower()
    ]
    if len(critical_warnings) >= 2:
        overall = "FAIL"
    elif critical_warnings:
        overall = "WARN"
    else:
        overall = "PASS"

    return {
        "overall": overall,
        "divergence": divergence,
        "agg_citations": agg_citations,
        "con_citations": con_citations,
        "agreement_signals": agreement_signals,
        "warnings": warnings,
    }


def check_blind_round(
    bull_blind: str,
    bear_blind: str,
) -> dict:
    """Check if blind round arguments are sufficiently divergent.

    In a blind round, both debaters independently analyze the same data.
    If their output is too similar, roles have degraded and both are
    defaulting to the same analytical framework.
    """
    overlap = 1.0 - compute_divergence(bull_blind, bear_blind)  # similarity
    
    if overlap > QUALITY_THRESHOLDS["blind_overlap_max"]:
        return {
            "overlap": overlap,
            "degraded": True,
            "warning": (
                f"Blind round overlap too high ({overlap:.2f} > "
                f"{QUALITY_THRESHOLDS['blind_overlap_max']}). "
                "Both debaters producing similar analysis — roles have degraded."
            ),
        }
    
    return {
        "overlap": overlap,
        "degraded": False,
        "warning": "",
    }


def generate_quality_report(
    scenario_results: list[dict],
    risk_results: Optional[list[dict]] = None,
    blind_check: Optional[dict] = None,
) -> QualityReport:
    """Generate a comprehensive quality report from all debate rounds.

    Args:
        scenario_results: List of quality check dicts from each scenario round
        risk_results: Optional list from risk debate rounds
        blind_check: Optional blind round check result

    Returns:
        QualityReport with overall assessment
    """
    report = QualityReport()

    # Aggregate scenario debate metrics
    if scenario_results:
        report.scenario_divergence = sum(
            r.get("divergence", 0) for r in scenario_results
        ) / len(scenario_results)
        report.bear_citations = sum(r.get("bear_citations", 0) for r in scenario_results)
        report.bull_citations = sum(r.get("bull_citations", 0) for r in scenario_results)

    # Aggregate risk debate metrics
    if risk_results:
        report.risk_divergence = sum(
            r.get("divergence", 0) for r in risk_results
        ) / len(risk_results)

    # Blind round
    if blind_check:
        report.blind_overlap = blind_check.get("overlap")

    # Collect all warnings
    for r in scenario_results:
        report.warnings.extend(r.get("warnings", []))
    if risk_results:
        for r in risk_results:
            report.warnings.extend(r.get("warnings", []))

    # Determine degradation
    fail_count = sum(1 for r in scenario_results if r.get("overall") == "FAIL")
    warn_count = sum(1 for r in scenario_results if r.get("overall") == "WARN")

    if blind_check and blind_check.get("degraded"):
        report.degradation_detected = True
        report.degradation_reason = blind_check.get("warning", "Blind round degradation detected")
    elif fail_count > 0:
        report.degradation_detected = True
        report.degradation_reason = f"{fail_count} round(s) failed quality checks"
    elif warn_count >= len(scenario_results) // 2 + 1:
        report.degradation_detected = True
        report.degradation_reason = f"Majority of rounds ({warn_count}/{len(scenario_results)}) have warnings"

    if report.degradation_detected:
        report.overall = "FAIL" if fail_count > 0 else "WARN"
    elif warn_count > 0:
        report.overall = "WARN"
    else:
        report.overall = "PASS"

    return report
