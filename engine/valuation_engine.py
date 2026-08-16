#!/usr/bin/env python3
"""
Valuation Engine — 分类型估值引擎
===================================
为底部加速系统提供估值维度双确认。

设计原则:
1. 分类型 — 宽基/红利/行业/AI链/港股各用不同估值方法
2. 可扩展 — ETF和个股共用同一接口，个股方法预留
3. 旁路架构 — 不改bottom_accelerator核心逻辑
4. 数据降级 — 数据不可用时标注而非崩溃

数据源:
- PE: akshare stock_index_pe_lg (legulegu, 15-20年历史)
- PB: akshare stock_index_pb_lg (legulegu, 15-20年历史)
- 股息率: akshare stock_zh_index_value_csindex (中证指数, 1月历史)

ETF→指数映射:
  宽基: 510300→沪深300, 510050→上证50, 510500→中证500
  创业板/科创50: →创业板50(proxy)
  红利因子: 512890→上证红利(proxy), 510880→上证红利
  行业/AI链/港股: →中证500(proxy) 或 数据不足

估值方法分配:
  宽基: PE分位数(0.6) + PB分位数(0.4)
  红利因子: PE分位数(0.4) + 股息率判定(0.6)
  行业/AI链/港股: PB分位数(0.6) + PE分位数(0.4)
  个股: PE分位数(0.5) + PB分位数(0.5) — 预留

使用: python3 valuation_engine.py [etf_code]
"""

import sys
import os
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from enum import Enum
# Cache integration
VALUATION_CACHE_TTL = 86400  # 24h default for valuation data


def _get_cache():
    """Lazy-load DataCache to avoid import-time DB init."""
    from market_data_layer import DataCache
    return DataCache()


def _valuation_from_cache(etf_code: str):
    """Try to get valuation from cache. Returns ValuationResult or None."""
    try:
        cache = _get_cache()
        data, age = cache.get_cached_valuation(etf_code, max_age=VALUATION_CACHE_TTL)
        if data is None:
            return None
        return ValuationResult(
            etf_code=data.get("etf_code", etf_code),
            etf_name=data.get("etf_name", ""),
            category=data.get("category", ""),
            pe_percentile=data.get("pe_pct"),
            pb_percentile=data.get("pb_pct"),
            dividend_yield=data.get("div_yield"),
            composite_zone=ValZone(data.get("composite_zone", "insufficient")),
            composite_score=data.get("composite_score", 0),
            confidence=data.get("confidence", 5),
            data_sources=data.get("data_sources", {}),
            warnings=data.get("warnings", []),
        )
    except Exception:
        return None


def _valuation_to_cache(result) -> None:
    """Store valuation result to cache."""
    try:
        cache = _get_cache()
        data = {
            "etf_code": result.etf_code,
            "etf_name": result.etf_name,
            "category": result.category,
            "pe_pct": result.pe_percentile,
            "pb_pct": result.pb_percentile,
            "div_yield": result.dividend_yield,
            "composite_score": result.composite_score,
            "composite_zone": result.composite_zone.value,
            "confidence": result.confidence,
            "data_sources": result.data_sources,
            "warnings": result.warnings,
        }
        cache.cache_valuation(result.etf_code, data, ttl=VALUATION_CACHE_TTL)
    except Exception:
        pass  # cache write failure is non-fatal


class ValZone(Enum):
    CHEAP = "cheap"                   # <20% 分位
    SOMEWHAT_CHEAP = "somewhat_cheap"  # 20-40%
    NORMAL = "normal"                  # 40-60%
    SOMEWHAT_EXPENSIVE = "somewhat_expensive"  # 60-80%
    EXPENSIVE = "expensive"            # >80%
    INSUFFICIENT = "insufficient"      # 数据不足


@dataclass
class ValuationResult:
    """估值评估结果"""
    etf_code: str
    etf_name: str
    category: str
    pe_percentile: Optional[float] = None     # PE在历史上的百分位(0-100)
    pb_percentile: Optional[float] = None     # PB在历史上的百分位(0-100)
    dividend_yield: Optional[float] = None    # 当前股息率%
    dividend_percentile: Optional[float] = None  # 股息率百分位(越高=越便宜)
    composite_zone: ValZone = ValZone.INSUFFICIENT
    composite_score: float = 0.0              # 0-10, 越高越便宜
    confidence: int = 0                        # 1-10, 数据质量
    warnings: List[str] = field(default_factory=list)
    data_sources: Dict[str, str] = field(default_factory=dict)


