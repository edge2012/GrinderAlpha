"""
DataAccess — 数据访问抽象接口
==============================

抽象行情/估值数据的获取，让消费者（方法论/引擎）不直接依赖
market_data_layer / urllib / akshare。

三部分：
- DataAccess (ABC): 接口定义（行情 + 估值 + 估值缓存）
- MarketDataAccess: 私有实现，注入 market_data_layer（缓存/限流/容错）+ akshare
- SimpleDataAccess: 公开实现，urllib 腾讯 + akshare 直连（公开库可用）

估值数据多源降级（2026-08-31 重构）：
- PE 主源: 中证指数官网（官方，2011 起 15 年，index-perf 端点 peg 字段；更早 peg 为空）
- PE/PB 降级: legulegu（akshare，15-20 年）→ 蛋卷快照（10 年口径现成百分位）
- 股息率主源: 中证指数官网（csindex，1 月历史）→ 蛋卷快照 yeild

用法：
    # 生产私有（默认）
    from investment.data_access import MarketDataAccess
    da = MarketDataAccess()
    price = da.get_quote("600519", "A")

    # 公开库
    from investment.data_access import SimpleDataAccess
    da = SimpleDataAccess()

设计原则（对齐 PositionProvider 抽象）：
- 接口 ABC 不 import 具体数据源（market_data_layer / akshare 均在实现内 lazy import；
  官方源中证官网/蛋卷为 urllib 直连，模块级函数供两个实现共用）
- 消费者通过构造参数注入，默认延迟创建私有实现
- 所有 fetch 方法失败返回 None，不抛异常（与 valuation_engine 既有语义一致）
"""

import abc
import json
import time
import urllib.request
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

# ─── 官方数据源拉取（urllib 直连，MarketDataAccess 与 SimpleDataAccess 共用）───

_CSINDEX_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_CSINDEX_PE_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
_DANJUAN_URL = "https://danjuanfunds.com/djapi/index_eva/dj"

# 蛋卷快照进程内缓存（63 只指数全量，1h TTL，市场级数据对所有 ETF 共享）
_DANJUAN_CACHE: Dict[str, Any] = {"ts": 0.0, "items": []}

# 中证官网 PE 历史进程内缓存（index_code -> (fetched_at, result)，1h TTL）。
# 行业/AI链 ETF 共用中证500(000905)，避免同一指数代码被重复请求触发官网限流。
_CSINDEX_PE_CACHE: Dict[str, Any] = {}
_CSINDEX_PE_CACHE_TTL = 3600  # 1h

# legulegu 熔断（模块级，两个 DataAccess 实现共用）：
# legulegu 宕机时（csrf 缺失 → AttributeError）每次 akshare 调用浪费 ~6s。
# 熔断后 30 分钟内跳过 legulegu 直接降级，避免批量估值刷新的累计超时。
_LEGULEGU_CIRCUIT: Dict[str, Any] = {"open": False, "until": 0.0}
_LEGULEGU_COOLDOWN = 1800  # 30 分钟


def _legulegu_available() -> bool:
    """legulegu 熔断检查：熔断打开期间返回 False（跳过 legulegu 直接降级）。"""
    if _LEGULEGU_CIRCUIT["open"]:
        if time.time() < _LEGULEGU_CIRCUIT["until"]:
            return False
        _LEGULEGU_CIRCUIT["open"] = False  # 熔断窗口过期，重新探测
    return True


def _trip_legulegu_circuit() -> None:
    """legulegu 宕机（csrf 缺失），打开熔断。"""
    _LEGULEGU_CIRCUIT["open"] = True
    _LEGULEGU_CIRCUIT["until"] = time.time() + _LEGULEGU_COOLDOWN


def _fetch_legulegu_series(ak_fn, name: str):
    """带熔断的 akshare legulegu 调用。返回 DataFrame 或 None。

    - 熔断打开期间：直接返回 None（不发起网络请求）
    - legulegu 宕机（csrf 缺失 → AttributeError）：打开熔断，返回 None
    - 其他异常（指数名不存在等）：仅返回 None，不熔断
    """
    if not _legulegu_available():
        return None
    try:
        return ak_fn(symbol=name)
    except AttributeError:
        # csrf-token 缺失 = legulegu 页面不可达（宕机），触发熔断
        _trip_legulegu_circuit()
        return None
    except Exception:
        return None


def _http_get_json(url: str, headers: Dict[str, str], timeout: int = 15):
    """urllib 拉取 JSON，失败返回 None。"""
    try:
        req = urllib.request.Request(url, headers=headers)
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8")
        return json.loads(raw)
    except Exception:
        return None


