<#
.SYNOPSIS
    把上游 prompt-optimizer 的最新代码同步进 upstream/，可选地立刻重新打包。

.DESCRIPTION
    upstream/ 是用 git subtree 引入的源项目，本仓库只在它之外新增文件（desktop/、scripts/ 等），
    因此同步时不会产生冲突。同步后上游版本号会打印出来，方便写更新说明。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/sync-upstream.ps1
    powershell -ExecutionPolicy Bypass -File scripts/sync-upstream.ps1 -Ref develop -Rebuild
#>
param(
    [string]$Ref = "develop",
    [switch]$Rebuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root

function Get-UpstreamVersion {
    $pkg = Join-Path $root "upstream/package.json"
    if (Test-Path $pkg) {
        (Get-Content $pkg -Raw | ConvertFrom-Json).version
    } else {
        "unknown"
    }
}

try {
    if (-not (git remote | Select-String -Quiet "^upstream$")) {
        git remote add upstream https://github.com/linshenkx/prompt-optimizer.git
    }

    $before = Get-UpstreamVersion
    Write-Host "[sync] 当前上游版本：$before" -ForegroundColor Cyan

    git fetch upstream $Ref
    if ($LASTEXITCODE -ne 0) { throw "git fetch 失败" }

    $incoming = git log -1 --format="%h %s" FETCH_HEAD
    Write-Host "[sync] 目标提交：$incoming" -ForegroundColor Cyan

    git subtree pull --prefix=upstream upstream $Ref --squash
    if ($LASTEXITCODE -ne 0) { throw "git subtree pull 失败（可能需要在 upstream/ 内手动解决冲突）" }

    $after = Get-UpstreamVersion
    Write-Host "[sync] 同步完成：$before -> $after" -ForegroundColor Green

    if ($Rebuild) {
        Write-Host "[sync] 开始重新打包…" -ForegroundColor Cyan
        python desktop/build.py
        if ($LASTEXITCODE -ne 0) { throw "构建失败" }
    } else {
        Write-Host "[sync] 如需重新打包：python desktop/build.py" -ForegroundColor Yellow
    }
}
finally {
    Pop-Location
}
