"""PyInstaller 打包入口。

单独放在包外，避免打包后以 ``__main__`` 运行时丢失包上下文导致相对导入失败。
源码调试仍可用 ``python -m app``（在 desktop 目录下）。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
