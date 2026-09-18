# run-all.ps1：備援路徑——依序跑完所有步驟，任何一步失敗就停下
# 用法（建議以系統管理員身分開 PowerShell，才能套用 -System）：
#   powershell -ExecutionPolicy Bypass -File scripts\run-all.ps1 [-System] [-ModelsDir D:\ollama-models] [-SkipStress]
param([switch]$System, [string]$ModelsDir = '', [switch]$SkipStress)
. "$PSScriptRoot\common.ps1"

$steps = @(
    @{ File = '00-precheck.ps1';       Args = @{ ModelsDir = $ModelsDir } },
    @{ File = '05-install-skills.ps1'; Args = @{} },
    @{ File = '10-install-ollama.ps1'; Args = @{} },
    @{ File = '20-configure.ps1';      Args = @{ ModelsDir = $ModelsDir; System = $System } },
    @{ File = '30-pull-model.ps1';     Args = @{} },
    @{ File = '40-python-env.ps1';     Args = @{} },
    @{ File = '50-verify.ps1';         Args = @{ SkipStress = $SkipStress } }
)
foreach ($s in $steps) {
    Write-Host "`n########## $($s.File) ##########" -ForegroundColor Magenta
    $a = $s.Args
    & (Join-Path $PSScriptRoot $s.File) @a
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`n停在 $($s.File)（exit $LASTEXITCODE）。處理完問題後可以只重跑這一步，或重跑 run-all（已完成的步驟會自動略過）" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}
Write-Host "`n全部完成，驗收報告在 reports\ 資料夾" -ForegroundColor Green
exit 0
