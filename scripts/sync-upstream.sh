#!/usr/bin/env bash
# 把上游 prompt-optimizer 的最新代码同步进 upstream/（macOS / Linux / Git Bash）
#
# 用法：
#   ./scripts/sync-upstream.sh                 # 同步，不打包
#   ./scripts/sync-upstream.sh --rebuild       # 同步后立即重新打包
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REF="${UPSTREAM_REF:-develop}"
REBUILD=0
[[ "${1:-}" == "--rebuild" ]] && REBUILD=1

cd "$ROOT"

upstream_version() {
  if [[ -f upstream/package.json ]]; then
    python -c "import json;print(json.load(open('upstream/package.json',encoding='utf-8'))['version'])"
  else
    echo unknown
  fi
}

if ! git remote | grep -qx upstream; then
  git remote add upstream https://github.com/linshenkx/prompt-optimizer.git
fi

before="$(upstream_version)"
echo "[sync] 当前上游版本：$before"

git fetch upstream "$REF"
echo "[sync] 目标提交：$(git log -1 --format='%h %s' FETCH_HEAD)"

git subtree pull --prefix=upstream upstream "$REF" --squash

after="$(upstream_version)"
echo "[sync] 同步完成：$before -> $after"

if [[ "$REBUILD" == "1" ]]; then
  echo "[sync] 开始重新打包…"
  python desktop/build.py
else
  echo "[sync] 如需重新打包：python desktop/build.py"
fi