# ─── Configuration ──────────────────────────────────────────────────

# ETF → (PE源名称, PB源名称, 股息率指数代码) 映射
ETF_VALUATION_MAP = {
    # 宽基 — 直接映射
    "510300": ("沪深300", "沪深300", "000300", "宽基"),
    "510050": ("上证50", "上证50", "000016", "宽基"),
    "510500": ("中证500", "中证500", "000905", "宽基"),
    # 创业板/科创50 — 创业板50 proxy
    "588000": ("创业板50", "创业板50", None, "宽基"),
    "159915": ("创业板50", "创业板50", None, "宽基"),
    # 红利因子
    "512890": ("上证红利", "上证红利", "000922", "因子"),
    "510880": ("上证红利", "上证红利", "000922", "因子"),
    # 行业/AI链 — 中证500 proxy (PB为主)
    "512480": ("中证500", "中证500", None, "行业"),
    "516160": ("中证500", "中证500", None, "行业"),
    "512010": ("中证500", "中证500", None, "行业"),
    "515880": ("中证500", "中证500", None, "AI链"),
    "515070": ("中证500", "中证500", None, "AI链"),
    "562500": ("中证500", "中证500", None, "AI链"),
    "159928": ("中证500", "中证500", None, "行业"),
    # 港股宽基 — 沪深300 proxy (跨市场，可信度低)
    "159920": ("沪深300", "沪深300", None, "宽基-港股"),
    "513130": ("沪深300", "沪深300", None, "宽基-港股"),
}

# 类别→估值方法权重
CATEGORY_VAL_METHODS = {
    "宽基": {"pe": 0.6, "pb": 0.4, "dividend": 0.0},
    "宽基-港股": {"pe": 0.4, "pb": 0.3, "dividend": 0.0},  # 跨市场proxy，降权
    "因子": {"pe": 0.3, "dividend": 0.7, "pb": 0.0},       # 红利以股息率为核心
    "行业": {"pb": 0.7, "pe": 0.3, "dividend": 0.0},        # PB为主（周期行业）
    "AI链": {"pb": 0.6, "pe": 0.4, "dividend": 0.0},
    "个股": {"pe": 0.5, "pb": 0.5, "dividend": 0.0},        # 预留
}

# 百分位→估值区映射
PERCENTILE_ZONES = [
    (20, ValZone.CHEAP, "历史低位"),
    (40, ValZone.SOMEWHAT_CHEAP, "偏低"),
    (60, ValZone.NORMAL, "正常"),
    (80, ValZone.SOMEWHAT_EXPENSIVE, "偏高"),
    (float("inf"), ValZone.EXPENSIVE, "历史高位"),
]


def _pct_to_zone(pct: float) -> Tuple[ValZone, str]:
    """百分位→估值区"""
    for threshold, zone, label in PERCENTILE_ZONES:
        if pct < threshold:
            return zone, label
    return ValZone.EXPENSIVE, "历史高位"


def _fetch_pe_history(pe_name: str):
    """从legulegu获取PE历史"""
    try:
        import akshare as ak
        df = ak.stock_index_pe_lg(symbol=pe_name)
        # 滚动市盈率 is the preferred PE metric
        pe_col = "滚动市盈率" if "滚动市盈率" in df.columns else "静态市盈率"
        values = df[pe_col].dropna().tolist()
        return values, df["日期"].iloc[0], df["日期"].iloc[-1]
    except Exception as e:
        return None, None, None


def _fetch_pb_history(pb_name: str):
    """从legulegu获取PB历史"""
    try:
        import akshare as ak
        df = ak.stock_index_pb_lg(symbol=pb_name)
        # PB列名通常是'市净率'或第二列数值列
        pb_col = None
        for col in df.columns:
            if "市净" in str(col) or "PB" in str(col).upper():
                pb_col = col
                break
        if pb_col is None:
            # 尝试找数值列
            num_cols = df.select_dtypes(include=["float64", "int64"]).columns
            pb_col = num_cols[-1] if len(num_cols) > 1 else num_cols[0]
        values = df[pb_col].dropna().tolist()
        return values, df["日期"].iloc[0], df["日期"].iloc[-1]
    except Exception as e:
        return None, None, None


