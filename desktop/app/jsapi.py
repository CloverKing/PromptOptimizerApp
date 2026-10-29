"""暴露给前端的 JS 桥接接口（``window.pywebview.api``）。

刻意保持极小：只提供桌面环境必需的能力，业务逻辑全部留在上游前端里，
这样上游升级时这个文件几乎不需要改动。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import webbrowser
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from . import updater
from .paths import APP_ID, GITHUB_REPO, UPSTREAM_REPO

if TYPE_CHECKING:  # pragma: no cover
    from .main import DesktopApp

log = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http", "https", "mailto"}


class DesktopApi:
    def __init__(self, app: "DesktopApp") -> None:
        self._app = app

    # ---------- 信息 ----------

    def get_app_info(self) -> dict[str, Any]:
        info = self._app.build_info()
        info.update(
            {
                "app_id": APP_ID,
                "data_dir": str(self._app.data_dir),
                "port": self._app.port,
                "platform": sys.platform,
                "github_repo": GITHUB_REPO,
                "upstream_repo": UPSTREAM_REPO,
            }
        )
        return info

    # ---------- 系统集成 ----------

    def open_external(self, url: str) -> bool:
        """用系统默认程序打开链接，仅允许 http/https/mailto。"""
        if not isinstance(url, str):
            return False
        parsed = urlparse(url)
        if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
            log.warning("拒绝打开非白名单协议：%s", url)
            return False
        try:
            webbrowser.open(url)
            return True
        except OSError as exc:
            log.warning("打开链接失败：%s", exc)
            return False

    def open_data_dir(self) -> bool:
        path = str(self._app.data_dir)
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606 - 打开用户自己的数据目录
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])  # noqa: S603,S607
            else:
                subprocess.Popen(["xdg-open", path])  # noqa: S603,S607
            return True
        except OSError as exc:
            log.warning("打开数据目录失败：%s", exc)
            return False

    # ---------- 更新 ----------

    def check_updates(self, silent: bool = True) -> dict[str, Any] | None:
        release = updater.fetch_latest(GITHUB_REPO)
        if release is None:
            if not silent:
                self._app.toast("检查更新失败，请稍后重试")
            return None
        current = self._app.build_info().get("app_version", "0.0.0")
        if updater.is_newer(release.tag, current):
            return {"available": True, "tag": release.tag, "url": release.url, "name": release.name}
        if not silent:
            self._app.toast(f"已是最新版本 {current}")
        return {"available": False, "tag": release.tag, "url": release.url, "name": release.name}

    # ---------- 生命周期 ----------

    def quit_app(self) -> bool:
        self._app.request_quit()
        return True
