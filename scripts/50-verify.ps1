# 50-verify.ps1：執行驗收（四層＋壓力測試），報告寫到 reports\
# 用法：powershell -ExecutionPolicy Bypass -File scripts\50-verify.ps1 [-SkipStress]
param([switch]$SkipStress)
. "$PSScriptRoot\common.ps1"

$vpy = Join-Path $script:RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $vpy)) { Write-Fail '找不到 .venv，請先跑 40-python-env.ps1'; exit 1 }

$env:PYTHONUTF8 = '1'
$pyArgs = @('-X', 'utf8', (Join-Path $script:RepoRoot 'tests\verify.py'))
if ($SkipStress) { $pyArgs += '--skip-stress' }
else { Write-Info '含壓力測試，約需 10～15 分鐘，期間請勿關閉視窗' }

& $vpy @pyArgs
exit $LASTEXITCODE
