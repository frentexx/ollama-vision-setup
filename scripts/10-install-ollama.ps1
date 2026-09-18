# 10-install-ollama.ps1：用 winget 安裝 Ollama（已安裝就略過）
. "$PSScriptRoot\common.ps1"

Write-Step '安裝 Ollama'
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Pass "已安裝：$(ollama --version 2>&1)，略過"
} else {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Fail '沒有 winget。請手動到 https://ollama.com/download/windows 下載 OllamaSetup.exe 安裝後，再重跑本腳本'
        exit 1
    }
    # 官方套件 Ollama.Ollama；裝在使用者帳號下，不需要系統管理員
    winget install --id Ollama.Ollama -e --silent --accept-package-agreements --accept-source-agreements
    Update-SessionPath
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        Write-Fail '安裝後仍找不到 ollama 指令，請關閉這個視窗、開新的 PowerShell 再試'
        exit 1
    }
    Write-Pass "安裝完成：$(ollama --version 2>&1)"
}

Write-Step '確認服務啟動'
$v = Wait-Ollama 10
if (-not $v) { Write-Info '服務未執行，嘗試啟動…'; $v = Restart-Ollama }
if ($v) { Write-Pass "Ollama API 回應正常，版本 $v" } else { Write-Fail 'Ollama API 60 秒內沒有回應'; exit 1 }
exit 0
