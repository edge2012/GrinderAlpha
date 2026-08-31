"""DecisionReport demo — 用示例数据组装一份完整决策报告，并演示动作裁决。

运行：python engine/examples/demo_decision_report.py
（零依赖，不需要联网、不需要 key）

这个 demo 展示 DecisionReport 的「三合一」：
  1. 结论层（动作 + 信心 + 区间）
  2. 证据层（每个维度一条，标清规则 + 数据源）
  3. 推导链（摊开算——每步「输入哪来 / 规则怎么判 / 判成什么」）
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from investment.decision_report import (
    Action,
    DataSourceInfo,
    DecisionReport,
    DimensionCheck,
    TraceStep,
    format_report,
    resolve_action,
)


def build_etf_dca_report() -> DecisionReport:
    """组装一份 ETF 定投（沪深300 510300）的决策报告示例。"""
    return DecisionReport(
        symbol="510300",
        market="CN",
        asset_class="etf",
        methodology="bottom_accelerator",
        methodology_label="ETF定投",
        maturity="完整",

        action=Action.ADD,
        confidence=8,
        conclusion="沪深300 距历史底部趋势线 -18% → 落入「恐慌区」，DCA 倍率 3x，估值确认 upgraded",
        price_zone="¥4.0-4.3（恐慌区）",
        price_zone_low=4.0,
        price_zone_high=4.3,

        dimensions=[
            DimensionCheck(
                name="大底趋势", ok=True,
                detail="现价折算指数 4166，趋势线投影 5080，偏离 -18%",
                rule="偏离趋势线 -18% → 恐慌区",
                data_source="腾讯行情(实时)",
            ),
            DimensionCheck(
                name="估值", ok=True,
                detail="指数 PE 分位 28% → 不贵",
                rule="估值确认 upgraded",
                data_source="akshare",
            ),
            DimensionCheck(
                name="仓位纪律", ok=True,
                detail="单标的占比 12% < 15% 纪律线",
                rule="未超 15%",
                data_source="用户持仓",
            ),
            DimensionCheck(
                name="止损", ok=True,
                detail="距 50MA -3% → 建仓期仅展示不拦截",
                rule="建仓期不拦截",
                data_source="腾讯日K",
            ),
        ],

        trace=[
            TraceStep("① 拉取历史大底", "BOTTOM_DEFINITIONS[\"沪深300\"]", "读历史大底常量",
                      "[807(2005-06), 2836(2014-06)...]"),
            TraceStep("② 拟合趋势线", "上一步的底点", "对数线性拟合",
                      "年化 +8.1%, r²=0.98"),
            TraceStep("③ 投影当前", "slope + intercept", "趋势线外推",
                      "投影 5080 点"),
            TraceStep("④ 拉取实时价", "腾讯 qt.gtimg.cn", "实时行情",
                      "¥4.20（折算指数 4166）"),
            TraceStep("⑤ 判定区域", "4166 vs 5080", "(4166-5080)/5080 = -18%",
                      "落入恐慌区 → DCA 3x"),
        ],

        backtest_ref="entry_signal --symbol sh000300",
        as_of="2026-08-31",
        data_sources=[
            DataSourceInfo("历史大底常量", "static", "2005-06 起"),
            DataSourceInfo("腾讯行情", "realtime", "now"),
            DataSourceInfo("akshare 估值", "realtime", "now"),
        ],
    )


def demo_resolve_action() -> None:
    """演示 action 优先级裁决（三条铁律）。"""
    print("=" * 56)
    print("动作裁决演示（优先级 EXIT>TRIM>ADD>BUY>REBUY>HOLD/WAIT）")
    print("=" * 56)

    cases = [
        ([Action.ADD, Action.TRIM], "止盈压过加仓（先落袋不追高）"),
        ([Action.ADD, Action.EXIT], "止损压过一切（风控优先）"),
        ([Action.BUY, Action.ADD], "加仓压过建仓（有持仓优先加）"),
        ([Action.BUY], "单信号直接采用"),
        ([], "无信号 + 有持仓 → HOLD"),
        ([], "无信号 + 无持仓 → WAIT"),
    ]
    for candidates, note in cases:
        has_position = (note.endswith("HOLD"))
        result = resolve_action(candidates, has_position=has_position)
        sigs = "+".join(c.value for c in candidates) or "无信号"
        print(f"  {sigs:<14} → {result.value:<5}  ({note})")


def main() -> None:
    report = build_etf_dca_report()
    print(format_report(report))
    print()
    demo_resolve_action()


if __name__ == "__main__":
    main()
