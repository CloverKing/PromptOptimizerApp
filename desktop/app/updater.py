"""版本更新检查。

桌面端是「一个 exe = 一个上游版本」，没有增量更新能力，
所以在启动后异步查一次本仓库的 GitHub Release，发现新版本就用页面内提示引导用户去下载。
只发一个只读的 GET 请求，失败静默。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)

_API = "https://api.github.com/repos/{repo}/releases/latest"
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


@dataclass
class ReleaseInfo:
    tag: str
    url: str
    name: str


def normalize_version(value: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(value or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def fetch_latest(repo: str, timeout: float = 8.0) -> ReleaseInfo | None:
    request = urllib.request.Request(
        _API.format(repo=repo),
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "PromptOptimizerApp-Desktop",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - 固定 https://api.github.com
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.debug("检查更新失败：%s", exc)
        return None

    tag = str(payload.get("tag_name") or "")
    url = str(payload.get("html_url") or "")
    if not tag or not url:
        return None
    return ReleaseInfo(
        tag=tag,
        url=url,
        name=str(payload.get("name") or tag),
    )


def is_newer(candidate: str, current: str) -> bool:
    left = normalize_version(candidate)
    right = normalize_version(current)
    if not left or not right:
        return False
    return left > right
