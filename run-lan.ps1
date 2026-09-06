$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "请先运行 run.ps1 完成首次安装。" -ForegroundColor Yellow
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

Write-Host "Charles-ai-job-agent 将在当前局域网公开访问，无应用内密码。" -ForegroundColor Green
if ($lanAddress) {
    Write-Host "其他设备访问：http://$($lanAddress):8765" -ForegroundColor Cyan
}
Write-Host "同一网络中的任何人都能打开；不要在公共 Wi-Fi 使用或把 8765 端口映射到公网。" -ForegroundColor Yellow

& $venvPython -m streamlit run app.py `
    --server.address 0.0.0.0 `
    --server.port 8765 `
    --server.enableCORS true `
    --server.enableXsrfProtection true
