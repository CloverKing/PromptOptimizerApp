# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

目标：一个不依赖外部 Python 环境的单文件 exe。
关键点是把 WebView2 的托管 DLL 放到解包根目录——pywebview 在单文件模式下只在
``sys._MEIPASS`` 顶层查找 ``Microsoft.Web.WebView2.Core.dll``。
"""

import struct
from pathlib import Path

DESKTOP = Path(SPECPATH).resolve()
APP_DIR = DESKTOP / "app"
WEB_DIST = DESKTOP / "web-dist"
ASSETS = DESKTOP / "assets"
VERSION_FILE = DESKTOP / "build" / "version_info.txt"
BUILD_INFO = DESKTOP / "build-info.json"

if not (WEB_DIST / "index.html").is_file():
    raise SystemExit("缺少 desktop/web-dist/index.html，请先运行：python desktop/build.py")

datas = [
    (str(WEB_DIST), "web"),
    (str(APP_DIR / "inject.js"), "."),
]
if ASSETS.is_dir():
    datas.append((str(ASSETS), "assets"))
if BUILD_INFO.is_file():
    datas.append((str(BUILD_INFO), "."))

# ---- WebView2 / pythonnet 运行时 ----
import importlib.util  # noqa: E402

_webview_spec = importlib.util.find_spec("webview")
if _webview_spec is None or not _webview_spec.submodule_search_locations:
    raise SystemExit("未安装 pywebview，请先执行：pip install -r desktop/requirements.txt")

_webview_lib = Path(next(iter(_webview_spec.submodule_search_locations))) / "lib"

for _dll in ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll"):
    _path = _webview_lib / _dll
    if _path.is_file():
        datas.append((str(_path), "."))

_arch = "win-x64" if struct.calcsize("P") * 8 == 64 else "win-x86"
_loader = _webview_lib / "runtimes" / _arch / "native" / "WebView2Loader.dll"
binaries = [(str(_loader), ".")] if _loader.is_file() else []

hiddenimports = [
    "clr",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
]

excludes = [
    "tkinter",
    "unittest",
    "pydoc_data",
    "test",
    "numpy",
    "pandas",
    "matplotlib",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "gi",
    "pytest",
]

a = Analysis(
    [str(DESKTOP / "run.py")],
    pathex=[str(DESKTOP), str(APP_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PromptOptimizerApp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "icon.ico") if (ASSETS / "icon.ico").is_file() else None,
    version=str(VERSION_FILE) if VERSION_FILE.is_file() else None,
)