def _fetch_dividend_info(csindex_code: str):
    """从中证指数获取当前股息率"""
    try:
        import akshare as ak
        df = ak.stock_zh_index_value_csindex(symbol=csindex_code)
        if df is None or len(df) == 0:
            return None
        last = df.iloc[-1]
        div1 = last.get("股息率1")
        div2 = last.get("股息率2")
        # 优先用股息率1
        return float(div1) if div1 else (float(div2) if div2 else None)
    except Exception:
        return None


def evaluate_etf(etf_code: str, etf_name: str = "", category: str = "") -> ValuationResult:
    """
    评估一只ETF的估值位置。

    Args:
        etf_code: 6位ETF代码 (如 "510300")
        etf_name: 可选，ETF名称
        category: 可选，类别覆盖（默认从映射表获取）

    Returns:
        ValuationResult
    """
    map_entry = ETF_VALUATION_MAP.get(etf_code)
    if map_entry is None:
        return ValuationResult(
            etf_code=etf_code, etf_name=etf_name,
            category=category or "未知",
            composite_zone=ValZone.INSUFFICIENT,
            warnings=["ETF未在估值映射表中"],
            confidence=0
        )

    pe_name, pb_name, csindex_code, mapped_cat = map_entry
    cat = category or mapped_cat
    methods = CATEGORY_VAL_METHODS.get(cat, CATEGORY_VAL_METHODS["宽基"])

    # ── Cache-first: try cache before hitting slow akshare ──
    cached = _valuation_from_cache(etf_code)
    if cached is not None:
        # Populate name/category if caller provided them
        if etf_name:
            cached.etf_name = etf_name
        if category:
            cached.category = category
        return cached

    result = ValuationResult(
        etf_code=etf_code,
        etf_name=etf_name,
        category=cat,
        data_sources={}
    )
    scores = []  # (score, weight) tuples
    confidence_deductions = 0

    # ── PE Percentile ──
    if methods.get("pe", 0) > 0:
        pe_vals, pe_start, pe_end = _fetch_pe_history(pe_name)
        if pe_vals and len(pe_vals) > 100:
            current_pe = pe_vals[-1]
            pct = sum(1 for v in pe_vals if v < current_pe) / len(pe_vals) * 100
            result.pe_percentile = round(pct, 1)
            zone, label = _pct_to_zone(pct)
            # 低PE=便宜, 所以用(100-pct)映射到0-10分
            pe_score = round((100 - pct) / 10, 1)
            scores.append((pe_score, methods["pe"]))
            result.data_sources["PE"] = f"legulegu {pe_name} ({pe_start}~{pe_end}, {len(pe_vals)}条)"
        else:
            result.warnings.append(f"PE数据不可用({pe_name})")
            confidence_deductions += 3

    # ── PB Percentile ──
    if methods.get("pb", 0) > 0:
        pb_vals, pb_start, pb_end = _fetch_pb_history(pb_name)
        if pb_vals and len(pb_vals) > 100:
            current_pb = pb_vals[-1]
            pct = sum(1 for v in pb_vals if v < current_pb) / len(pb_vals) * 100
            result.pb_percentile = round(pct, 1)
            pb_score = round((100 - pct) / 10, 1)
            scores.append((pb_score, methods["pb"]))
            result.data_sources["PB"] = f"legulegu {pb_name} ({pb_start}~{pb_end}, {len(pb_vals)}条)"
        else:
            result.warnings.append(f"PB数据不可用({pb_name})")
            confidence_deductions += 3

    # ── Dividend Yield ──
    if methods.get("dividend", 0) > 0 and csindex_code:
        div = _fetch_dividend_info(csindex_code)
        if div is not None:
            result.dividend_yield = round(div, 2)
            # 股息率越高越便宜，但需要历史区间来判断
            # 红利类ETF股息率通常在2-6%
            if cat == "因子":
                if div >= 5.0:
                    div_score = 9.0
                elif div >= 4.5:
                    div_score = 7.0
                elif div >= 4.0:
                    div_score = 5.0
                elif div >= 3.5:
                    div_score = 3.0
                else:
                    div_score = 1.0
            else:
                # 非红利类，股息率权重低
                div_score = min(div / 0.5, 10)  # 2% = 4分
            scores.append((div_score, methods["dividend"]))
            result.data_sources["股息率"] = f"中证指数 {csindex_code}"
            result.dividend_percentile = round(div_score * 10, 1)
        else:
            result.warnings.append(f"股息率数据不可用({csindex_code})")
            confidence_deductions += 2

    # ── Composite Score ──
    if not scores:
        result.composite_zone = ValZone.INSUFFICIENT
        result.confidence = max(1, 10 - confidence_deductions)
        return result

    total_weight = sum(w for _, w in scores)
    if total_weight > 0:
        composite = sum(s * w for s, w in scores) / total_weight
    else:
        composite = scores[0][0]

    result.composite_score = round(composite, 1)
    result.composite_zone, _ = _pct_to_zone(100 - composite * 10)

    # Confidence
    result.confidence = max(1, min(10, 10 - confidence_deductions))
    if "proxy" in str(pe_name).lower() or "proxy" in str(pb_name).lower():
        result.confidence = max(1, result.confidence - 2)
        result.warnings.append("使用代理指数，估值判断仅供参考")

    # ── Write-through cache: store successful valuation ──
    _valuation_to_cache(result)

    return result


