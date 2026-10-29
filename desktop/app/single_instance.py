"""单实例控制。

桌面端必须保证同一时间只有一个进程持有本地服务与 WebView2 数据目录，
否则会出现两个窗口写同一份 IndexedDB 的诡异状态。

Windows 用命名互斥量实现；其它平台退化为纯端口探测（由调用方处理）。
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

log = logging.getLogger(__name__)

ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "Local\\PromptOptimizerApp.SingleInstance"


class InstanceGuard:
    """持有命名互斥量；进程退出时释放。"""

    def __init__(self) -> None:
        self._handle: int | None = None
        self.already_running = False

    def acquire(self) -> bool:
        """返回 True 表示本次是第一个实例。"""
        if not hasattr(ctypes, "windll"):
            return True
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
            handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
            last_error = ctypes.get_last_error()
        except OSError as exc:  # pragma: no cover - 极端环境
            log.warning("创建互斥量失败，跳过单实例检查：%s", exc)
            return True

        if not handle:
            log.warning("创建互斥量返回空句柄，跳过单实例检查")
            return True

        self._handle = handle
        if last_error == ERROR_ALREADY_EXISTS:
            self.already_running = True
            return False
        return True

    def release(self) -> None:
        if self._handle and hasattr(ctypes, "windll"):
            try:
                ctypes.WinDLL("kernel32").CloseHandle(self._handle)
            except OSError:
                pass
            self._handle = None


def focus_existing_window(title_hint: str = "Prompt Optimizer") -> bool:
    """尽力把已存在的窗口拉到前台，成功返回 True。"""
    if not hasattr(ctypes, "windll"):
        return False

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except OSError:
        return False

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found: list[int] = []

    def _callback(hwnd, _lparam):  # type: ignore[no-untyped-def]
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if title_hint.lower() in buffer.value.lower():
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(EnumWindowsProc(_callback), 0)
    if not found:
        return False

    hwnd = found[0]
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except OSError:
        return False
