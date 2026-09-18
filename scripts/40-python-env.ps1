# 40-python-env.ps1：建立 Python 虛擬環境（.venv）並安裝套件
. "$PSScriptRoot\common.ps1"

Write-Step '1. 找 Python 3.10 以上'
# 回傳 @{Exe=執行檔; Pre=前置參數}，找不到回傳 $null
function Get-PythonCmd {
    $candidates = @(@{ Exe = 'py'; Pre = @('-3') }, @{ Exe = 'python'; Pre = @() })
    foreach ($c in $candidates) {
        if (Get-Command $c.Exe -ErrorAction SilentlyContinue) {
            $ver = & $c.Exe @($c.Pre + @('-c', "import sys; print('%d.%d' % sys.version_info[:2])")) 2>$null
            if ($ver -and [version]$ver -ge [version]'3.10') { return $c }
        }
    }
    return $null
}
$py = Get-PythonCmd
if (-not $py) {
    Write-Info '沒有合適的 Python，用 winget 安裝 Python 3.12…'
    winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
    Update-SessionPath
    $py = Get-PythonCmd
}
if (-not $py) { Write-Fail '仍找不到 Python，請開新的 PowerShell 視窗再重跑'; exit 1 }
Write-Pass "使用 $($py.Exe) $($py.Pre -join ' ')：$(& $py.Exe @($py.Pre + @('--version')) 2>&1)"

Write-Step '2. 建立 .venv'
$venv = Join-Path $script:RepoRoot '.venv'
$vpy = Join-Path $venv 'Scripts\python.exe'
if (Test-Path $vpy) { Write-Pass '.venv 已存在，沿用' }
else {
    & $py.Exe @($py.Pre + @('-m', 'venv', $venv))
    if (-not (Test-Path $vpy)) { Write-Fail '.venv 建立失敗'; exit 1 }
    Write-Pass ".venv 建立於 $venv"
}

Write-Step '3. 安裝套件'
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install -r (Join-Path $script:RepoRoot 'requirements.txt') --quiet
if ($LASTEXITCODE -ne 0) { Write-Fail 'pip 安裝失敗'; exit 1 }
& $vpy -X utf8 -c "import requests, PIL, ollama, opencc; print('套件匯入正常：requests', requests.__version__, '/ Pillow', PIL.__version__)"
if ($LASTEXITCODE -ne 0) { Write-Fail '套件匯入失敗'; exit 1 }
Write-Pass 'Python 環境完成'
exit 0
