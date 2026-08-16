#!/usr/bin/env python3
"""
宏观数据管道 v2.0 — 全量采集 + 回测验证

数据源：
  - 东方财富 datacenter: PMI / CPI / PPI / GDP
  - AKShare: M2 / 社融 / 新增信贷 / CN10Y / US10Y / 中美利差

模式：
  python3 macro_pipeline.py              # 默认：采集+保存
  python3 macro_pipeline.py --backtest   # 采集+全量回测
  python3 macro_pipeline.py --json       # 采集+JSON输出

输出：
  ~/.hermes/data/macro.db          — SQLite 时序数据库
  ~/.hermes/data/macro_state.json  — 最新状态快照
  ~/.hermes/data/macro_backtest.json — 回测结果
"""

import json

# 策略参数外部化（不入 Git）
try:
    from strategy_param_loader import get_params as _get_strategy_params
    _SP = _get_strategy_params()
    if _SP is None:
        import logging as _logging
        _logging.getLogger("macro_pipeline").error("策略参数加载失败，使用硬编码回退值")
except ImportError:
    _SP = None
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime
from typing import Optional

import akshare as ak

# ─── 配置 ────────────────────────────────────────────

_HERMES_HOME = os.path.expanduser("~/.hermes")
MACRO_DB = os.path.join(_HERMES_HOME, "data", "macro.db")
STATE_FILE = os.path.join(_HERMES_HOME, "data", "macro_state.json")
BACKTEST_FILE = os.path.join(_HERMES_HOME, "data", "macro_backtest.json")
REQUEST_TIMEOUT = 15

EASTMONEY_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"

# ─── Part 1: 东方财富数据源 ──────────────────────────

def _em_api(report_name: str, page_size: int = 200) -> list:
    url = (f"{EASTMONEY_URL}?reportName={report_name}&columns=ALL"
           f"&pageNumber=1&pageSize={page_size}"
           f"&sortTypes=-1&sortColumns=REPORT_DATE"
           f"&source=WEB&client=WEB")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [ERR] {report_name}: {e}", file=sys.stderr)
        return []
    if not data.get("success"):
        return []
    return data["result"]["data"]


def fetch_pmi() -> list[dict]:
    rows = _em_api("RPT_ECONOMY_PMI")
    out = []
    for r in rows:
        d = str(r.get("REPORT_DATE", ""))[:7]
        make_idx = r.get("MAKE_INDEX")
        nmake_idx = r.get("NMAKE_INDEX")
        try:
            out.append({"date": d, "pmi_mfg": float(make_idx) if make_idx else None,
                        "pmi_nonmfg": float(nmake_idx) if nmake_idx else None})
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda x: x["date"])
    return out


def fetch_cpi() -> list[dict]:
    rows = _em_api("RPT_ECONOMY_CPI")
    out = []
    for r in rows:
        d = str(r.get("REPORT_DATE", ""))[:7]
        try:
            out.append({"date": d,
                "cpi_yoy": float(r["NATIONAL_SAME"]) if r.get("NATIONAL_SAME") else None,
                "cpi_mom": float(r["NATIONAL_SEQUENTIAL"]) if r.get("NATIONAL_SEQUENTIAL") else None})
        except (ValueError, TypeError, KeyError):
            continue
    out.sort(key=lambda x: x["date"])
    return out


def fetch_ppi() -> list[dict]:
    rows = _em_api("RPT_ECONOMY_PPI")
    out = []
    for r in rows:
        d = str(r.get("REPORT_DATE", ""))[:7]
        ppi = None
        for key in ["BASE_SAME", "MAKE_INDEX", "PPI_SAME", "SAME_RATIO", "NATIONAL_SAME"]:
            if r.get(key) and r[key] != "--":
                try:
                    ppi = float(r[key])
                    break
                except (ValueError, TypeError):
                    continue
        out.append({"date": d, "ppi_yoy": ppi})
    out.sort(key=lambda x: x["date"])
    return out


def fetch_gdp() -> list[dict]:
    rows = _em_api("RPT_ECONOMY_GDP")
    out = []
    for r in rows:
        d = str(r.get("REPORT_DATE", ""))[:7]
        gdp = None
        # GDP fields: SUM_SAME = 累计同比增速（主要用这个）
        for key in ["SUM_SAME", "GDP_SAME", "ACCUMULATE_SAME", "SAME_RATIO"]:
            if r.get(key) and r[key] != "--":
                try:
                    gdp = float(r[key])
                    break
                except (ValueError, TypeError):
                    continue
        out.append({"date": d, "gdp_yoy": gdp})
    out.sort(key=lambda x: x["date"])
    return out


