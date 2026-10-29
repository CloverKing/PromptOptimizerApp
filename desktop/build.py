"""一键构建桌面端单文件 exe。

做的事情就三步：
    1. 在上游目录里编译前端（core → ui → web）；
    2. 把 web/dist 拷到 desktop/web-dist，并写入 build-info.json；
    3. 调 PyInstaller 按 PromptOptimizerApp.spec 打成单文件 exe。

用法：
    python desktop/build.py                # 完整构建
    python desktop/build.py --skip-pack    # 只编译前端（调试页面用）
    python desktop/build.py --skip-node    # 复用已有前端产物，只重新打包
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

DESKTOP_DIR = Path(__file__).resolve().parent
REPO_ROOT = DESKTOP_DIR.parent
UPSTREAM_DIR = REPO_ROOT / "upstream"
WEB_DIST_SRC = UPSTREAM_DIR / "packages" / "web" / "dist"
WEB_DIST_DST = DESKTOP_DIR / "web-dist"
ASSETS_DIR = DESKTOP_DIR / "assets"
BUILD_DIR = DESKTOP_DIR / "build"
DIST_DIR = DESKTOP_DIR / "dist"
SPEC_FILE = DESKTOP_DIR / "PromptOptimizerApp.spec"
BUILD_INFO = DESKTOP_DIR / "build-info.json"


# ------------------------------------------------------------------ 控制台编码

def _ensure_utf8_stdio() -> None:
    """Windows 上默认控制台编码可能是 cp1252/cp936，直接打印中文会 UnicodeEncodeError。

    CI（GitHub Actions 的 pwsh）尤其明显，这里统一把标准输出切到 UTF-8。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


_ensure_utf8_stdio()


# ------------------------------------------------------------------ 工具函数


def log(message: str) -> None:
    print(f"\033[36m[build]\033[0m {message}", flush=True)


def fail(message: str) -> NoReturn:
    print(f"\033[31m[build] {message}\033[0m", file=sys.stderr)
    raise SystemExit(1)


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    display = " ".join(cmd)
    log(f"$ {display}")
    merged_env = {**os.environ, **(env or {})}
    # 上游 .npmrc 里写死了 engine-strict=true，Node 版本略有差异时会直接拒绝安装，
    # 这里显式放开：编译产物与 Node 小版本无关。
    merged_env.setdefault("npm_config_engine_strict", "false")
    result = subprocess.run(cmd, cwd=str(cwd), env=merged_env)  # noqa: S603
    if result.returncode != 0:
        fail(f"命令执行失败（exit {result.returncode}）：{display}")


def find_pnpm() -> str:
    for name in ("pnpm.cmd", "pnpm"):
        found = shutil.which(name)
        if found:
            return found
    fail("未找到 pnpm。请先安装：npm install -g pnpm@10")
    return "pnpm"


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(f"读取 {path} 失败：{exc}")
        return {}


