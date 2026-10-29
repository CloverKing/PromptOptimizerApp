"""配置文件读写。

配置放在数据目录下的 ``config.json``，首次启动自动生成。
所有字段都有默认值，用户只改自己关心的键即可，缺失/非法值会回落到默认值。
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    # 本地服务端口。桌面端所有数据（IndexedDB）按 origin 隔离，
    # 换端口等于换了一份数据，除非端口被占用，否则不要修改。
    "port": 17321,
    "window": {
        "width": 1280,
        "height": 860,
        "min_width": 960,
        "min_height": 620,
        "maximized": False,
        "x": None,
        "y": None,
    },
    # 桌面端固定加载内置产物，Service Worker 会导致升级后仍读到旧缓存，默认关闭。
    "disable_service_worker": True,
    # 启动后异步检查本仓库的 GitHub Release（仅提示，不自动下载）。
    "check_updates": True,
    # 打开 WebView2 开发者工具（调试用）。
    "devtools": False,
    # 追加给 WebView2 的浏览器参数，例如 ["--disable-web-security"]。
    "extra_browser_args": [],
}


def _coerce(default: Any, value: Any) -> Any:
    """按默认值的类型做保守转换，非法值一律回落。"""
    if default is None:
        return value
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        return int(value)
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    return value


def _merge(defaults: dict[str, Any], incoming: Any) -> dict[str, Any]:
    result = copy.deepcopy(defaults)
    if not isinstance(incoming, dict):
        return result
    for key, default_value in defaults.items():
        if key not in incoming:
            continue
        value = incoming[key]
        if isinstance(default_value, dict):
            result[key] = _merge(default_value, value)
        elif isinstance(default_value, list):
            result[key] = value if isinstance(value, list) else default_value
        else:
            result[key] = _coerce(default_value, value)
    return result


def load(path: Path) -> dict[str, Any]:
    """读取配置；文件不存在或损坏时生成一份默认配置。"""
    if not path.is_file():
        config = copy.deepcopy(DEFAULT_CONFIG)
        save(path, config)
        return config

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("配置文件无法解析（%s），本次使用默认配置：%s", path, exc)
        return copy.deepcopy(DEFAULT_CONFIG)

    merged = _merge(DEFAULT_CONFIG, raw)
    if merged.get("port") and not (1024 <= int(merged["port"]) <= 65535):
        log.warning("配置端口 %s 非法，回落到默认端口", merged["port"])
        merged["port"] = DEFAULT_CONFIG["port"]

    for axis in ("x", "y"):
        value = merged["window"].get(axis)
        if value is not None and not isinstance(value, int):
            merged["window"][axis] = None
    return merged


def save(path: Path, config: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("配置写入失败：%s", exc)
