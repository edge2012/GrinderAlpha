#!/usr/bin/env python3
"""
CBOE 期权链客户端 — 真实市场 IV + 权利金数据源
================================================
数据源: CBOE delayed quotes API（免费、无认证、延迟15分钟）
  https://cdn.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json

关键事实（2026-08-12 盘中实测验证）:
  - 盘中(9:30-16:00 ET): 个体合约 bid/ask/iv/delta/oi 全量可用
    (SPY 12K+ 合约非零 IV、HOOD 1.7K)
  - 盘前/盘后: 个体合约全 0，仅 iv30(股票级ATM IV) 可用
  - 扫描必须放在开盘后（cron 已调整为 21:40/22:40 BJT）

合约名解析: TICKER + YYMMDD + C/P + strike×1000
  例: HOOD260911P00082000 → HOOD 2026-09-11 Put $82

定价策略: 直接用市场 bid/ask 中间价（真实可成交参考），不再用 BS + 估算 IV
  （此前 BS 用 52w范围×0.8 估算 IV，HOOD 实测高估 55.5pp）
"""

import json
import urllib.request
from datetime import datetime, date
from typing import Optional, Dict, List

CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"
UA = {'User-Agent': 'Mozilla/5.0'}

# ─── 流动性门禁阈值 ───
LIQ_OI_MIN = 50          # 最低持仓量（软提示）
LIQ_SPREAD_MAX = 0.30    # 最大价差率 (ask-bid)/mid（软提示）


def parse_option_name(opt_name: str):
    """解析合约名 → (ticker, expiry_date, cp, strike)，失败返回 None"""
    try:
        for i, ch in enumerate(opt_name):
            if ch in ('C', 'P') and i >= 8:
                ticker = opt_name[:i - 6]
                date_str = opt_name[i - 6:i]
                cp = ch
                strike = int(opt_name[i + 1:]) / 1000.0
                try:
                    dt = datetime.strptime(date_str, "%y%m%d").date()
                    return ticker, dt, cp, strike
                except ValueError:
                    return None
    except Exception:
        return None
    return None


def fetch_chain(symbol: str) -> Optional[dict]:
    """拉取 CBOE 期权链（含 current_price、iv30、全量 options 数组）"""
    url = CBOE_URL.format(symbol=symbol)
    try:
        req = urllib.request.Request(url, headers=UA)
        raw = urllib.request.urlopen(req, timeout=15).read().decode('utf-8')
        data = json.loads(raw).get('data')
        if not data or not data.get('options'):
            return None
        return data
    except Exception as e:
        print(f"⚠️ CBOE 期权链拉取失败 {symbol}: {e}")
        return None


def _extract_puts(chain: dict, target_dte: int = 30):
    """从期权链提取 Put 合约，返回 (最接近 target_dte 的到期日, 该到期日 Put 列表[按strike升序])"""
    today = date.today()
    puts_by_exp: Dict = {}
    for o in chain.get('options', []):
        if not o.get('option'):
            continue
        parsed = parse_option_name(o['option'])
        if not parsed:
            continue
        ticker, expiry, cp, strike = parsed
        if cp != 'P':
            continue
        dte = (expiry - today).days
        if dte < 1:
            continue
        puts_by_exp.setdefault(expiry, []).append({
            **o, 'strike': strike, 'expiry': expiry, 'dte': dte,
        })
    if not puts_by_exp:
        return None, None
    best_exp = min(puts_by_exp.keys(), key=lambda d: abs((d - today).days - target_dte))
    puts = sorted(puts_by_exp[best_exp], key=lambda x: x['strike'])
    return best_exp, puts


def assess_liquidity(bid: float, ask: float, oi: float, mid: float) -> tuple:
    """流动性门禁 → (状态码, 提示语)

    状态码: 'ok' / 'no_quote'(无报价,硬门禁) / 'wide_spread'(价差过宽) / 'low_oi'(持仓稀少)
    """
    if not bid or not ask:
        return 'no_quote', '流动性不足(无报价)'
    if oi < LIQ_OI_MIN:
        return 'low_oi', f'持仓稀少(OI={oi:.0f})'
    spread_ratio = (ask - bid) / mid if mid > 0 else 1.0
    if spread_ratio > LIQ_SPREAD_MAX:
        return 'wide_spread', f'价差过宽({spread_ratio:.0%})'
    return 'ok', ''


