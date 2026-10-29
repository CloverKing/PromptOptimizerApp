"""单实例控制与窗口激活。

桌面端必须保证同一时间只有一个进程持有本地服务与 WebView2 数据目录，
否则会出现两个窗口写同一份 IndexedDB 的诡异状态。

Windows 用命名互斥量判定「是否已有实例」；已有实例时，
第二个进程负责把对方的窗口从最小化恢复并置前。
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

log = logging.getLogger(__name__)

ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "Local\\PromptOptimizerApp.SingleInstance"

SW_RESTORE = 9
SW_SHOW = 5


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


# ---------------------------------------------------------------- 窗口查找 / 激活


def _enum_windows() -> list[tuple[int, str, int]]:
    """返回 (hwnd, title, pid) 列表，仅包含可见的顶层窗口。"""
    if not hasattr(ctypes, "windll"):
        return []
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except OSError:
        return []

    results: list[tuple[int, str, int]] = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd, _lparam):  # type: ignore[no-untyped-def]
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        results.append((int(hwnd), buffer.value, int(pid.value)))
        return True

    user32.EnumWindows(EnumWindowsProc(_callback), 0)
    return results


def find_window(title_hint: str, pid: int | None = None) -> int | None:
    """按标题关键字（可选限定进程）查找窗口句柄。"""
    hint = title_hint.lower()
    for hwnd, title, owner in _enum_windows():
        if pid is not None and owner != pid:
            continue
        if hint in title.lower():
            return hwnd
    return None


def activate_window(hwnd: int) -> bool:
    """把窗口从最小化恢复并置前。"""
    if not hasattr(ctypes, "windll"):
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
    except OSError:
        return False
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        else:
            user32.ShowWindow(hwnd, SW_SHOW)
        # 置前可能被前台锁拒绝，失败不算错误（窗口至少已经恢复）
        if not user32.SetForegroundWindow(hwnd):
            log.debug("SetForegroundWindow 被系统拒绝（窗口已恢复显示）")
        return True
    except OSError:
        log.debug("激活窗口失败", exc_info=True)
        return False


def focus_window(title_hint: str, pid: int | None = None) -> bool:
    """查找并激活窗口，成功返回 True。"""
    hwnd = find_window(title_hint, pid=pid)
    if hwnd is None:
        return False
    return activate_window(hwnd)


def focus_existing_window(title_hint: str = "Prompt Optimizer") -> bool:
    """兼容旧调用名。"""
    return focus_window(title_hint)
