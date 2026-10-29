"""路径解析：同时兼容「源码运行」与「PyInstaller 单文件打包」两种形态。

单文件打包后，资源被解压到 ``sys._MEIPASS`` 临时目录（只读），
用户数据则固定写到 ``%LOCALAPPDATA%\\PromptOptimizerApp``，
避免每次启动换临时目录导致 IndexedDB / localStorage 全部丢失。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ID = "PromptOptimizerApp"
APP_DISPLAY_NAME = "Prompt Optimizer"
GITHUB_REPO = "CloverKing/PromptOptimizerApp"
UPSTREAM_REPO = "linshenkx/prompt-optimizer"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包产物中。"""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """只读资源根目录：单文件模式为解包临时目录，源码模式为 ``desktop/``。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def executable_dir() -> Path:
    """exe 所在目录（源码运行时等同项目 desktop 目录）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return bundle_dir()


def repo_root() -> Path:
    """仓库根目录（仅源码运行时有意义）。"""
    return Path(__file__).resolve().parents[2]


def web_dist_dir() -> Path:
    """定位前端静态产物目录。

    优先级：环境变量 > 打包内置资源 > exe 同级 web 目录 > 源码构建目录 > 上游构建目录。
    这样便携版可以直接把新的 web 产物放到 exe 旁边热替换，无需重新打包。
    """
    candidates: list[Path] = []
    env = os.environ.get("PO_WEB_DIST")
    if env:
        candidates.append(Path(env).expanduser())

    candidates += [
        bundle_dir() / "web",                       # PyInstaller datas 内置
        executable_dir() / "web",                   # 便携版旁置（可热替换）
        bundle_dir() / "web-dist",                  # desktop/web-dist
        repo_root() / "desktop" / "web-dist",
        repo_root() / "upstream" / "packages" / "web" / "dist",
    ]

    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return candidates[0]


def build_info_path() -> Path:
    """构建信息（上游版本 / commit / 构建时间）。"""
    for candidate in (bundle_dir() / "build-info.json", executable_dir() / "build-info.json"):
        if candidate.is_file():
            return candidate
    return bundle_dir() / "build-info.json"


def _default_local_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base)
    return Path.home() / "AppData" / "Local"


def _portable_root() -> Path | None:
    """便携模式：数据写在 exe 同级 ``data`` 目录。

    触发条件（任一）：设置了 ``PO_PORTABLE=1``；exe 同级存在 ``portable.txt``；
    exe 同级已存在 ``data`` 目录。目录必须可写，否则回落到 LOCALAPPDATA。
    """
    if os.environ.get("PO_PORTABLE") == "1":
        portable = True
    else:
        portable = (executable_dir() / "portable.txt").is_file() or (executable_dir() / "data").is_dir()

    if not portable:
        return None

    target = executable_dir() / "data"
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        return None
    return target


def user_data_dir() -> Path:
    """可写数据目录：配置、日志、WebView2 用户数据（IndexedDB 等业务数据都在这里）。"""
    override = os.environ.get("PO_DATA_DIR")
    if override:
        path = Path(override).expanduser()
    else:
        path = _portable_root() or (_default_local_appdata() / APP_ID)
    path.mkdir(parents=True, exist_ok=True)
    return path


def webview_storage_dir() -> Path:
    """WebView2 用户数据目录——应用的会话历史、收藏等持久化数据实际存放于此。"""
    path = user_data_dir() / "webview"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = user_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return user_data_dir() / "config.json"


def is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False