# ─── Part 2: AKShare 数据源 ──────────────────────────

def fetch_m2_akshare() -> list[dict]:
    """M2货币供应量 — 月度数据 2008+"""
    try:
        df = ak.macro_china_money_supply()
        out = []
        for _, row in df.iterrows():
            month_str = str(row["月份"])
            # Parse "2008年01月份" → "2008-01"
            parts = month_str.replace("年", "-").replace("月份", "").split("-")
            if len(parts) == 2:
                d = f"{parts[0]}-{parts[1].zfill(2)}"
            else:
                continue
            try:
                out.append({
                    "date": d,
                    "m2_amount": float(row["货币和准货币(M2)-数量(亿元)"]),
                    "m2_yoy": float(row["货币和准货币(M2)-同比增长"]),
                    "m1_yoy": float(row["货币(M1)-同比增长"]),
                })
            except (ValueError, KeyError):
                continue
        out.sort(key=lambda x: x["date"])
        return out
    except Exception as e:
        print(f"  [ERR] M2: {e}", file=sys.stderr)
        return []


def fetch_social_financing() -> list[dict]:
    """社会融资规模增量 — 月度数据 ~2014+"""
    try:
        df = ak.macro_china_shrzgm()
        out = []
        for _, row in df.iterrows():
            month_str = str(row["月份"])
            # Parse "202601" → "2026-01"
            if len(month_str) == 6:
                d = f"{month_str[:4]}-{month_str[4:]}"
            else:
                continue
            try:
                out.append({
                    "date": d,
                    "shrz_total": float(row["社会融资规模增量"]),
                    "shrz_rmb_loan": float(row["其中-人民币贷款"]),
                    "shrz_bond": float(row["其中-企业债券"]) if (row.get("其中-企业债券") is not None and not (isinstance(row.get("其中-企业债券"), float) and row.get("其中-企业债券") != row.get("其中-企业债券"))) else None,
                })
            except (ValueError, KeyError):
                continue
        out.sort(key=lambda x: x["date"])
        return out
    except Exception as e:
        print(f"  [ERR] 社融: {e}", file=sys.stderr)
        return []


def fetch_new_credit() -> list[dict]:
    """新增人民币贷款 — 月度数据 2008+"""
    try:
        df = ak.macro_china_new_financial_credit()
        out = []
        for _, row in df.iterrows():
            month_str = str(row["月份"])
            parts = month_str.replace("年", "-").replace("月份", "").split("-")
            if len(parts) == 2:
                d = f"{parts[0]}-{parts[1].zfill(2)}"
            else:
                continue
            try:
                out.append({
                    "date": d,
                    "loan_new": float(row["当月"]),
                    "loan_yoy": float(row["当月-同比增长"]),
                })
            except (ValueError, KeyError):
                continue
        out.sort(key=lambda x: x["date"])
        return out
    except Exception as e:
        print(f"  [ERR] 信贷: {e}", file=sys.stderr)
        return []


def fetch_cn_us_spread() -> list[dict]:
    """中美利差日频 → 月均"""
    try:
        df = ak.bond_zh_us_rate()
        out = []
        for _, row in df.iterrows():
            date_str = str(row["日期"])[:10]
            try:
                cn10 = float(row["中国国债收益率10年"])
                us10 = float(row["美国国债收益率10年"])
                spread = cn10 - us10
                out.append({
                    "date": date_str,
                    "cn_10y": cn10,
                    "us_10y": us10,
                    "spread": spread,
                })
            except (ValueError, KeyError):
                continue
        out.sort(key=lambda x: x["date"])
        return out
    except Exception as e:
        print(f"  [ERR] 中美利差: {e}", file=sys.stderr)
        return []


# ─── Part 3: 数据库 ──────────────────────────────────

