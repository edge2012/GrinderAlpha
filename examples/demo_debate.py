#!/usr/bin/env python3
"""LLM debate engine demo — multi-agent adversarial debate.

Requires an API key (see README "Configuration"):
    export OPENAI_API_KEY=sk-...
    export OPENAI_BASE_URL=https://api.openai.com/v1   # or any OpenAI-compatible endpoint

Run:
    python3 examples/demo_debate.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from investment.debate_engine import AnalysisInput, run_debate


def _has_key() -> bool:
    return any(
        os.environ.get(k)
        for k in ("LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY")
    )


def main() -> None:
    if not _has_key():
        print("⚠️  辩论引擎需要 LLM API key，请先配置：")
        print("    export OPENAI_API_KEY=sk-...")
        print("    export OPENAI_BASE_URL=https://api.openai.com/v1")
        print("详见 README「配置」章节。")
        return

    result = run_debate(
        AnalysisInput(
            ticker="600519",
            company_name="贵州茅台",
            trade_date="2026-08-31",
            market_report=(
                "示例技术面：现价 1480 元，50 日线 1520，200 日线 1400，"
                "短期跌破 50 日线，中长期仍在 200 日线上方。"
            ),
            sentiment_report=(
                "示例情绪面：近期舆情中性偏空，白酒板块整体承压，资金关注度下降。"
            ),
            news_report=(
                "示例宏观新闻：消费复苏节奏温和，高端白酒批价企稳，行业去库存接近尾声。"
            ),
            fundamentals_report=(
                "示例基本面：PE(TTM) 22x，处于近五年 15% 分位，ROE 稳定 25%+，股息率约 2%。"
            ),
        )
    )

    print("=" * 56)
    print(f"辩论结果 | {result.ticker} {result.trade_date}")
    print("=" * 56)
    print(f"评级      {result.rating.value}")
    print(f"目标价    {result.price_target}")
    print(f"持有期    {result.time_horizon}")
    print(f"信心      {result.confidence.value}")
    print(f"\n执行摘要\n{result.executive_summary}")
    print(f"\n投资论点\n{result.investment_thesis}")
    if result.key_risks:
        print("\n关键风险")
        for r in result.key_risks:
            print(f"  - {r}")


if __name__ == "__main__":
    main()
