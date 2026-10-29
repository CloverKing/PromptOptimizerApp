"""桌面端启动流程。

顺序：单实例 → 读配置 → 起本地服务（固定 origin）→ 开 WebView2 窗口 → 注入桌面脚本。
窗口关闭后回收服务与互斥量。任何一步失败都会落到 ``fatal()``，弹窗并提示日志位置。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from . import config as config_module
from . import paths, updater
from .jsapi import DesktopApi
from .server import ServerContext, WebAppServer, probe_instance
from .single_instance import InstanceGuard, focus_existing_window
from .version import __version__

log = logging.getLogger("poapp")

TITLE = "Prompt Optimizer"


# ---------------------------------------------------------------- 基础设施


def setup_logging(debug: bool = False) -> Path:
    log_file = paths.log_dir() / "app.log"
    handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.addHandler(handler)
    if debug or not paths.is_frozen():
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(console)
    return log_file


def show_error(message: str, detail: str = "") -> None:
    text = message if not detail else f"{message}\n\n{detail}"
    log.error("%s | %s", message, detail)
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10)  # MB_ICONERROR
            return
        except OSError:
            pass
    print(text, file=sys.stderr)


def load_build_info() -> dict[str, Any]:
    """构建信息：打包时由 build.py 写入，源码运行时从上游 package.json 兜底。"""
    info: dict[str, Any] = {
        "app_version": __version__,
        "upstream_version": "dev",
        "upstream_commit": "",
        "built_at": "",
    }
    candidate = paths.build_info_path()
    if candidate.is_file():
        try:
            info.update(json.loads(candidate.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            log.warning("构建信息读取失败：%s", exc)

    if info.get("upstream_version") in (None, "", "dev"):
        package_json = paths.repo_root() / "upstream" / "package.json"
        if package_json.is_file():
            try:
                info["upstream_version"] = json.loads(package_json.read_text(encoding="utf-8")).get(
                    "version", "dev"
                )
            except (OSError, ValueError):
                pass

    info["app_version"] = str(info.get("app_version") or __version__)
    return info


# ---------------------------------------------------------------- 应用主体


class DesktopApp:
    def __init__(self, config: dict[str, Any], data_dir: Path, debug: bool = False) -> None:
        self.config = config
        self.data_dir = data_dir
        self.debug = debug
        self._build_info = load_build_info()
        self.server: WebAppServer | None = None
        self.window: Any = None
        self._inject_source = ""
        self._update_checked = False
        self._lock = threading.Lock()

    # ---------- 对外信息 ----------

    @property
    def port(self) -> int:
        return self.server.port if self.server else int(self.config["port"])

    def build_info(self) -> dict[str, Any]:
        return dict(self._build_info)

    # ---------- 窗口内提示 ----------

    def _evaluate(self, script: str) -> None:
        with self._lock:
            if self.window is None:
                return
            try:
                self.window.evaluate_js(script)
            except Exception:  # noqa: BLE001 - 页面可能正在跳转
                log.debug("脚本注入失败（忽略）", exc_info=True)

    def toast(
        self,
        message: str,
        action_text: str | None = None,
        action_url: str | None = None,
        duration: int = 15000,
    ) -> None:
        options: dict[str, Any] = {"duration": duration}
        if action_text:
            options["actionText"] = action_text
        if action_url:
            options["actionUrl"] = action_url
        payload = json.dumps(options, ensure_ascii=False)
        text = json.dumps(message, ensure_ascii=False)
        self._evaluate(
            f"window.__poDesktop && window.__poDesktop.toast({text}, {payload});"
        )

    # ---------- 启动 ----------

    def prepare(self) -> int:
        web_root = paths.web_dist_dir()
        if not (web_root / "index.html").is_file():
            raise FileNotFoundError(
                "未找到前端资源（index.html）。\n"
                "源码运行请先执行：python desktop/build.py --skip-pack\n"
                f"当前查找目录：{web_root}"
            )

        context = ServerContext(
            web_root=web_root,
            app_version=str(self._build_info["app_version"]),
            upstream_version=str(self._build_info["upstream_version"]),
            disable_service_worker=bool(self.config["disable_service_worker"]),
        )
        existing = probe_instance(int(self.config["port"]))
        if existing:
            log.warning(
                "端口 %s 已被本应用的另一个实例占用（pid=%s），本次会另选端口",
                self.config["port"],
                existing.get("pid"),
            )
        self.server = WebAppServer(context, preferred_port=int(self.config["port"]))
        port = self.server.start()
        log.info("构建信息：%s", self._build_info)
        return port

    def run(self, server_only: bool = False) -> None:
        port = self.prepare()
        if server_only:
            print(f"服务已启动：http://127.0.0.1:{port}/  (Ctrl+C 退出)")
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
            finally:
                if self.server:
                    self.server.stop()
            return

        import webview  # 延迟导入：--version / --server-only 不需要 GUI 依赖

        extra_args = [str(item) for item in self.config.get("extra_browser_args") or []]
        if extra_args:
            os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = " ".join(extra_args)

        inject_path = paths.bundle_dir() / "inject.js"
        if inject_path.is_file():
            self._inject_source = inject_path.read_text(encoding="utf-8")
        else:
            log.warning("注入脚本缺失：%s", inject_path)

        window_config = self.config["window"]
        icon = paths.bundle_dir() / "assets" / "icon.ico"

        self.window = webview.create_window(
            title=TITLE,
            url=self.server.url if self.server else f"http://127.0.0.1:{self.port}/",
            width=int(window_config["width"]),
            height=int(window_config["height"]),
            min_size=(int(window_config["min_width"]), int(window_config["min_height"])),
            x=window_config.get("x"),
            y=window_config.get("y"),
            maximized=bool(window_config.get("maximized")),
            background_color="#ffffff",
            text_select=True,
            zoomable=True,
            confirm_close=False,
            js_api=DesktopApi(self),
        )
        self.window.events.loaded += self._on_loaded
        self.window.events.closing += self._on_closing

        webview.start(
            debug=bool(self.debug or self.config.get("devtools")),
            private_mode=False,
            storage_path=str(paths.webview_storage_dir()),
            http_server=False,
            icon=str(icon) if icon.is_file() else None,
            gui="edgechromium",
        )

        if self.server:
            self.server.stop()

    # ---------- 事件 ----------

    def _on_loaded(self) -> None:
        if self._inject_source:
            info = {
                "appVersion": self._build_info["app_version"],
                "upstreamVersion": self._build_info["upstream_version"],
                "platform": sys.platform,
            }
            script = self._inject_source.replace("__PO_INFO__", json.dumps(info, ensure_ascii=False))
            self._evaluate(script)

        if self.server and self.server.port_switched:
            self.toast(
                f"默认端口 {self.config['port']} 被占用，已临时改用 {self.server.port}。"
                "本次可能看不到之前的会话记录，关闭占用该端口的程序后重启即可恢复。",
                duration=20000,
            )

        if not self._update_checked:
            self._update_checked = True
            if self.config.get("check_updates"):
                threading.Thread(target=self._check_updates, name="poapp-update", daemon=True).start()

    def _check_updates(self) -> None:
        release = updater.fetch_latest(paths.GITHUB_REPO)
        if release is None:
            return
        current = str(self._build_info["app_version"])
        if not updater.is_newer(release.tag, current):
            log.info("已是最新版本 %s（远端 %s）", current, release.tag)
            return
        log.info("发现新版本 %s（当前 %s）", release.tag, current)
        self.toast(
            f"发现新版本 {release.tag}，当前版本 {current}",
            action_text="去下载",
            action_url=release.url,
            duration=30000,
        )

    def _on_closing(self) -> bool:
        try:
            width = int(self.window.width)
            height = int(self.window.height)
            if 400 <= width <= 10000 and 300 <= height <= 10000:
                self.config.setdefault("window", {})["width"] = width
                self.config["window"]["height"] = height
            config_module.save(paths.config_path(), self.config)
        except Exception:  # noqa: BLE001
            log.debug("保存窗口尺寸失败", exc_info=True)

        if self.server:
            threading.Thread(target=self.server.stop, daemon=True).start()
        return True

    def request_quit(self) -> None:
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:  # noqa: BLE001
                log.debug("销毁窗口失败", exc_info=True)


# ---------------------------------------------------------------- 入口


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="PromptOptimizerApp", description="Prompt Optimizer 桌面版")
    parser.add_argument("--version", action="store_true", help="打印版本信息后退出")
    parser.add_argument("--debug", action="store_true", help="开启调试日志与开发者工具")
    parser.add_argument("--port", type=int, default=None, help="覆盖本地服务端口")
    parser.add_argument("--data-dir", default=None, help="覆盖数据目录（调试用）")
    parser.add_argument("--server-only", action="store_true", help="只启动本地服务，不打开窗口")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.version:
        info = load_build_info()
        print(f"PromptOptimizerApp {info['app_version']} (upstream {info['upstream_version']})")
        return 0

    if args.data_dir:
        os.environ["PO_DATA_DIR"] = args.data_dir

    log_file = setup_logging(args.debug)
    log.info("启动桌面端 %s，Python %s", __version__, sys.version.split()[0])

    config = config_module.load(paths.config_path())
    if args.port:
        config["port"] = args.port
    if args.debug:
        config["devtools"] = True

    guard = InstanceGuard()
    is_first = guard.acquire()
    if not is_first:
        log.info("检测到已有实例在运行")
        if not focus_existing_window(TITLE):
            show_error("Prompt Optimizer 已在运行。", "请在任务栏中切换到已打开的窗口。")
        return 0

    try:
        app = DesktopApp(config, paths.user_data_dir(), debug=args.debug)
        app.run(server_only=args.server_only)
        return 0
    except FileNotFoundError as exc:
        show_error("前端资源缺失", str(exc))
        return 2
    except Exception as exc:  # noqa: BLE001 - 顶层兜底
        log.exception("启动失败")
        show_error("启动失败", f"{exc}\n\n详细日志：{log_file}")
        return 1
    finally:
        guard.release()


if __name__ == "__main__":
    raise SystemExit(main())
