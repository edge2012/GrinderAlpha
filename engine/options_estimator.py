#!/usr/bin/env python3
"""
期权估算引擎（降级兜底路径）— 纯Python Black-Scholes（无scipy依赖）
====================================================================
⚠️ 定位：本文件是 Account C 期权扫描器的【降级兜底】，不是主路径。

主路径（优先）：engine/cboe_options.py 读 CBOE 期权链真实 bid/ask 中间价，
  权利金直接取市场报价，无"估算精度"问题。

本文件仅在 CBOE 期权链不可用时启用，用 BS + 估算 IV 兜底：
  bs_put_price()      — 欧式Put定价
  estimate_stock_iv() — 52w范围×0.8 估算个股IV（已被证伪：HOOD高估55.5pp）
  estimate_index_iv() — 指数IV≈VIX本身
  estimate_spread()   — Bull Put Spread 权利金
  estimate_sp()       — Naked SP 权利金
  calc_premium_risk() — 权利金 × 止损比例 → 仓位占比

已知局限（降级路径固有）：
  - 用 52w范围×0.8 估 IV，绝对精度仅 ~45%（SPY spread 实测 $241 vs 富途 $167）
  - 只用于仓位估算量级，不可作真实权利金报价
  - 深度虚值Put可能被系统性低估（vol skew简化处理）
"""

import math

# ─── 标准正态分布CDF和PDF（纯Python，不依赖scipy）───
def _norm_cdf(x: float) -> float:
    """标准正态分布CDF — Abramowitz & Stegun 近似"""
    if x < -8: return 0.0
    if x > 8: return 1.0
    # Hart's algorithm
    return 0.5 * math.erfc(-x / math.sqrt(2))

def _norm_pdf(x: float) -> float:
    """标准正态分布PDF"""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


# ─── Black-Scholes ───

def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    欧式Put定价（纯Python）
    
    Args:
        S: 标的现价
        K: 行权价
        T: 到期时间（年，如30天=30/365）
        r: 无风险利率（如0.05 = 5%）
        sigma: 波动率（如0.50 = 50%）
    
    Returns: 权利金
    """
    if T <= 0 or sigma <= 0:
        return max(K - S, 0)
    d1 = (math.log(S / K) + (r + sigma**2 / 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return max(K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1), 0)


def bs_put_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Put Delta（用于快速判断OTM程度）"""
    if T <= 0 or sigma <= 0:
        return -1.0 if S < K else 0.0
    d1 = (math.log(S / K) + (r + sigma**2 / 2) * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1) - 1


# ─── IV 估算 ───

def estimate_stock_iv(
    price: float,
    high_52w: float,
    low_52w: float,
    vix: float,
    vol_tier: str = 'mid'
) -> float:
    """
    估算个股隐含波动率。
    
    方法: 历史波动率（52w范围归一化） + VIX溢价
    
    Args:
        price: 现价
        high_52w: 52周最高
        low_52w: 52周最低
        vix: CBOE VIX 当前值
        vol_tier: high/mid/low 波动率档位
    
    Returns: 估算IV（小数，如0.50 = 50%）
    """
    hv_range = (high_52w / low_52w - 1) if low_52w > 0 else 0.5
    
    # 历史波动率（52w范围归一化到年化）
    hv = hv_range * 0.8  # 近似：范围×0.8 ≈ 年化波动率
    
    # VIX溢价（个股IV > VIX）
    tier_mult = {'high': 2.5, 'mid': 1.8, 'low': 1.3}.get(vol_tier, 1.8)
    vix_decimal = vix / 100.0
    
    iv = max(hv, vix_decimal * tier_mult)
    return min(iv, 2.0)  # 上限200%


def estimate_index_iv(vix: float) -> float:
    """估算指数期权IV（接近VIX本身）"""
    return vix / 100.0 * 1.05  # VIX + 5%溢价


# ─── 仓位计算 ───

