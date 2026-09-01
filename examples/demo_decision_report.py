#!/usr/bin/env python3
"""DecisionReport demo — 对任意 ETF 跑真实底部加速分析，组装成决策报告。

用法：
    python3 examples/demo_decision_report.py                  # 真实跑 510300（联网）
    python3 examples/demo_decision_report.py --symbol 510050  # 上证50
    python3 examples/demo_decision_report.py --symbol 159915  # 创业板指

零依赖的 DecisionReport schema（结论 + 证据 + 推导链）。
默认拉真实数据（腾讯实时价 + 估值），由 bottom_accelerator 真实计算；
离线/无数据时回退到内置示例（510300），保证任何环境都能看到报告结构。

注意：DCA 加速的 zone 阈值来自策略参数（strategy_params.example.json 的
bottom_accelerator.zones），示例参数下为空 → 恒 1x。填入自己的 zones 后，
DCA 倍率才会随偏离深度变化。
"""

import argparse
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
)
from investment.data_access import get_data_access
from bottom_accelerator import analyze_etf_combined
from valuation_engine import ValZone

# ETF → 回测指数代码（用于 backtest_ref 提示）
_BACKTEST_INDEX = {
    "510300": "sh000300",  # 沪深300
    "510050": "sh000001",  # 上证50
    "510500": "sh000905",  # 中证500
    "159915": "sz399006",  # 创业板指
}


def _build_example_report() -> DecisionReport:
    """内置示例（写死 510300），离线/无数据时仍能看到完整报告结构。"""
    return DecisionReport(
        symbol="510300",
        market="CN",
        asset_class="etf",
        methodology="bottom_accelerator",
        methodology_label="ETF定投",
        maturity="完整",
        action=Action.ADD,
        confidence=8,
        conclusion="沪深300 距历史底部趋势线 -18% → 落入「恐慌区」，DCA 倍率 3x（示例数据）",
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


def _map_result(result: dict) -> DecisionReport:
    """把 analyze_etf_combined 的 dict 映射成 DecisionReport（demo 级聚合）。"""
    symbol = result["etf_code"]
    index_name = result["index_name"]
    deviation = result["deviation_pct"]
    projected = result["projected_bottom"]
    price_in_index = result["price_in_index_terms"]
    dca = result.get("dca_adjusted") or result["dca_multiplier"]
    zone_name = result["zone"]["zone_name"]

    # action（demo 级简单规则，正式判断见 M2 聚合层）
    if deviation < -15:
        action = Action.ADD
    elif deviation < 0:
        action = Action.BUY
    else:
        action = Action.WAIT

    conclusion = (
        f"{index_name} 距历史底部趋势线 {deviation:+.0f}% → "
        f"「{zone_name}」，DCA 倍率 {dca:g}x"
    )

    dimensions = [
        DimensionCheck(
            name="大底趋势",
            ok=deviation < 0,
            detail=f"现价折算指数 {price_in_index}，趋势线投影 {projected}，偏离 {deviation:+.0f}%",
            rule=f"偏离趋势线 {deviation:+.0f}% → {zone_name}",
            data_source="腾讯行情(实时)",
        ),
    ]

    val = result.get("valuation")
    data_sources = [
        DataSourceInfo("历史大底常量", "static", "2005-06 起"),
        DataSourceInfo("腾讯行情", "realtime", "now"),
    ]
    if val is not None and val.composite_zone != ValZone.INSUFFICIENT:
        cheap = val.composite_zone in (ValZone.CHEAP, ValZone.SOMEWHAT_CHEAP)
        pe_txt = f"{val.pe_percentile:.0f}%" if val.pe_percentile is not None else "N/A"
        dimensions.append(DimensionCheck(
            name="估值",
            ok=cheap,
            detail=f"指数 PE 分位 {pe_txt} → {val.composite_zone.value}",
            rule=f"估值确认 {result.get('confirmation')}",
            data_source="akshare",
        ))
        data_sources.append(DataSourceInfo("akshare 估值", "realtime", "now"))

    tr = result.get("trace", {})
    bottoms = tr.get("bottoms", [])
    fit = tr.get("fit", {})
    trace = [
        TraceStep("① 拉取历史大底", f"BOTTOM_DEFINITIONS[{index_name}]", "读历史大底常量",
                  str([f"{v}({d})" for d, v in bottoms])),
        TraceStep("② 拟合趋势线", "上一步的底点", "对数线性拟合",
                  f"年化 +{fit.get('annual_growth_pct')}%, r²={fit.get('r_squared')}"),
        TraceStep("③ 投影当前", "slope + intercept", "趋势线外推",
                  f"投影 {fit.get('projected_now')} 点"),
        TraceStep("④ 拉取实时价", "腾讯 qt.gtimg.cn", "实时行情",
                  f"¥{result['current_price']:.2f}（折算指数 {price_in_index}）"),
        TraceStep("⑤ 判定区域", f"{price_in_index} vs {projected}",
                  f"({price_in_index}-{projected})/{projected} = {deviation:+.0f}%",
                  f"落入{zone_name} → DCA {dca:g}x"),
    ]

    backtest_index = _BACKTEST_INDEX.get(symbol)
    backtest_ref = f"entry_signal --symbol {backtest_index}" if backtest_index else None

    return DecisionReport(
        symbol=symbol,
        market="CN",
        asset_class="etf",
        methodology="bottom_accelerator",
        methodology_label="ETF定投",
        maturity="完整",
        action=action,
        confidence=8,
        conclusion=conclusion,
        price_zone=f"¥{result['current_price']:.2f}（{zone_name}）",
        dimensions=dimensions,
        trace=trace,
        data_sources=data_sources,
        backtest_ref=backtest_ref,
        as_of="now",
    )


def build_report(symbol: str) -> DecisionReport:
    """真实跑指定 ETF；离线/无数据/无底部趋势线时回退示例。"""
    try:
        da = get_data_access()
        price = da.get_quote(symbol, "A")
        if price is None:
            return _build_example_report()
        result = analyze_etf_combined(symbol, price)
        if result is None:
            return _build_example_report()
        return _map_result(result)
    except Exception:
        return _build_example_report()


def main() -> None:
    p = argparse.ArgumentParser(description="ETF 定投决策报告（真实数据）")
    p.add_argument("--symbol", default="510300",
                   help="ETF 代码，如 510300 / 510050 / 510500 / 159915")
    a = p.parse_args()

    report = build_report(a.symbol)
    print(format_report(report))
    print(f"（symbol={a.symbol}，实时数据；离线/无数据时回退内置示例）")


if __name__ == "__main__":
    main()
