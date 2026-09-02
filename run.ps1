$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand -or $pythonCommand.Source -like "*\WindowsApps\python.exe") {
        Write-Host "未找到可运行的 Python。请先安装 Python 3.11 或更高版本，并勾选 Add Python to PATH。" -ForegroundColor Yellow
        exit 1
    }
    python -c "import sys; assert sys.version_info >= (3, 11)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "需要可运行的 Python 3.11 或更高版本。" -ForegroundColor Yellow
        exit 1
    }
    python -m venv .venv
}

& $venvPython -m pip install -r requirements.txt
& $venvPython -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    Write-Host "自动填写所需的浏览器组件安装失败。仍可手动运行程序，但一键官网填写暂不可用。" -ForegroundColor Yellow
}
& $venvPython -m streamlit run app.py --server.address 127.0.0.1 --server.port 8765