def _fetch_csindex_pe_history(index_code: str) -> Optional[Tuple[List[float], date, date]]:
    """中证指数官网官方 PE 历史序列（index-perf 端点 peg 字段，2011 起约 15 年）。

    peg 字段即滚动市盈率（官网字段名误标为 peg），实测值 ≈ 指数当前 PE。
    返回 (values, start_date, end_date)，失败或数据不足返回 None。
    """
    if not index_code:
        return None
    # 进程内缓存（1h TTL）：同一指数代码避免重复请求（行业/AI链 ETF 共用中证500）
    cached = _CSINDEX_PE_CACHE.get(index_code)
    if cached and time.time() - cached[0] < _CSINDEX_PE_CACHE_TTL:
        return cached[1]
    end = date.today().strftime("%Y%m%d")
    url = f"{_CSINDEX_PE_URL}?indexCode={index_code}&startDate=20050101&endDate={end}"
    data = _http_get_json(url, {
        "User-Agent": _CSINDEX_UA,
        "Referer": "https://www.csindex.com.cn/",
        "Accept": "application/json",
    })
    if not data or data.get("code") != "200":
        return None
    rows = data.get("data") or []
    if not rows:
        return None
    vals, dts = [], []
    for r in rows:
        peg = r.get("peg")
        td = r.get("tradeDate")
        if peg is None or td is None:
            continue
        try:
            v = float(peg)
        except (TypeError, ValueError):
            continue
        if v <= 0:  # 过滤无盈利/异常值
            continue
        vals.append(v)
        dts.append(td)
    if len(vals) < 100:
        return None
    try:
        start = datetime.strptime(dts[0], "%Y%m%d").date()
        end_d = datetime.strptime(dts[-1], "%Y%m%d").date()
    except (ValueError, IndexError):
        return None
    result = (vals, start, end_d)
    _CSINDEX_PE_CACHE[index_code] = (time.time(), result)  # 仅缓存成功结果
    return result


def _fetch_danjuan_snapshot(danjuan_code: str) -> Optional[Dict[str, Optional[float]]]:
    """蛋卷基金指数估值快照。返回 {pe, pb, pe_pct, pb_pct, div_yield} 或 None。

    单位约定：
    - pe/pb: 绝对值
    - pe_pct/pb_pct: 0-1 小数（蛋卷 10 年历史口径的百分位，越高越贵）
    - div_yield: 小数（0.0106 = 1.06%），消费方需 ×100 转百分比
    """
    if not danjuan_code:
        return None
    now = time.time()
    if now - _DANJUAN_CACHE["ts"] > 3600 or not _DANJUAN_CACHE["items"]:
        data = _http_get_json(_DANJUAN_URL, {"User-Agent": _CSINDEX_UA})
        items = (data or {}).get("data", {}).get("items") or []
        if items:
            _DANJUAN_CACHE["ts"] = now
            _DANJUAN_CACHE["items"] = items
    for it in _DANJUAN_CACHE["items"]:
        if it.get("index_code") == danjuan_code:
            return {
                "pe": it.get("pe"),
                "pb": it.get("pb"),
                "pe_pct": it.get("pe_percentile"),
                "pb_pct": it.get("pb_percentile"),
                "div_yield": it.get("yeild"),
            }
    return None


class DataAccess(abc.ABC):
    """数据访问抽象接口 — 行情 + 估值。"""

    # A/H 市场 → 腾讯前缀映射（市场通用知识，非数据源特定）
    MARKET_PREFIX = {"A": "sh", "HK": "hk"}

    # ── 行情 ──
    @abc.abstractmethod
    def get_quote(self, symbol: str, market: str = "A") -> Optional[float]:
        """当前实时价格。symbol 为 6 位代码（A/H）。market: 'A' | 'HK'。

        失败返回 None。
        """
        ...

    # ── 估值数据 ──
    @abc.abstractmethod
    def get_pe_history(self, name: str) -> Optional[Tuple[List[float], date, date]]:
        """指数 PE 历史序列（legulegu）。返回 (values, start_date, end_date)，失败返回 None。"""
        ...

    @abc.abstractmethod
    def get_pb_history(self, name: str) -> Optional[Tuple[List[float], date, date]]:
        """指数 PB 历史序列（legulegu）。返回 (values, start_date, end_date)，失败返回 None。"""
        ...

    def get_pe_history_official(self, index_code: str) -> Optional[Tuple[List[float], date, date]]:
        """指数 PE 历史序列（中证指数官网官方源）。index_code 为中证指数代码（如 '000300'）。

        返回 (values, start_date, end_date)，失败返回 None。
        可选实现：不支持官方源的 DataAccess 返回 None，调用方降级到 legulegu/蛋卷。
        """
        return None

    def get_index_valuation_snapshot(self, danjuan_code: str) -> Optional[Dict[str, Optional[float]]]:
        """指数估值快照（蛋卷基金）。danjuan_code 如 'SH000300' / 'HKHSI'。

        返回 {pe, pb, pe_pct, pb_pct, div_yield}，失败返回 None。
        可选实现：不支持蛋卷源的 DataAccess 返回 None，调用方降级。
        """
        return None

    @abc.abstractmethod
    def get_dividend_yield(self, code: str) -> Optional[float]:
        """指数当前股息率（%）。code 为中证指数代码（如 '000300'）。失败返回 None。"""
        ...

    # ── 估值缓存 ──
    @abc.abstractmethod
    def get_cached_valuation(self, etf_code: str, max_age: Optional[int] = None) -> Optional[Tuple[dict, float]]:
        """估值缓存读。返回 (data_dict, age_seconds)，未命中/过期返回 None。"""
        ...

    @abc.abstractmethod
    def cache_valuation(self, etf_code: str, data: dict, ttl: int = 86400) -> None:
        """估值缓存写。"""
        ...