def get_put_quote(symbol: str, strike: float, target_dte: int = 30,
                  chain: Optional[dict] = None) -> Optional[dict]:
    """获取目标行权价的真实市场报价（自动对齐到最近挂牌价）。

    Returns: {
        'symbol', 'strike'(实际挂牌价), 'target_strike', 'expiry', 'dte',
        'iv', 'bid', 'ask', 'mid', 'oi', 'delta',
        'liquidity'(状态码), 'liquidity_note',
    }，链不可用/无合约时返回 None
    """
    if chain is None:
        chain = fetch_chain(symbol)
    if not chain:
        return None
    best_exp, puts = _extract_puts(chain, target_dte)
    if not puts:
        return None
    nearest = min(puts, key=lambda p: abs(p['strike'] - strike))
    iv = nearest.get('iv', 0) or 0
    bid = nearest.get('bid', 0) or 0
    ask = nearest.get('ask', 0) or 0
    oi = nearest.get('open_interest', 0) or 0
    mid = (bid + ask) / 2 if (bid and ask) else (bid or ask)
    liquidity, note = assess_liquidity(bid, ask, oi, mid)
    return {
        'symbol': symbol,
        'strike': nearest['strike'],
        'target_strike': strike,
        'expiry': best_exp,
        'dte': nearest['dte'],
        'iv': iv,
        'bid': bid,
        'ask': ask,
        'mid': mid,
        'oi': oi,
        'delta': nearest.get('delta'),
        'liquidity': liquidity,
        'liquidity_note': note,
    }


def estimate_spread_cboe(symbol: str, sell_strike: float, buy_strike: float,
                         target_dte: int = 30, chain: Optional[dict] = None) -> Optional[dict]:
    """用 CBOE 真实报价估算 Bull Put Spread 权利金。

    返回结构兼容旧 estimate_spread()（net_premium/max_loss/risk_50pct/iv_used），
    并额外带 sell_quote/buy_quote/liquidity。链不可用返回 None（调用方降级到 BS）。
    """
    if chain is None:
        chain = fetch_chain(symbol)
    sell_q = get_put_quote(symbol, sell_strike, target_dte, chain=chain)
    buy_q = get_put_quote(symbol, buy_strike, target_dte, chain=chain)
    if not sell_q or not buy_q:
        return None

    net = sell_q['mid'] - buy_q['mid']
    spread_width = sell_q['strike'] - buy_q['strike']

    # 流动性：任一腿 no_quote 则整体不可做
    if sell_q['liquidity'] == 'no_quote' or buy_q['liquidity'] == 'no_quote':
        liquidity = 'no_quote'
        liq_note = '流动性不足' + (f"(卖腿)" if sell_q['liquidity'] == 'no_quote' else "(买腿)")
    elif sell_q['liquidity'] == 'wide_spread' or buy_q['liquidity'] == 'wide_spread':
        liquidity = 'wide_spread'
        liq_note = '价差过宽'
    elif sell_q['liquidity'] == 'low_oi' or buy_q['liquidity'] == 'low_oi':
        liquidity = 'low_oi'
        liq_note = '持仓稀少'
    else:
        liquidity = 'ok'
        liq_note = ''

    return {
        'sell_premium': round(sell_q['mid'], 2),
        'buy_premium': round(buy_q['mid'], 2),
        'net_premium': round(net, 2),
        'max_loss': round((spread_width - net) * 100, 2),
        'risk_50pct': round(net * 0.5 * 100, 2),
        'iv_used': round(sell_q['iv'], 2),
        'sell_strike': sell_q['strike'],
        'buy_strike': buy_q['strike'],
        'liquidity': liquidity,
        'liquidity_note': liq_note,
        'sell_quote': sell_q,
        'buy_quote': buy_q,
    }


def estimate_sp_cboe(symbol: str, strike: float, target_dte: int = 30,
                     chain: Optional[dict] = None) -> Optional[dict]:
    """用 CBOE 真实报价估算 Naked Short Put 权利金。

    返回结构兼容旧 estimate_sp()（premium/risk_50pct/delta），
    额外带 iv/liquidity/liquidity_note/quote。链不可用返回 None。
    """
    if chain is None:
        chain = fetch_chain(symbol)
    q = get_put_quote(symbol, strike, target_dte, chain=chain)
    if not q:
        return None
    return {
        'premium': round(q['mid'] * 100, 2),
        'risk_50pct': round(q['mid'] * 100 * 0.5, 2),
        'delta': round(q['delta'], 3) if q['delta'] is not None else None,
        'iv': q['iv'],
        'strike': q['strike'],
        'liquidity': q['liquidity'],
        'liquidity_note': q['liquidity_note'],
        'quote': q,
    }


if __name__ == '__main__':
    # 自检：打印各标的目标行权价的真实报价
    for sym, strikes in [('ADBE', [256, 230]), ('CRM', [164, 148]), ('HOOD', [75])]:
        chain = fetch_chain(sym)
        print(f"\n=== {sym} 现价 ${chain['current_price']:.2f} ===")
        for s in strikes:
            q = get_put_quote(sym, s, chain=chain)
            if q:
                print(f"  目标${s} → 挂牌${q['strike']:.0f} | mid=${q['mid']:.2f} "
                      f"IV={q['iv']*100:.0f}% OI={q['oi']:.0f} [{q['liquidity']}]")
            else:
                print(f"  目标${s} → 无数据")
