"""内置静态服务器。

为什么不用 ``file://`` 直接打开页面？
1. 前端是标准 SPA，路由/资源路径都以站点根目录为基准；
2. 浏览器只对 http(s) origin 提供稳定的 IndexedDB / localStorage 隔离，
   ``file://`` 下不同路径会被当成不同 origin，数据随时丢失；
3. 本地 http 还能规避 WebView 对 file 协议的一部分安全限制。

因此这里起一个只监听 127.0.0.1 的轻量服务器，端口固定，
保证 origin 恒定（http://127.0.0.1:<port>），用户的会话历史、收藏才能长期留存。
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import socket
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .paths import APP_ID

log = logging.getLogger(__name__)

HEALTH_PATH = "/__app/health"
SERVICE_WORKER_PATHS = frozenset({"/sw.js", "/service-worker.js"})

# 关掉 Service Worker 时返回的替身脚本：接管旧缓存并清空，避免升级后仍加载旧前端。
_SW_STUB = """\
// PromptOptimizer desktop: service worker disabled (static assets are bundled).
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map((key) => caches.delete(key)));
    } catch (error) { /* ignore */ }
    await self.clients.claim();
  })());
});
"""

_EXTRA_MIME = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".map": "application/json",
    ".wasm": "application/wasm",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json",
    ".ico": "image/x-icon",
}


@dataclass
class ServerContext:
    """服务器需要的只读上下文。"""

    web_root: Path
    app_version: str
    upstream_version: str
    disable_service_worker: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


def _guess_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _EXTRA_MIME:
        return _EXTRA_MIME[suffix]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _cache_control_for(relative: str) -> str:
    if relative in {"index.html", "config.js", "sw.js", "manifest.webmanifest"}:
        return "no-store"
    if relative.startswith("assets/"):
        # Vite 产物带内容哈希，可长期缓存
        return "public, max-age=31536000, immutable"
    return "no-cache"


class _Handler(BaseHTTPRequestHandler):
    server_version = "PromptOptimizerDesktop"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # ---------- 基础设施 ----------

    @property
    def ctx(self) -> ServerContext:
        return self.server.ctx  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        log.debug("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch(head_only=False)

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch(head_only=True)

    def handle_one_request(self) -> None:
        """浏览器提前断开连接是常态（keep-alive 被取消），不必抛堆栈。"""
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self.send_header("Allow", "GET, HEAD, OPTIONS")
        self.end_headers()

    # ---------- 路由 ----------

    def _dispatch(self, head_only: bool) -> None:
        if not self._host_allowed():
            self._send_text(HTTPStatus.FORBIDDEN, "Forbidden", head_only)
            return

        parsed = urlparse(self.path)
        path = unquote(parsed.path) or "/"

        if path == HEALTH_PATH:
            self._send_health(head_only)
            return

        if self.ctx.disable_service_worker and path in SERVICE_WORKER_PATHS:
            self._send_bytes(
                HTTPStatus.OK,
                _SW_STUB.encode("utf-8"),
                "text/javascript; charset=utf-8",
                "no-store",
                head_only,
            )
            return

        target = self._resolve(path)
        if target is not None:
            self._send_file(target, head_only)
            return

        # 单页应用兜底：无扩展名的路径交给 index.html，由前端路由处理
        if "." not in Path(path).name:
            index = (self.ctx.web_root / "index.html").resolve()
            if index.is_file():
                self._send_file(index, head_only)
                return

        self._send_text(HTTPStatus.NOT_FOUND, "Not Found", head_only)

    def _resolve(self, url_path: str) -> Path | None:
        relative = url_path.lstrip("/")
        if not relative:
            relative = "index.html"
        candidate = (self.ctx.web_root / relative).resolve()
        try:
            candidate.relative_to(self.ctx.web_root.resolve())
        except ValueError:
            log.warning("拒绝越权路径访问：%s", url_path)
            return None
        if candidate.is_dir():
            index = candidate / "index.html"
            return index if index.is_file() else None
        return candidate if candidate.is_file() else None

    # ---------- 响应 ----------

    def _host_allowed(self) -> bool:
        host = self.headers.get("Host", "")
        if not host:
            return True
        hostname = host.rsplit(":", 1)[0].strip("[]").lower()
        return hostname in {"127.0.0.1", "localhost", "::1"}

    def _send_health(self, head_only: bool) -> None:
        payload = {
            "app_id": APP_ID,
            "app_version": self.ctx.app_version,
            "upstream_version": self.ctx.upstream_version,
            "pid": os.getpid(),
        }
        self._send_bytes(
            HTTPStatus.OK,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            "no-store",
            head_only,
        )

    def _send_text(self, status: HTTPStatus, message: str, head_only: bool) -> None:
        self._send_bytes(
            status,
            message.encode("utf-8"),
            "text/plain; charset=utf-8",
            "no-store",
            head_only,
        )

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        cache_control: str,
        head_only: bool,
    ) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache_control)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            log.debug("客户端提前断开：%s", self.path)

    def _send_file(self, path: Path, head_only: bool) -> None:
        try:
            size = path.stat().st_size
        except OSError as exc:
            log.warning("读取文件失败 %s：%s", path, exc)
            self._send_text(HTTPStatus.INTERNAL_SERVER_ERROR, "Read Error", head_only)
            return

        relative = path.relative_to(self.ctx.web_root.resolve()).as_posix()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", _guess_type(path))
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", _cache_control_for(relative))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if head_only:
                return
            with path.open("rb") as fh:
                while chunk := fh.read(256 * 1024):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            log.debug("客户端提前断开：%s", self.path)
        except OSError as exc:
            log.warning("发送文件失败 %s：%s", path, exc)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows 下 SO_REUSEADDR 会允许抢占已被监听的端口，
    # 这里关掉才能准确判断「端口是否被占用」。
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], context: ServerContext) -> None:
        super().__init__(address, _Handler)
        self.ctx = context


class WebAppServer:
    """127.0.0.1 上的静态站点服务。"""

    def __init__(
        self,
        context: ServerContext,
        preferred_port: int,
        fallback_count: int = 9,
    ) -> None:
        self._context = context
        self._preferred_port = preferred_port
        self._fallback_count = fallback_count
        self._httpd: _Server | None = None
        self._thread: threading.Thread | None = None
        self.port = preferred_port
        self.port_switched = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> int:
        ports = [self._preferred_port] + [
            self._preferred_port + offset for offset in range(1, self._fallback_count + 1)
        ]
        last_error: OSError | None = None
        for port in ports:
            try:
                httpd = _Server(("127.0.0.1", port), self._context)
            except OSError as exc:
                last_error = exc
                continue
            self._httpd = httpd
            self.port = port
            self.port_switched = port != self._preferred_port
            self._thread = threading.Thread(
                target=httpd.serve_forever,
                name="poapp-http",
                kwargs={"poll_interval": 0.2},
                daemon=True,
            )
            self._thread.start()
            log.info("本地服务已启动：%s（静态目录 %s）", self.url, self._context.web_root)
            if self.port_switched:
                log.warning(
                    "默认端口 %s 被占用，已改用 %s；由于数据按 origin 隔离，本次看到的数据可能与之前不同",
                    self._preferred_port,
                    port,
                )
            return port

        raise RuntimeError(f"无法在 127.0.0.1 上绑定端口 {self._preferred_port}：{last_error}")

    def stop(self) -> None:
        if self._httpd is None:
            return
        log.info("正在关闭本地服务")
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception:  # noqa: BLE001 - 关闭阶段不应阻塞退出
            log.debug("关闭本地服务时出现异常", exc_info=True)
        self._httpd = None


def probe_instance(port: int, timeout: float = 0.6) -> dict[str, Any] | None:
    """探测该端口上是否已经运行着本应用（用于单实例复用）。"""
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{port}{HEALTH_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - 固定本机地址
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, socket.timeout):
        return None
    return payload if payload.get("app_id") == APP_ID else None
