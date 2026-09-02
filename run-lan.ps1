$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envPath = Join-Path $projectRoot ".env"

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "请先运行 run.ps1 完成首次安装。" -ForegroundColor Yellow
    exit 1
}
if (-not (Test-Path -LiteralPath $envPath)) {
    Write-Host "没有找到 .env，请先从 .env.example 复制并设置 APP_ACCESS_PASSWORD。" -ForegroundColor Yellow
    exit 1
}

$passwordLine = Get-Content -LiteralPath $envPath |
    Where-Object { $_ -match '^APP_ACCESS_PASSWORD=' } |
    Select-Object -Last 1
$password = if ($passwordLine) { ($passwordLine -split '=', 2)[1].Trim() } else { "" }
if ($password.Length -lt 12) {
    Write-Host "局域网启动要求 APP_ACCESS_PASSWORD 至少 12 个字符。" -ForegroundColor Red
    exit 1
}

$lanAddress = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
        $_.IPAddress -notlike '127.*' -and
        $_.IPAddress -notlike '169.254.*' -and
        $_.AddressState -eq 'Preferred' -and
        $_.InterfaceAlias -notmatch 'Meta|Loopback|WSL|vEthernet'
    } |
    Select-Object -First 1 -ExpandProperty IPAddress

Write-Host "AI Job Agent 将仅供当前局域网访问。" -ForegroundColor Green
if ($lanAddress) {
    Write-Host "其他设备访问：http://$($lanAddress):8765" -ForegroundColor Cyan
}
Write-Host "不要在路由器中把 8765 端口映射到公网。" -ForegroundColor Yellow

& $venvPython -m streamlit run app.py `
    --server.address 0.0.0.0 `
    --server.port 8765 `
    --server.enableCORS true `
    --server.enableXsrfProtection true