def init_db():
    conn = sqlite3.connect(MACRO_DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS macro_data (
        indicator TEXT, date TEXT, value REAL, extra TEXT,
        fetched_at TEXT, PRIMARY KEY (indicator, date))""")
    conn.commit()
    return conn


def save_series(conn, indicator: str, data: list, value_key: str):
    now = datetime.now().isoformat()
    for item in data:
        val = item.get(value_key)
        if val is None:
            continue
        extra = json.dumps({k: v for k, v in item.items()
                           if k not in ("date", value_key)},
                          ensure_ascii=False)
        conn.execute("INSERT OR REPLACE INTO macro_data VALUES (?,?,?,?,?)",
                     (indicator, item["date"], val, extra, now))
    conn.commit()


# ─── Part 4: 状态构建 ─────────────────────────────────

def classify_profit_cycle(pmi: list[dict], m2: list[dict],
                          shrz: list[dict], spread: list[dict]) -> dict:
    """多层因子综合判断利润周期"""
    if not pmi:
        return {"phase": "unknown", "confidence": "low"}

    recent = [d for d in pmi[-6:] if d.get("pmi_mfg")]
    if len(recent) < 3:
        return {"phase": "unknown", "confidence": "low"}

    latest = recent[-1]["pmi_mfg"]
    trend_3m = latest - recent[0]["pmi_mfg"]
    avg_3m = sum(d["pmi_mfg"] for d in recent[-3:]) / 3

    # M2趋势
    m2_recent = [d["m2_yoy"] for d in m2[-3:]] if m2 else []
    m2_trend = m2_recent[-1] - m2_recent[0] if len(m2_recent) >= 3 else 0

    # 社融趋势
    shrz_recent = [d["shrz_total"] for d in shrz[-3:]] if shrz else []
    shrz_trend = sum(shrz_recent) / len(shrz_recent) if shrz_recent else 0

    # 最新利差
    spread_latest = spread[-1]["spread"] if spread else None

    # 综合判断
    if latest > 50.5 and trend_3m > 0.3:
        phase = "expansion"
    elif latest > 50.5:
        phase = "peaking"
    elif latest < 49.5 and trend_3m < -0.3:
        phase = "contraction"
    elif latest < 49.5:
        phase = "bottoming"
    else:
        phase = "neutral"

    return {
        "phase": phase,
        "confidence": "high" if len(recent) >= 6 else "medium",
        "pmi_latest": latest,
        "pmi_3m_avg": round(avg_3m, 1),
        "pmi_trend": round(trend_3m, 1),
        "m2_trend": round(m2_trend, 1) if m2_recent else None,
        "cn_us_spread": round(spread_latest, 2) if spread_latest else None,
        "pmi_history": [{"date": d["date"], "value": d["pmi_mfg"]} for d in recent[-6:]],
    }


def build_state(pmi, cpi, ppi, gdp, m2, shrz, spread) -> dict:
    cycle = classify_profit_cycle(pmi, m2, shrz, spread)

    labels = {"expansion": "🟢 扩张期", "peaking": "🟡 见顶",
              "contraction": "🔴 收缩期", "bottoming": "🔵 触底",
              "neutral": "⚪ 中性", "unknown": "❓ 数据不足"}

    return {
        "updated": datetime.now().isoformat(),
        "profit_cycle": {
            "phase": cycle["phase"],
            "phase_label": labels.get(cycle["phase"], "?"),
            "confidence": cycle["confidence"],
            "pmi_mfg_latest": cycle["pmi_latest"],
            "pmi_mfg_3m_avg": cycle["pmi_3m_avg"],
            "pmi_mfg_trend": cycle["pmi_trend"],
            "pmi_history": cycle["pmi_history"],
        },
        "inflation": {
            "cpi_yoy": cpi[-1]["cpi_yoy"] if cpi else None,
            "ppi_yoy": ppi[-1]["ppi_yoy"] if ppi else None,
        },
        "liquidity": {
            "m2_yoy": m2[-1]["m2_yoy"] if m2 else None,
            "m1_yoy": m2[-1]["m1_yoy"] if m2 else None,
            "cn_us_spread": cycle["cn_us_spread"],
            "cn_10y": spread[-1]["cn_10y"] if spread else None,
            "us_10y": spread[-1]["us_10y"] if spread else None,
        },
        "credit": {
            "shrz_total_latest": shrz[-1]["shrz_total"] if shrz else None,
        } if shrz else {},
        "dca_signal": compute_dca_signal(
            ppi[-1]["ppi_yoy"] if ppi else None,
            m2[-1]["m2_yoy"] if m2 else None,
        ),
        "interpretation": _make_interpretation(cycle),
    }



def compute_dca_signal(ppi_yoy: float, m2_yoy: float) -> dict:
    """基于回测阈值计算DCA倍率信号
    
    回测支撑（2010-2026，197-198月观测）：
    - PPI: bottom_33pct=XX, top_67pct=XX (see config)
      PPI底→A股6月+5.3%胜率64%  |  PPI顶→A股6月-3.5%胜率30%  ← 最强指标
    - M2:  bottom_33pct=XX, top_67pct=XX (see config)
      M2放缓→A股6月+3.1%胜率60%  |  M2加速→A股6月+1.1%胜率43%
    
    优先级：PPI > M2（PPI spread 8.8pp > M2 spread 3.0pp）
    方向冲突时 PPI 主导，M2 调半级。
    """
    mp = _SP["macro_pipeline"] if _SP else {}
    PPI_BOTTOM = mp.get("ppi_bottom", 999.0)  # NO FALLBACK
    PPI_TOP = mp.get("ppi_top", -999.0)  # NO FALLBACK
    M2_BOTTOM = mp.get("m2_bottom", 999.0)  # NO FALLBACK
    M2_TOP = mp.get("m2_top", -999.0)  # NO FALLBACK
    
    ppi_low = ppi_yoy is not None and ppi_yoy <= PPI_BOTTOM
    ppi_high = ppi_yoy is not None and ppi_yoy >= PPI_TOP
    m2_low = m2_yoy is not None and m2_yoy <= M2_BOTTOM
    m2_high = m2_yoy is not None and m2_yoy >= M2_TOP
    
    # ── 五级倍率 ──
    # 1.5x: PPI低位 + M2低位 = 双重最佳
    if ppi_low and m2_low:
        return {
            "multiplier": mp.get("dca_multipliers", {}).get("ppi_low_m2_low", 0.0),  # NO FALLBACK
            "signal": "加速", "label": "🟢 加速建仓",
            "reason": "PPI低位(6月胜率64%)+M2放缓(6月胜率60%)→双重最佳买点",
            "ppi_zone": "low", "m2_zone": "low",
        }
    
    # 1.25x: PPI低位 + M2中性 = PPI主导利好
    if ppi_low and not m2_high:
        return {
            "multiplier": mp.get("dca_multipliers", {}).get("ppi_low_m2_mid", 0.0),  # NO FALLBACK
            "signal": "偏快", "label": "🟢 偏快建仓",
            "reason": "PPI低位(6月胜率64%)→最佳单指标信号",
            "ppi_zone": "low", "m2_zone": "mid",
        }
    
    # 1.25x: PPI中性 + M2低位 = M2独立利好
    if not ppi_high and m2_low:
        return {
            "multiplier": mp.get("dca_multipliers", {}).get("ppi_mid_m2_low", 0.0),  # NO FALLBACK
            "signal": "偏快", "label": "🟢 偏快建仓",
            "reason": "M2放缓(6月胜率60%)→流动性改善信号",
            "ppi_zone": "mid", "m2_zone": "low",
        }
    
    # 1.0x: 两者中性，或方向抵消后净中性
    if not ppi_high and not ppi_low and not m2_high and not m2_low:
        return {
            "multiplier": mp.get("dca_multipliers", {}).get("ppi_mid_m2_mid", 0.0),  # NO FALLBACK
            "signal": "正常", "label": "⚪ 正常节奏",
            "reason": "PPI和M2均在历史中位区间",
            "ppi_zone": "mid", "m2_zone": "mid",
        }
    
    # 0.75x: PPI高位 + M2低位 = PPI危险但M2缓冲（当前状态）
    if ppi_high and m2_low:
        return {
            "multiplier": mp.get("dca_multipliers", {}).get("ppi_top_m2_low", 0.0),  # NO FALLBACK
            "signal": "偏慢", "label": "🟡 偏慢建仓",
            "reason": "PPI高位(6月胜率仅30%)←主导信号；M2放缓部分缓冲但不反转",
            "ppi_zone": "high", "m2_zone": "low",
        }
    
    # 0.5x: PPI高位 + M2高位 = 双重最差（必须最先检查）
    if ppi_high and m2_high:
        return {
            "multiplier": mp.get("dca_multipliers", {}).get("ppi_top_m2_high", 0.0),  # NO FALLBACK
            "signal": "减速", "label": "🔴 减速建仓",
            "reason": "PPI高位+M2高位→双重危险，历史上6月胜率<30%",
            "ppi_zone": "high", "m2_zone": "high",
        }
    
    # 0.75x: PPI高位 + M2中性
    if ppi_high and not m2_low and not m2_high:
        return {
            "multiplier": mp.get("dca_multipliers", {}).get("ppi_top_m2_mid", 0.0),  # NO FALLBACK
            "signal": "偏慢", "label": "🟡 偏慢建仓",
            "reason": "PPI高位(6月胜率仅30%)→历史最危险宏观环境",
            "ppi_zone": "high", "m2_zone": "mid",
        }
    
    # 0.75x: PPI中性 + M2高位
    if not ppi_low and not ppi_high and m2_high:
        return {
            "multiplier": mp.get("dca_multipliers", {}).get("ppi_mid_m2_high", 0.0), "signal": "偏慢", "label": "🟡 偏慢建仓",
            "reason": "M2高位(6月胜率43%)→流动性收紧信号",
            "ppi_zone": "mid", "m2_zone": "high",
        }
    
    # Fallback
    return {
        "multiplier": mp.get("dca_multipliers", {}).get("default", 0.0),  # NO FALLBACK
            "signal": "正常", "label": "⚪ 正常节奏",
        "reason": "数据不足，维持基准",
        "ppi_zone": "unknown", "m2_zone": "unknown",
    }


def _make_interpretation(cycle: dict) -> str:
    phase = cycle["phase"]
    base = {
        "expansion": "利润周期扩张。PMI>50.5且向上。回测显示扩张期后续6月均值+2.2%，胜率50%——不如收缩期。",
        "peaking": "利润周期高位但有见顶迹象。PMI仍在扩张但动能放缓。历史显示PMI>50.5后12月收益偏弱。",
        "contraction": "⚠️ 利润周期收缩。PMI<49.5。但回测显示收缩期后续6月收益+3.2%（胜率71%）——政策刺激窗口。",
        "bottoming": "利润周期触底。PMI在收缩区但跌势放缓。历史上这个阶段是布局窗口但不确定性高。",
        "neutral": "利润周期中性。PMI 49.5-50.5。无明确方向，维持既定策略。",
    }
    return base.get(phase, "数据不足，无法判断。")


# ─── Part 5: 回测引擎 ────────────────────────────────

import pandas as pd
import numpy as np

def _get_index_returns():
    """获取沪深300和恒生指数月度收益率"""
    try:
        hs300 = ak.stock_zh_index_daily(symbol="sh000300")
        hsi = ak.stock_hk_index_daily_em(symbol="HSI")
        return hs300, hsi
    except Exception as e:
        print(f"  [ERR] 指数数据: {e}", file=sys.stderr)
        return None, None


def _to_monthly_index(df, date_col, price_col):
    """日频转月频，取月末收盘价"""
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.sort_values(date_col)
    df["month"] = df[date_col].dt.to_period("M")
    monthly = df.groupby("month")[price_col].last().reset_index()
    monthly["month"] = monthly["month"].astype(str)
    return monthly


def run_backtest(indicator_name: str, indicator_series: list[dict],
                 indicator_key: str, lookaheads=[3, 6, 12]) -> dict:
    """
    对单个指标运行回测。
    indicator_series: [{"date": "2020-01", "value": 50.1}, ...]
    返回: {指标名: {lookahead_M: {bin: {avg_return, win_rate, count}}}}
    """
    if len(indicator_series) < 24:
        return {"error": "数据不足（<24个月）"}

    # 获取指数收益率
    hs300_raw, hsi_raw = _get_index_returns()
    if hs300_raw is None:
        return {"error": "无法获取指数数据"}

    hs300_m = _to_monthly_index(hs300_raw, "date", "close")
    hsi_m = _to_monthly_index(hsi_raw, "date", "close")

    # 计算月度收益率
    hs300_m["ret"] = hs300_m["close"].pct_change()
    hsi_m["ret"] = hsi_m["close"].pct_change()

    # 对齐宏观指标
    idx_vals = []
    for item in indicator_series:
        idx_vals.append({"month": item["date"], "value": item[indicator_key]})

    idx_df = pd.DataFrame(idx_vals)
    idx_df = idx_df.dropna(subset=["value"])

    # 合并
    merged = idx_df.merge(hs300_m, on="month", how="left")
    merged = merged.merge(hsi_m.rename(columns={"ret": "hsi_ret"}), on="month", how="left")

    # 排除 NaN
    merged = merged.dropna(subset=["ret", "hsi_ret"])

    if len(merged) < 12:
        return {"error": f"合并后数据不足（{len(merged)}月）"}

    results = {}
    for la in lookaheads:
        # 前向收益率
        merged[f"fwd_a_{la}m"] = merged["ret"].rolling(la).sum().shift(-la)
        merged[f"fwd_h_{la}m"] = merged["hsi_ret"].rolling(la).sum().shift(-la)

        fwd = merged.dropna(subset=[f"fwd_a_{la}m"])

        if len(fwd) < 6:
            continue

        # 分位分组（低/中/高）
        vals = fwd["value"]
        lo = vals.quantile(0.33)
        hi = vals.quantile(0.67)

        def _bin(v):
            if v <= lo:
                return "low"
            elif v <= hi:
                return "mid"
            return "high"

        fwd["bin"] = fwd["value"].apply(_bin)

        la_results = {}
        for market, col in [("a_share", f"fwd_a_{la}m"), ("h_share", f"fwd_h_{la}m")]:
            market_results = {}
            for b in ["low", "mid", "high"]:
                subset = fwd[fwd["bin"] == b][col]
                if len(subset) < 3:
                    continue
                market_results[b] = {
                    "avg_ret": round(subset.mean() * 100, 1),
                    "win_rate": round((subset > 0).mean() * 100, 1),
                    "count": len(subset),
                }
            la_results[market] = market_results

        results[f"{la}m"] = la_results

    return results


def run_all_backtests(pmi, cpi, ppi, m2, shrz, spread) -> dict:
    """全量回测"""
    print("\n═══ 回测开始 ═══", file=sys.stderr)

    configs = [
        ("pmi_mfg", pmi, "pmi_mfg"),
        ("cpi_yoy", cpi, "cpi_yoy"),
        ("ppi_yoy", ppi, "ppi_yoy"),
        ("m2_yoy", m2, "m2_yoy"),
        ("shrz_total", shrz, "shrz_total"),
    ]

    # 中美利差需要月均化
    if spread:
        spread_monthly = _monthly_avg(spread, "spread")
        configs.append(("cn_us_spread", spread_monthly, "spread"))

    all_results = {}
    for name, series, key in configs:
        if not series:
            print(f"  [SKIP] {name}: 无数据", file=sys.stderr)
            continue
        print(f"  [回测] {name} ({len(series)}月)...", file=sys.stderr)
        result = run_backtest(name, series, key)
        all_results[name] = result

    # 保存
    with open(BACKTEST_FILE, "w") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)

    print(f"  结果保存至 {BACKTEST_FILE}", file=sys.stderr)
    return all_results


def _monthly_avg(daily: list[dict], val_key: str) -> list[dict]:
    """日频 → 月均"""
    df = pd.DataFrame(daily)
    df["date"] = pd.to_datetime(df["date"])
    df["month"] = df["date"].dt.strftime("%Y-%m")
    monthly = df.groupby("month")[val_key].mean().reset_index()
    monthly.columns = ["month", val_key]
    out = []
    for _, row in monthly.iterrows():
        out.append({"date": row["month"], val_key: row[val_key]})
    out.sort(key=lambda x: x["date"])
    return out


# ─── Part 6: 主函数 ──────────────────────────────────

SIGNAL_LOG = os.path.expanduser("~/.hermes/data/macro_signal_log.jsonl")


def _append_signal_log(state: dict):
    """每次管道运行追加一条信号记录，用于Phase 3追踪准确率"""
    c = state["profit_cycle"]
    l = state.get("liquidity", {})
    i = state.get("inflation", {})

    entry = {
        "timestamp": state["updated"],
        "phase": c["phase"],
        "phase_label": c["phase_label"],
        "pmi": c["pmi_mfg_latest"],
        "pmi_3m_avg": c["pmi_mfg_3m_avg"],
        "pmi_trend": c["pmi_mfg_trend"],
        "ppi": i.get("ppi_yoy"),
        "cpi": i.get("cpi_yoy"),
        "m2_yoy": l.get("m2_yoy"),
        "m1_yoy": l.get("m1_yoy"),
        "cn_us_spread": l.get("cn_us_spread"),
        "dca_multiplier": state.get("dca_signal", {}).get("multiplier"),
        "dca_signal": state.get("dca_signal", {}).get("signal"),
        # 待回填：actual_a_3m, actual_h_3m, actual_a_6m, actual_h_6m
    }

    try:
        with open(SIGNAL_LOG, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"  [WARN] 信号日志写入失败: {e}", file=sys.stderr)


def main():
    do_backtest = "--backtest" in sys.argv
    json_mode = "--json" in sys.argv

    print("═══ 宏观数据管道 v2.0 ═══", file=sys.stderr)

    # ── 采集 ──
    print("\n[1/7] 东方财富 → PMI", file=sys.stderr)
    pmi = fetch_pmi()
    print(f"  PMI: {len(pmi)}条 ({pmi[0]['date']}~{pmi[-1]['date']})" if pmi else "  PMI: 失败")

    print("[2/7] AKShare → M2/货币供应", file=sys.stderr)
    m2 = fetch_m2_akshare()
    print(f"  M2: {len(m2)}条 ({m2[0]['date']}~{m2[-1]['date']})" if m2 else "  M2: 失败")

    print("[3/7] AKShare → 社融规模", file=sys.stderr)
    shrz = fetch_social_financing()
    print(f"  社融: {len(shrz)}条 ({shrz[0]['date']}~{shrz[-1]['date']})" if shrz else "  社融: 失败")

    print("[4/7] AKShare → 新增信贷", file=sys.stderr)
    credit = fetch_new_credit()
    print(f"  信贷: {len(credit)}条" if credit else "  信贷: 失败")

    print("[5/7] 东方财富 → CPI/PPI/GDP", file=sys.stderr)
    cpi = fetch_cpi()
    ppi = fetch_ppi()
    gdp = fetch_gdp()
    print(f"  CPI: {len(cpi)}条 | PPI: {len(ppi)}条 | GDP: {len(gdp)}条")

    print("[6/7] AKShare → 中美利差（可能较慢）", file=sys.stderr)
    spread = fetch_cn_us_spread()
    print(f"  利差: {len(spread)}条日频数据 ({spread[0]['date']}~{spread[-1]['date']})" if spread else "  利差: 失败")

    # ── 入库 ──
    print("\n[7/7] 写库", file=sys.stderr)
    conn = init_db()
    save_series(conn, "pmi_mfg", pmi, "pmi_mfg")
    save_series(conn, "cpi_yoy", cpi, "cpi_yoy")
    save_series(conn, "ppi_yoy", ppi, "ppi_yoy")
    save_series(conn, "gdp_yoy", gdp, "gdp_yoy")
    save_series(conn, "m2_yoy", m2, "m2_yoy")
    save_series(conn, "m1_yoy", m2, "m1_yoy")
    save_series(conn, "shrz_total", shrz, "shrz_total")
    save_series(conn, "loan_new", credit, "loan_new")
    if spread:
        save_series(conn, "cn_10y", spread, "cn_10y")
        save_series(conn, "us_10y", spread, "us_10y")
        save_series(conn, "cn_us_spread", spread, "spread")
    conn.close()

    # ── 状态 ──
    state = build_state(pmi, cpi, ppi, gdp, m2, shrz, spread)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    # ── 信号日志（Phase 3 影子运行） ──
    _append_signal_log(state)

    # ── 回测 ──
    if do_backtest:
        run_all_backtests(pmi, cpi, ppi, m2, shrz, spread)

    if json_mode:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        c = state["profit_cycle"]
        print(f"\n═══ 利润周期定位 ═══")
        print(f"状态：{c['phase_label']} | 置信度：{c['confidence']}")
        print(f"PMI：{c['pmi_mfg_latest']} | 3月均：{c['pmi_mfg_3m_avg']} | 趋势：{c['pmi_mfg_trend']:+.1f}")
        liq = state.get("liquidity", {})
        if liq.get("m2_yoy"):
            print(f"M2：{liq['m2_yoy']:.1f}% | M1：{liq['m1_yoy']:.1f}%")
        if liq.get("cn_us_spread"):
            print(f"中美利差：{liq['cn_us_spread']:.2f}%")
        inf = state.get("inflation", {})
        if inf.get("cpi_yoy"):
            print(f"CPI：{inf['cpi_yoy']:.1f}% | PPI：{inf['ppi_yoy']}%")
        dca = state.get("dca_signal", {})
        if dca:
            print(f"\n📊 DCA倍率：{dca.get('label', '?')} ({dca.get('multiplier', 1.0)}x)")
            print(f"   {dca.get('reason', '')}")
        print(f"\n{state['interpretation']}")


if __name__ == "__main__":
    main()