class MarketDataAccess(DataAccess):
    """私有实现 — 注入 market_data_layer（缓存/限流/容错）+ akshare。

    面向生产环境：
    - 行情走 SourceManager.fetch_tencent_rt（腾讯 https，已有 UA/超时）
    - 估值缓存走 DataCache（SQLite，持久化）
    - PE 官方源走中证指数官网（urllib 直连）；PE/PB/股息率降级走 akshare（legulegu + csindex）+ 蛋卷
    """

    def get_quote(self, symbol: str, market: str = "A") -> Optional[float]:
        from market_data_layer import SourceManager
        prefix = self.MARKET_PREFIX.get(market, "hk")
        data = SourceManager.fetch_tencent_rt(f"{prefix}{symbol}")
        if not data:
            return None
        return data.get("c")

    def get_pe_history(self, name: str) -> Optional[Tuple[List[float], date, date]]:
        import akshare as ak
        df = _fetch_legulegu_series(ak.stock_index_pe_lg, name)
        if df is None or df.empty:
            return None
        try:
            # 滚动市盈率 is the preferred PE metric
            pe_col = "滚动市盈率" if "滚动市盈率" in df.columns else "静态市盈率"
            values = df[pe_col].dropna().tolist()
            if not values:
                return None
            return values, df["日期"].iloc[0], df["日期"].iloc[-1]
        except Exception:
            return None

    def get_pb_history(self, name: str) -> Optional[Tuple[List[float], date, date]]:
        import akshare as ak
        df = _fetch_legulegu_series(ak.stock_index_pb_lg, name)
        if df is None or df.empty:
            return None
        try:
            # PB 列名通常是'市净率'或第二列数值列
            pb_col = None
            for col in df.columns:
                if "市净" in str(col) or "PB" in str(col).upper():
                    pb_col = col
                    break
            if pb_col is None:
                num_cols = df.select_dtypes(include=["float64", "int64"]).columns
                pb_col = num_cols[-1] if len(num_cols) > 1 else num_cols[0]
            values = df[pb_col].dropna().tolist()
            if not values:
                return None
            return values, df["日期"].iloc[0], df["日期"].iloc[-1]
        except Exception:
            return None

    def get_pe_history_official(self, index_code: str) -> Optional[Tuple[List[float], date, date]]:
        return _fetch_csindex_pe_history(index_code)

    def get_index_valuation_snapshot(self, danjuan_code: str) -> Optional[Dict[str, Optional[float]]]:
        return _fetch_danjuan_snapshot(danjuan_code)

    def get_dividend_yield(self, code: str) -> Optional[float]:
        try:
            import akshare as ak
            df = ak.stock_zh_index_value_csindex(symbol=code)
            if df is None or len(df) == 0:
                return None
            last = df.iloc[-1]
            div1 = last.get("股息率1")
            div2 = last.get("股息率2")
            # 优先用股息率1
            return float(div1) if div1 else (float(div2) if div2 else None)
        except Exception:
            return None

    def get_cached_valuation(self, etf_code: str, max_age: Optional[int] = None) -> Optional[Tuple[dict, float]]:
        try:
            from market_data_layer import DataCache
            # DataCache.get_cached_valuation 运行时支持 max_age=None（内部有 is-not-None 判断），
            # 但其签名标注为 int 而非 Optional[int]，此处透传 None 是安全的既有语义。
            data, age = DataCache().get_cached_valuation(etf_code, max_age)  # type: ignore[arg-type]
            if data is None:
                return None
            return data, age
        except Exception:
            return None

    def cache_valuation(self, etf_code: str, data: dict, ttl: int = 86400) -> None:
        try:
            from market_data_layer import DataCache
            DataCache().cache_valuation(etf_code, data, ttl)
        except Exception:
            pass  # cache write failure is non-fatal


