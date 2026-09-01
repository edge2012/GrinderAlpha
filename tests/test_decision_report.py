"""DecisionReport schema + resolve_action 单元测试。

零第三方依赖（纯 assert），两种跑法：
    python3 tests/test_decision_report.py        # 直接跑（手动收集 test_ 函数）
    pytest tests/test_decision_report.py         # 装了 pytest 也能跑
"""

from investment.decision_report import (
    Action,
    DataSourceInfo,
    DecisionReport,
    DimensionCheck,
    TraceStep,
    format_report,
    resolve_action,
)


# ── Action 枚举 ──────────────────────────────────────────────────────

def test_action_has_seven_values():
    assert {a.value for a in Action} == {
        "BUY", "ADD", "HOLD", "TRIM", "EXIT", "WAIT", "REBUY"
    }


# ── resolve_action 优先级 ────────────────────────────────────────────

def test_resolve_single_action():
    assert resolve_action([Action.BUY]) == Action.BUY


def test_resolve_trim_over_add():
    """止盈压过加仓。"""
    assert resolve_action([Action.ADD, Action.TRIM]) == Action.TRIM


def test_resolve_exit_over_everything():
    """止损压过一切。"""
    assert resolve_action([Action.BUY, Action.ADD, Action.TRIM, Action.EXIT]) == Action.EXIT


def test_resolve_add_over_buy():
    """加仓压过建仓。"""
    assert resolve_action([Action.BUY, Action.ADD]) == Action.ADD


def test_resolve_rebuy_over_hold():
    assert resolve_action([Action.HOLD, Action.REBUY]) == Action.REBUY


def test_resolve_empty_no_position_wait():
    assert resolve_action([], has_position=False) == Action.WAIT


def test_resolve_empty_with_position_hold():
    assert resolve_action([], has_position=True) == Action.HOLD


# ── DecisionReport dataclass ─────────────────────────────────────────

def _minimal_report() -> DecisionReport:
    return DecisionReport(
        symbol="510300",
        market="CN",
        asset_class="etf",
        methodology="bottom_accelerator",
        methodology_label="ETF定投",
        maturity="完整",
        action=Action.ADD,
        confidence=8,
        conclusion="落入恐慌区，DCA 3x",
        price_zone="¥4.0-4.3（恐慌区）",
    )


def test_report_defaults():
    r = _minimal_report()
    assert r.dimensions == []
    assert r.trace == []
    assert r.data_sources == []
    assert r.risks == []
    assert r.warnings == []
    assert r.schema_version == "1.0"
    assert r.backtest_ref is None
    assert r.price_zone_low is None


def test_report_full_assembly():
    r = _minimal_report()
    r.dimensions = [
        DimensionCheck("大底趋势", True, "偏离 -18%", "偏离<-15%→恐慌区", "腾讯行情(实时)"),
    ]
    r.trace = [
        TraceStep("① 拉取历史大底", "BOTTOM_DEFINITIONS", "读常量", "[807...]"),
    ]
    r.data_sources = [
        DataSourceInfo("历史大底常量", "static", "2005-06 起"),
    ]
    assert len(r.dimensions) == 1
    assert len(r.trace) == 1
    assert r.dimensions[0].ok is True


# ── format_report ────────────────────────────────────────────────────

def test_format_report_contains_key_sections():
    r = _minimal_report()
    r.dimensions = [
        DimensionCheck("大底趋势", True, "偏离 -18%", "规则", "腾讯行情(实时)"),
    ]
    r.trace = [
        TraceStep("① 拉取历史大底", "输入", "规则", "输出"),
    ]
    r.data_sources = [
        DataSourceInfo("历史大底常量", "static", "2005-06 起"),
    ]
    out = format_report(r)
    assert "510300" in out
    assert "ADD" in out
    assert "落入恐慌区" in out
    assert "大底趋势" in out
    assert "① 拉取历史大底" in out
    assert "历史大底常量" in out


def test_format_report_false_dimension_shows_cross():
    r = _minimal_report()
    r.dimensions = [
        DimensionCheck("止损", False, "跌破 50MA", "规则", "腾讯日K"),
    ]
    out = format_report(r)
    assert "✗" in out


# ── 手动跑入口（零依赖，不用 pytest 也能跑）──────────────────────────

if __name__ == "__main__":
    import sys

    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