def git_output(args: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def resolve_app_version(explicit: str | None) -> str:
    """版本优先级：--app-version > APP_VERSION 环境变量 > git tag > version.py"""
    if explicit:
        return explicit.strip()
    env_version = os.environ.get("APP_VERSION", "").strip()
    if env_version:
        return env_version

    tag = git_output(["describe", "--tags", "--exact-match", "HEAD"], REPO_ROOT)
    match = re.search(r"(\d+\.\d+\.\d+)", tag)
    if match:
        return match.group(1)

    version_file = DESKTOP_DIR / "app" / "version.py"
    match = re.search(r'__version__\s*=\s*"([^"]+)"', version_file.read_text(encoding="utf-8"))
    return match.group(1) if match else "0.0.0"


def upstream_commit() -> str:
    """从 subtree 的 squashed 提交信息里取上游真实 commit。"""
    message = git_output(["log", "-1", "--format=%s", "--grep=Squashed 'upstream/'"], REPO_ROOT)
    match = re.search(r"commit ([0-9a-f]{7,40})", message)
    if match:
        return match.group(1)[:7]
    return git_output(["rev-parse", "--short", "HEAD"], REPO_ROOT)


# ------------------------------------------------------------------ 构建步骤


def ensure_upstream() -> None:
    if not (UPSTREAM_DIR / "package.json").is_file():
        fail("未找到 upstream/，请先执行 git subtree pull（见 README「同步上游」）。")


def check_node() -> None:
    """上游 .npmrc 里 engine-strict=true，Node 版本不符会直接拒绝构建，提前给出人话提示。"""
    required = str(read_json(UPSTREAM_DIR / "package.json").get("engines", {}).get("node", ""))
    try:
        raw = subprocess.run(  # noqa: S603
            ["node", "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        fail("未找到 node，请安装 Node.js 24（https://nodejs.org）后重试")

    current = tuple(int(part) for part in re.findall(r"\d+", raw)[:3])
    wanted = tuple(int(part) for part in re.findall(r"\d+", required)[:3])
    if wanted and current < wanted:
        fail(
            f"当前 Node {raw} 低于上游要求（{required}）。\n"
            "  解决办法：安装 Node 24 并确保 `node -v` 输出 24.x，再执行本脚本。\n"
            "  提示：可以用 nvm-windows / fnm 切换版本，无需卸载现有 Node。"
        )
    log(f"Node {raw}（上游要求 {required or '未声明'}）")


def install_dependencies(pnpm: str) -> None:
    if (UPSTREAM_DIR / "node_modules").is_dir():
        log("依赖已存在，跳过安装（如需强制重装请删除 upstream/node_modules）")
        return
    run([pnpm, "install", "--frozen-lockfile"], cwd=UPSTREAM_DIR)


def build_frontend(pnpm: str) -> None:
    log("编译 @prompt-optimizer/core")
    run([pnpm, "build:core"], cwd=UPSTREAM_DIR)
    log("编译 @prompt-optimizer/ui")
    run([pnpm, "build:ui"], cwd=UPSTREAM_DIR)
    log("编译 @prompt-optimizer/web")
    run([pnpm, "-F", "@prompt-optimizer/web", "build"], cwd=UPSTREAM_DIR)

    if not (WEB_DIST_SRC / "index.html").is_file():
        fail(f"前端产物缺失：{WEB_DIST_SRC / 'index.html'}")


def sync_web_dist() -> None:
    log(f"同步前端产物 → {WEB_DIST_DST}")
    if WEB_DIST_DST.exists():
        shutil.rmtree(WEB_DIST_DST)
    shutil.copytree(WEB_DIST_SRC, WEB_DIST_DST)


def write_build_info(app_version: str) -> None:
    upstream_pkg = read_json(UPSTREAM_DIR / "package.json")
    info = {
        "app_version": app_version,
        "upstream_version": str(upstream_pkg.get("version", "unknown")),
        "upstream_commit": upstream_commit(),
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "builder": f"Python {platform.python_version()} / {platform.system()}",
    }
    BUILD_INFO.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"构建信息：{info['app_version']} (upstream {info['upstream_version']}+{info['upstream_commit']})")


def write_version_resource(app_version: str) -> Path:
    """生成 Windows 可执行文件的版本资源（右键属性里能看到版本号）。"""
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    parts = (app_version.split(".") + ["0", "0", "0"])[:4]
    numbers = ", ".join(str(int(p)) if p.isdigit() else "0" for p in parts)
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({numbers}), prodvers=({numbers}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('080404B0', [
      StringStruct('CompanyName', 'CloverKing'),
      StringStruct('FileDescription', 'Prompt Optimizer Desktop'),
      StringStruct('FileVersion', '{app_version}'),
      StringStruct('InternalName', 'PromptOptimizerApp'),
      StringStruct('LegalCopyright', 'AGPL-3.0-only. Based on linshenkx/prompt-optimizer.'),
      StringStruct('OriginalFilename', 'PromptOptimizerApp.exe'),
      StringStruct('ProductName', 'Prompt Optimizer'),
      StringStruct('ProductVersion', '{app_version}')])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""
    path = BUILD_DIR / "version_info.txt"
    path.write_text(content, encoding="utf-8")
    return path


def package(app_version: str) -> Path:
    write_version_resource(app_version)
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR),
        str(SPEC_FILE),
    ]
    run(cmd, cwd=DESKTOP_DIR, env={"PO_APP_VERSION": app_version})

    exe = DIST_DIR / ("PromptOptimizerApp.exe" if platform.system() == "Windows" else "PromptOptimizerApp")
    if not exe.is_file():
        fail(f"打包结束但未找到产物：{exe}")
    size_mb = exe.stat().st_size / 1024 / 1024
    log(f"完成：{exe}（{size_mb:.1f} MB）")
    return exe


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 Prompt Optimizer 桌面端")
    parser.add_argument("--skip-node", action="store_true", help="跳过前端编译，复用已有 web-dist")
    parser.add_argument("--skip-install", action="store_true", help="跳过 pnpm install")
    parser.add_argument("--skip-pack", action="store_true", help="只编译前端，不打包 exe")
    parser.add_argument("--app-version", default=None, help="指定桌面端版本号")
    args = parser.parse_args()

    started = time.time()
    ensure_upstream()
    app_version = resolve_app_version(args.app_version)

    if not args.skip_node:
        check_node()
        pnpm = find_pnpm()
        if not args.skip_install:
            install_dependencies(pnpm)
        build_frontend(pnpm)
        sync_web_dist()
    elif not (WEB_DIST_DST / "index.html").is_file():
        fail("指定了 --skip-node，但 desktop/web-dist 不存在，请先完整构建一次")

    write_build_info(app_version)
    if args.skip_pack:
        log(f"前端已就绪：{WEB_DIST_DST}（耗时 {time.time() - started:.0f}s）")
        return 0

    package(app_version)
    log(f"全部完成，耗时 {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
