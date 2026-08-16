"""
策略参数加载器

从 ~/.hermes/config/strategy_params.json 加载核心策略参数。
该文件不入 Git，Agent 只能看到 example 模板（全零值）。

用法:
    from strategy_param_loader import get_params
    p = get_params()
    cond4_max = p["account_b_builder"]["cond4_ma20_ma60_max"]

加载失败行为：记录 ERROR 日志 → 返回 None → 调用方应检查并安全降级。
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger("strategy_params")

_PARAMS_CACHE: Optional[Dict[str, Any]] = None
_PARAMS_PATH = Path.home() / ".hermes" / "config" / "strategy_params.json"


def _ensure_logging():
    """确保至少有一个 stderr handler"""
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)s [strategy_params] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(h)
        logger.setLevel(logging.WARNING)


def get_params() -> Optional[Dict[str, Any]]:
    """
    加载策略参数。首次调用后缓存，后续调用返回同一份数据。

    Returns:
        dict on success, None on failure (文件缺失/格式错误/权限不足)
    """
    global _PARAMS_CACHE

    if _PARAMS_CACHE is not None:
        return _PARAMS_CACHE

    _ensure_logging()

    path = os.path.expanduser(str(_PARAMS_PATH))

    if not os.path.exists(path):
        logger.error(
            "strategy_params.json 不存在: %s。"
            "所有策略参数将不可用，系统可能产生错误决策。"
            "请检查文件是否被误删或权限是否正确。",
            path
        )
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            _PARAMS_CACHE = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(
            "strategy_params.json JSON 解析失败: %s。文件: %s",
            str(e), path
        )
        return None
    except PermissionError:
        logger.error(
            "strategy_params.json 无读取权限: %s", path
        )
        return None
    except Exception as e:
        logger.error(
            "strategy_params.json 加载异常: %s: %s", type(e).__name__, str(e)
        )
        return None

    # 基本完整性校验
    required_keys = [
        "account_b_builder", "bottom_accelerator",
        "macro_pipeline", "portfolio_builder", "market_data_layer"
    ]
    missing = [k for k in required_keys if k not in _PARAMS_CACHE]
    if missing:
        logger.error(
            "strategy_params.json 缺少关键配置段: %s。"
            "系统将使用 None 值，可能导致错误决策。",
            ", ".join(missing)
        )

    logger.info("策略参数加载成功 (version=%s)", _PARAMS_CACHE.get("_meta", {}).get("version", "?"))
    return _PARAMS_CACHE


def reload_params() -> Optional[Dict[str, Any]]:
    """强制重新加载（清除缓存）。用于参数热更新后验证。"""
    global _PARAMS_CACHE
    _PARAMS_CACHE = None
    return get_params()
