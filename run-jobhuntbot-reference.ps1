$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$dashboardRoot = Join-Path $projectRoot "vendor\JobHuntBot\dashboard"

if (-not (Test-Path -LiteralPath (Join-Path $dashboardRoot "server.js"))) {
    Write-Host "未找到 vendor\JobHuntBot。请先下载参考项目。" -ForegroundColor Yellow
    exit 1
}

$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
if ($nodeCommand) {
    $nodeExecutable = $nodeCommand.Source
} else {
    $bundledNode = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    if (-not (Test-Path -LiteralPath $bundledNode)) {
        Write-Host "未找到 Node.js。请先安装 Node.js 20 或更高版本。" -ForegroundColor Yellow
        exit 1
    }
    $nodeExecutable = $bundledNode
}

Set-Location -LiteralPath $dashboardRoot
Write-Host "JobHuntBot 参考看板：http://127.0.0.1:8420/dashboard.html" -ForegroundColor Green
Write-Host "按 Ctrl+C 停止。请勿向原始看板写入真实个人资料。" -ForegroundColor Yellow
& $nodeExecutable server.js