def evaluate_stock(ticker: str, pe: Optional[float] = None,
                   pb: Optional[float] = None) -> ValuationResult:
    """
    评估个股估值 — 预留接口。

    当前返回INSUFFICIENT，等后续接入个股PE/PB数据源后实现。
    """
    return ValuationResult(
        etf_code=ticker,
        etf_name="",
        category="个股",
        composite_zone=ValZone.INSUFFICIENT,
        warnings=["个股估值暂未实现，需接入财报/行业对比数据"],
        confidence=1
    )


def format_valuation_line(result: ValuationResult) -> str:
    """格式化单行估值输出（用于扫描器表格）"""
    if result.composite_zone == ValZone.INSUFFICIENT:
        return "数据不足"

    zone_emoji = {
        ValZone.CHEAP: "🟢",
        ValZone.SOMEWHAT_CHEAP: "🟢",
        ValZone.NORMAL: "⚪",
        ValZone.SOMEWHAT_EXPENSIVE: "🟡",
        ValZone.EXPENSIVE: "🔴",
    }

    emoji = zone_emoji.get(result.composite_zone, "❓")
    pe_str = f"PE{result.pe_percentile:.0f}%" if result.pe_percentile is not None else ""
    pb_str = f"PB{result.pb_percentile:.0f}%" if result.pb_percentile is not None else ""
    div_str = f"息{result.dividend_yield:.1f}%" if result.dividend_yield else ""

    detail = "/".join(filter(None, [pe_str, pb_str, div_str]))
    return f"{emoji}{result.composite_score:.0f}分 {detail}"


def format_valuation_detail(result: ValuationResult) -> str:
    """格式化详细估值输出"""
    lines = []
    lines.append(f"  📊 {result.etf_code} {result.etf_name} ({result.category})")
    lines.append(f"     综合评分: {result.composite_score}/10 "
                 f"→ {result.composite_zone.value} "
                 f"(置信度: {result.confidence}/10)")

    if result.pe_percentile is not None:
        lines.append(f"     PE分位: {result.pe_percentile:.0f}% "
                     f"(当前PE在历史第{result.pe_percentile:.0f}百分位)")
    if result.pb_percentile is not None:
        lines.append(f"     PB分位: {result.pb_percentile:.0f}%")
    if result.dividend_yield is not None:
        lines.append(f"     股息率: {result.dividend_yield:.2f}%")

    if result.warnings:
        for w in result.warnings:
            lines.append(f"     ⚠️ {w}")

    return "\n".join(lines)


# ─── CLI ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1:
        codes = sys.argv[1:]
    else:
        # 默认输出核心ETF
        codes = ["510300", "510050", "512890", "510880"]

    for code in codes:
        entry = ETF_VALUATION_MAP.get(code)
        name = ""
        if entry:
            name = entry[0]

        result = evaluate_etf(code, name)
        print(format_valuation_detail(result))
        print()