def calc_premium_risk(
    premium_per_contract: float,
    contracts: int,
    account_total: float,
    stop_loss_pct: float = 0.50
) -> dict:
    """
    计算权利金风险敞口和仓位占比。
    
    逻辑: 权利金亏损stop_loss_pct时止损 → 实际风险 = 权利金 × stop_loss_pct
    
    Args:
        premium_per_contract: 每张合约预估权利金
        contracts: 合约张数
        account_total: 账户总资产
        stop_loss_pct: 止损触发比例（默认50%）
    
    Returns: {'risk_amount': float, 'risk_pct': float, 'warning': str|None}
    """
    risk = premium_per_contract * contracts * stop_loss_pct
    risk_pct = risk / account_total * 100
    
    warning = None
    if risk_pct > 5:
        warning = f"⚠️超标({risk_pct:.1f}%>5%)"
    elif risk_pct > 4:
        warning = f"⚠️边缘({risk_pct:.1f}%)"
    
    return {'risk_amount': risk, 'risk_pct': risk_pct, 'warning': warning}


# ─── Spread 估算 ───

def estimate_spread(
    price: float,
    sell_strike: float,
    buy_strike: float,
    dte: int,
    vix: float,
    iv: float | None = None,
    rf: float = 0.04
) -> dict:
    """
    估算 Bull Put Spread 权利金和风险。
    
    Returns: {
        'sell_premium': float,   # 卖腿权利金
        'buy_premium': float,    # 买腿权利金
        'net_premium': float,    # 净收入
        'max_loss': float,       # 最大亏损 (spread宽度 - 净收入)
        'risk_50pct': float,     # 50%止损风险
    }
    """
    if iv is None:
        iv = estimate_index_iv(vix)  # 指数IV≈VIX本身
    
    T = dte / 365.0
    
    sell_p = bs_put_price(price, sell_strike, T, rf, iv)
    buy_p = bs_put_price(price, buy_strike, T, rf, iv)
    
    net = sell_p - buy_p
    spread_width = (sell_strike - buy_strike)
    max_loss = spread_width - net
    risk_50 = net * 0.5  # 止损亏损
    
    return {
        'sell_premium': round(sell_p, 2),
        'buy_premium': round(buy_p, 2),
        'net_premium': round(net, 2),
        'max_loss': round(max_loss * 100, 2),  # 转换为美元
        'risk_50pct': round(risk_50 * 100, 2),
        'iv_used': round(iv, 2),
    }


def estimate_sp(
    price: float,
    strike: float,
    dte: int,
    iv: float,
    rf: float = 0.04
) -> dict:
    """
    估算 Naked Short Put 权利金和风险。
    
    Returns: {
        'premium': float,        # 权利金
        'risk_50pct': float,     # 50%止损风险
        'delta': float,          # Delta（OTM程度）
    }
    """
    T = dte / 365.0
    premium = bs_put_price(price, strike, T, rf, iv)
    delta = bs_put_delta(price, strike, T, rf, iv)
    
    return {
        'premium': round(premium * 100, 2),  # 转换为美元
        'risk_50pct': round(premium * 100 * 0.5, 2),
        'delta': round(delta, 3),
    }


# ─── 自检 ───
if __name__ == '__main__':
    # 降级路径精度演示（非主路径——主路径走 cboe_options.py 真实报价）
    # SPY $768, VIX 15.32, 35 DTE → 降级BS估 $241 vs 富途实盘 ~$167
    sp = estimate_spread(768, 730, 690, 35, 15.32)
    print("=== SPY Spread 5/10 降级BS估算（精度演示） ===")
    print(f"卖腿@730: ${sp['sell_premium']}  买腿@690: ${sp['buy_premium']}")
    print(f"净收入: ${sp['net_premium']}/股  最大亏损: ${sp['max_loss']}  50%止损: ${sp['risk_50pct']}")
    print(f"富途实盘参考 ~$167，偏差~{abs(sp['net_premium']*100 - 167)/167:.0%}（降级路径固有，仅量级参考）")

    # HOOD SP（52w×0.8 估 IV → 148%，真实仅~61%，印证此方法已证伪）
    iv_h = estimate_stock_iv(94, 154, 54, 15.5, 'high')
    sp_h = estimate_sp(94, 75, 30, iv_h)
    print(f"\n=== HOOD SP @$75（降级BS） ===")
    print(f"52w×0.8估IV: {iv_h:.0%}（真实~61%，此方法已证伪）  权利金: ${sp_h['premium']}  Delta: {sp_h['delta']}")
