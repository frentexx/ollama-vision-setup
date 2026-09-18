# 20-configure.ps1：設定 Ollama（只給本機、模型常駐），可選擇一併調整電源與 Windows Update
#
# 用法：
#   只設定 Ollama（不需系統管理員）：
#     powershell -ExecutionPolicy Bypass -File scripts\20-configure.ps1
#   模型改放其他磁碟：
#     ... 20-configure.ps1 -ModelsDir D:\ollama-models
#   連同「關閉睡眠、設定更新使用時段」一起做（需以系統管理員身分開 PowerShell）：
#     ... 20-configure.ps1 -System
param(
    [string]$ModelsDir = '',
    [switch]$System,
    [int]$ActiveStart = 7,    # Windows Update 不會在 07:00～22:00 自動重開機
    [int]$ActiveEnd = 22
)
. "$PSScriptRoot\common.ps1"

Write-Step '1. Ollama 環境變數（使用者層級）'
$vars = [ordered]@{
    'OLLAMA_HOST'       = '127.0.0.1:11434'   # 只給本機，其他電腦連不到
    'OLLAMA_KEEP_ALIVE' = '-1'                # 模型常駐顯存，避免每次冷啟動
}
if ($ModelsDir) {
    New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
    $vars['OLLAMA_MODELS'] = $ModelsDir
}
foreach ($k in $vars.Keys) {
    $old = [Environment]::GetEnvironmentVariable($k, 'User')
    [Environment]::SetEnvironmentVariable($k, $vars[$k], 'User')
    # 目前視窗也要設，否則下面重開的 Ollama 會沿用舊值
    Set-Item -Path "env:$k" -Value $vars[$k]
    Write-Pass "$k = $($vars[$k])$(if ($old -and $old -ne $vars[$k]) { "（原本是 $old）" })"
}
if ($ModelsDir) { Write-Warn '改了模型位置：舊位置的模型不會自動搬過去，30-pull-model.ps1 會重新下載' }

Write-Step '2. 重開 Ollama 讓設定生效'
$v = Restart-Ollama
if ($v) { Write-Pass "Ollama 已重開，版本 $v" } else { Write-Fail 'Ollama 重開後 60 秒內沒有回應' }

$listen = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue
$addrs = ($listen | Select-Object -ExpandProperty LocalAddress -Unique) -join ', '
if ($listen -and -not ($listen | Where-Object { $_.LocalAddress -notin '127.0.0.1', '::1' })) {
    Write-Pass "只監聽本機：$addrs"
} else {
    Write-Fail "監聽位址是 $addrs。請打開 Ollama 視窗 → Settings，關閉 Expose Ollama to the network，再重跑本腳本"
}

$startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
if (Get-ChildItem $startup -Filter 'Ollama*.lnk' -ErrorAction SilentlyContinue) { Write-Pass '開機自動啟動：啟動資料夾有 Ollama 捷徑' }
else { Write-Warn '啟動資料夾沒有 Ollama 捷徑；請確認 Ollama 是用安裝程式裝的，或手動建立捷徑' }

Write-Step '3. 電源與 Windows Update'
if (-not $System) {
    Write-Info '未加 -System，略過。上課期間要防止睡眠和更新重開機，請以系統管理員身分重跑並加 -System'
} elseif (-not (Test-IsAdmin)) {
    Write-Fail '-System 需要系統管理員：請在 PowerShell 圖示上按右鍵 →「以系統管理員身分執行」後重跑'
} else {
    # 插電時不睡眠、不休眠（螢幕關閉不影響運算，維持系統預設）
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    Write-Pass '插電時：睡眠＝永不、休眠＝永不'

    # 使用時段內 Windows Update 不自動重開機（最長 18 小時）
    $wu = 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings'
    if (-not (Test-Path $wu)) { New-Item -Path $wu -Force | Out-Null }
    Set-ItemProperty -Path $wu -Name 'SmartActiveHoursState' -Value 0 -Type DWord
    Set-ItemProperty -Path $wu -Name 'ActiveHoursStart' -Value $ActiveStart -Type DWord
    Set-ItemProperty -Path $wu -Name 'ActiveHoursEnd' -Value $ActiveEnd -Type DWord
    Write-Pass ("Windows Update 使用時段：{0:00}:00～{1:00}:00（此時段內不自動重開機）" -f $ActiveStart, $ActiveEnd)
}

Write-Host ''
if ($script:FailCount -gt 0) { Write-Host "設定未完成：$script:FailCount 項 FAIL" -ForegroundColor Red; exit 1 }
Write-Host '設定完成' -ForegroundColor Green
exit 0
