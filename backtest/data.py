"""
回测统一历史K线数据接口
========================

统一「标的历史价格序列」的获取，按市场路由到底层免费数据源：

- A/H → 腾讯 fqkline（免费、qfq 复权、完整历史）
- US  → Yahoo Finance v8 chart API（免费、adjusted close、重试+缓存降级）

设计原则（对齐 investment.data_access.DataAccess）：
- 只依赖免费源，无 API key、无私有依赖（公开库可跑）
- 所有 fetch 失败返回空 DataFrame，不抛异常
- US 复用 scan_engines.trend_following._fetch_daily 的「重试 + 缓存降级」范式

⚠️ 腾讯 fqkline 字段名陷阱：URL 带 qfq 参数时返回字段名是 `qfqday`/`qfqmonth`，
不是 `day`/`month`，必须 `day or qfqday` 双兼容（见 investment-data-sourcing skill）。
⚠️ 腾讯 K线 API 不支持美股历史（只返首尾K线），美股必须走 Yahoo v8。
"""

import json
import os
import time
import urllib.request
from typing import Optional
from urllib.parse import quote as urlquote

import pandas as pd

_TENCENT_UA = "Mozilla/5.0"
_YAHOO_UA = "Mozilla/5.0"

# US 日线本地缓存目录（Yahoo 失败降级用，公开库下目录不存在则跳过缓存）
# ⚠️ 独立于 scan_engines 的 ~/.hermes/state/us_daily（后者缓存格式无 dates，回测需要日期）
_US_DAILY_CACHE_DIR = os.environ.get(
    "US_DAILY_CACHE_DIR",
    os.path.join(os.path.dirname(__file__), "..", "data", "us_daily_bt"),
)

_RETRY = 3
_RETRY_BACKOFF = [2, 5, 10]


def _us_cache_path(symbol: str) -> str:
    return os.path.join(_US_DAILY_CACHE_DIR, f"{symbol}.json")


def _read_us_cache(symbol: str) -> Optional[dict]:
    try:
        with open(_us_cache_path(symbol)) as f:
            return json.load(f)
    except Exception:
        return None


def _write_us_cache(symbol: str, dates: list, closes: list) -> None:
    try:
        os.makedirs(_US_DAILY_CACHE_DIR, exist_ok=True)
        with open(_us_cache_path(symbol), "w") as f:
            json.dump({"symbol": symbol, "dates": dates, "closes": closes}, f)
    except Exception:
        pass  # 缓存写失败不影响主流程


def fetch_tencent_kline(tcode: str, freq: str = "day",
                        count: int = 2000) -> pd.DataFrame:
    """腾讯 fqkline 历史K线（A/H 指数/个股，免费 qfq 复权）。

    tcode 需带市场前缀：sh000300 / sz399006 / hkHSTECH / hk00700。
    freq: 'day' | 'week' | 'month'。
    返回 DataFrame（DatetimeIndex + open/close/high/low 列），失败返回空 DataFrame。
    """
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={tcode},{freq},,,{count},qfq")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _TENCENT_UA})
        raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", errors="replace")
        data = json.loads(raw).get("data")
        if not isinstance(data, dict):
            return pd.DataFrame()
        node = data.get(tcode, {})
        # qfq 参数 → 字段名 qfqday/qfqweek/qfqmonth，双兼容
        field = f"{freq}" if freq != "day" else "day"
        qfq_field = f"qfq{freq}"
        rows = node.get(field) or node.get(qfq_field) or []
        if not rows:
            return pd.DataFrame()
        # 腾讯 K线数组: [date, open, close, high, low, volume, ...]
        recs = []
        for k in rows:
            try:
                recs.append({
                    "date": k[0],
                    "open": float(k[1]),
                    "close": float(k[2]),
                    "high": float(k[3]),
                    "low": float(k[4]),
                })
            except (IndexError, ValueError, TypeError):
                continue
        df = pd.DataFrame(recs)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date").sort_index()
    except Exception:
        return pd.DataFrame()


def fetch_tencent_prices(tcodes: list[str]) -> dict[str, float]:
    """批量腾讯实时价（纯 urllib，无缓存/限流）。返回 {tcode: price}。

    公开库可用；生产低频 cron 场景（止盈监控）直接批量拉，不走 market_data_layer。
    与 fetch_tencent_kline 同源（腾讯），放在一起供公开数据层调用。
    """
    if not tcodes:
        return {}
    url = f"http://qt.gtimg.cn/q={','.join(tcodes)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _TENCENT_UA})
        raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk", errors="replace")
    except (urllib.error.URLError, OSError):
        return {}

    prices: dict[str, float] = {}
    for line in raw.split("\n"):
        if "~" not in line:
            continue
        parts = line.split("~")
        if len(parts) < 5:
            continue
        try:
            price = float(parts[3])
        except (ValueError, IndexError):
            continue
        code = line.split('="')[0].replace("v_", "")
        prices[code] = price
    return prices


def fetch_yahoo_daily(symbol: str, range_str: str = "max") -> pd.DataFrame:
    """Yahoo Finance v8 美股日线（免费 adjusted close，重试 + 缓存降级）。

    返回 DataFrame（DatetimeIndex + close 列），失败时回退本地缓存，再失败返回空。
    """
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urlquote(symbol)}"
           f"?range={range_str}&interval=1d")

    for attempt in range(_RETRY):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _YAHOO_UA})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            result = data["chart"]["result"][0]
            ts = result.get("timestamp") or []
            quote = (result.get("indicators", {}).get("quote") or [{}])[0]
            closes = quote.get("close") or []
            if ts and closes:
                dates = [pd.to_datetime(int(t), unit="s") for t in ts]
                df = pd.DataFrame({"close": closes}, index=dates).sort_index()
                df = df[df["close"].notna()]
                _write_us_cache(symbol, [str(d) for d in df.index], df["close"].tolist())
                return df
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < _RETRY - 1:
                time.sleep(_RETRY_BACKOFF[attempt])
                continue
            break
        except Exception:
            if attempt < _RETRY - 1:
                time.sleep(_RETRY_BACKOFF[attempt])
                continue
            break

    # 缓存降级（仅本模块写入的带日期格式）
    cached = _read_us_cache(symbol)
    if cached and cached.get("closes") and cached.get("dates"):
        df = pd.DataFrame({"close": cached["closes"]}, index=pd.to_datetime(cached["dates"]))
        return df.sort_index()
    return pd.DataFrame()


def get_history(symbol: str, market: str = "A", freq: str = "day",
                count: int = 2000) -> tuple[pd.DataFrame, str]:
    """统一历史K线接口。返回 (DataFrame, data_source)。

    market: 'A' | 'HK' | 'US'。freq: 'day' | 'week' | 'month'。
    data_source 声明数据来源（配合6 验收要求），如 '腾讯 fqkline' / 'Yahoo Finance v8'。
    """
    market = market.upper()
    if market in ("A", "HK", "US"):
        if market == "US":
            df = fetch_yahoo_daily(symbol)
            return df, "Yahoo Finance v8"
        # A/H：symbol 需带前缀（sh/sz/hk），无前缀则按市场补默认前缀
        tcode = symbol
        if not any(symbol.startswith(p) for p in ("sh", "sz", "hk")):
            prefix = {"A": "sh", "HK": "hk"}.get(market, "sh")
            tcode = f"{prefix}{symbol}"
        df = fetch_tencent_kline(tcode, freq=freq, count=count)
        return df, "腾讯 fqkline"
    raise ValueError(f"不支持的市场: {market}（仅 A/HK/US）")