class SimpleDataAccess(DataAccess):
    """公开实现 — urllib 腾讯行情 + akshare 直连。

    面向公开库用户：无 market_data_layer 依赖，估值缓存用进程内内存 dict
    （无 SQLite 持久化，重启即失效，够公开库演示/测试用）。
    """

    def __init__(self):
        self._valuation_mem: Dict[str, Tuple[dict, float]] = {}

    def get_quote(self, symbol: str, market: str = "A") -> Optional[float]:
        try:
            prefix = self.MARKET_PREFIX.get(market, "hk")
            url = f"http://qt.gtimg.cn/q={prefix}{symbol}"
            raw = urllib.request.urlopen(url, timeout=10).read().decode("gbk")
            for line in raw.strip().split("\n"):
                if '="' not in line:
                    continue
                data = line[line.index('="') + 2:].rstrip('";\n\r')
                parts = data.split("~")
                if len(parts) > 3 and parts[3]:
                    return float(parts[3])
        except Exception:
            pass
        return None

    def get_pe_history(self, name: str) -> Optional[Tuple[List[float], date, date]]:
        import akshare as ak
        df = _fetch_legulegu_series(ak.stock_index_pe_lg, name)
        if df is None or df.empty:
            return None
        try:
            pe_col = "滚动市盈率" if "滚动市盈率" in df.columns else "静态市盈率"
            values = df[pe_col].dropna().tolist()
            if not values:
                return None
            return values, df["日期"].iloc[0], df["日期"].iloc[-1]
        except Exception:
            return None

    def get_pb_history(self, name: str) -> Optional[Tuple[List[float], date, date]]:
        import akshare as ak
        df = _fetch_legulegu_series(ak.stock_index_pb_lg, name)
        if df is None or df.empty:
            return None
        try:
            pb_col = None
            for col in df.columns:
                if "市净" in str(col) or "PB" in str(col).upper():
                    pb_col = col
                    break
            if pb_col is None:
                num_cols = df.select_dtypes(include=["float64", "int64"]).columns
                pb_col = num_cols[-1] if len(num_cols) > 1 else num_cols[0]
            values = df[pb_col].dropna().tolist()
            if not values:
                return None
            return values, df["日期"].iloc[0], df["日期"].iloc[-1]
        except Exception:
            return None

    def get_pe_history_official(self, index_code: str) -> Optional[Tuple[List[float], date, date]]:
        return _fetch_csindex_pe_history(index_code)

    def get_index_valuation_snapshot(self, danjuan_code: str) -> Optional[Dict[str, Optional[float]]]:
        return _fetch_danjuan_snapshot(danjuan_code)

    def get_dividend_yield(self, code: str) -> Optional[float]:
        try:
            import akshare as ak
            df = ak.stock_zh_index_value_csindex(symbol=code)
            if df is None or len(df) == 0:
                return None
            last = df.iloc[-1]
            div1 = last.get("股息率1")
            div2 = last.get("股息率2")
            return float(div1) if div1 else (float(div2) if div2 else None)
        except Exception:
            return None

    def get_cached_valuation(self, etf_code: str, max_age: Optional[int] = None) -> Optional[Tuple[dict, float]]:
        entry = self._valuation_mem.get(etf_code)
        if not entry:
            return None
        data, fetched_at = entry
        age = time.time() - fetched_at
        ttl = max_age if max_age is not None else 86400
        if age > ttl:
            return None
        return data, age

    def cache_valuation(self, etf_code: str, data: dict, ttl: int = 86400) -> None:
        self._valuation_mem[etf_code] = (data, time.time())


_data_access: Optional[DataAccess] = None


def get_data_access() -> DataAccess:
    """工厂：market_data_layer 可 import → MarketDataAccess，否则 → SimpleDataAccess。

    公开库不随包分发 market_data_layer（私有实现），此处用 find_spec 只检测模块
    存在性、不实际加载，避免 import 私有模块时的副作用。检测不到时回退
    SimpleDataAccess（urllib 腾讯 + 内存缓存），保证公开库开箱即用不崩。

    测试可通过 monkeypatch.setattr(data_access, '_data_access', mock) 注入；
    生产/公开库均通过此工厂获取默认实现。
    """
    global _data_access
    if _data_access is None:
        import importlib.util
        if importlib.util.find_spec("market_data_layer") is not None:
            _data_access = MarketDataAccess()
        else:
            _data_access = SimpleDataAccess()
    return _data_access
