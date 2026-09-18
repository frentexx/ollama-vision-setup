# common.ps1：各步驟腳本共用的小工具（用 . 引入）
$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:FailCount = 0
$script:OllamaUrl = 'http://127.0.0.1:11434'
$script:DefaultModel = 'qwen3-vl:8b-instruct'   # 不要用 qwen3-vl:8b，那是 thinking 版

function Write-Step([string]$msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Pass([string]$msg) { Write-Host "  [PASS] $msg" -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Write-Fail([string]$msg) { Write-Host "  [FAIL] $msg" -ForegroundColor Red; $script:FailCount++ }
function Write-Info([string]$msg) { Write-Host "  [INFO] $msg" }

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# 重新讀取 PATH（winget 裝完後，目前視窗不會自動更新）
function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

# 等 Ollama API 起來，成功回傳版本字串，逾時回傳 $null
function Wait-Ollama([int]$Seconds = 60) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $v = Invoke-RestMethod -Uri "$script:OllamaUrl/api/version" -TimeoutSec 3
            return $v.version
        } catch { Start-Sleep -Seconds 2 }
    }
    return $null
}

# 關掉再重開 Ollama（改完環境變數後必須重開才生效）
function Restart-Ollama {
    Get-Process | Where-Object { $_.Name -like 'ollama*' } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    $app = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama app.exe'
    if (Test-Path $app) {
        Start-Process -FilePath $app
    } else {
        # 找不到桌面程式時，直接在背景跑服務
        Start-Process -FilePath 'ollama' -ArgumentList 'serve' -WindowStyle Hidden
    }
    return (Wait-Ollama 60)
}
