# restart-review.ps1：重開審核頁（含作業牆管理），讓老師用到最新程式
# 用法：powershell -ExecutionPolicy Bypass -File scripts\restart-review.ps1
# - 只停掉佔用審核頁連接埠的 python；佔用的是別的程式就停下來，不動它
# - 新的伺服器在背景執行（沒有視窗），輸出寫到 logs\review.log；設定照 .env（GRADER_HOST、GRADER_PORT、密碼）
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Stop'

$port = 8765
$envFile = Join-Path $RepoRoot '.env'
if (Test-Path $envFile) {
    $line = Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -match '^\s*GRADER_PORT\s*=\s*(\d+)' } | Select-Object -First 1
    if ($line -match '(\d+)\s*$') { $port = [int]$Matches[1] }
}

Write-Step "停止目前的審核頁（連接埠 $port）"
$pids = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($id in $pids) {
    $p = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($p -and $p.ProcessName -notmatch '^python') {
        Write-Fail "連接埠 $port 被 $($p.ProcessName)（PID $id）佔用，不是審核頁，沒有動它"
        exit 1
    }
    try {
        Stop-Process -Id $id -Force -ErrorAction Stop
        Write-Pass "已停止 PID $id"
    } catch {
        Write-Fail "停不掉 PID $id：$($_.Exception.Message)（可能是用系統管理員身分開的，請到工作管理員結束它）"
        exit 1
    }
}
if (-not $pids) { Write-Info '目前沒有在執行' }

$deadline = (Get-Date).AddSeconds(10)
while ((Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }

Write-Step '啟動新的審核頁'
$logs = Join-Path $RepoRoot 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null
$py = Join-Path $RepoRoot '.venv\Scripts\python.exe'
Start-Process -FilePath $py -ArgumentList '-X', 'utf8', 'grade.py', 'review', '--no-browser' -WorkingDirectory $RepoRoot `
    -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'review.log') -RedirectStandardError (Join-Path $logs 'review.err.log')

$deadline = (Get-Date).AddSeconds(20)
while (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
$listen = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $listen) {
    Write-Fail "20 秒內沒有啟動，請看 logs\review.err.log"
    Get-Content (Join-Path $logs 'review.err.log') -Tail 15 -ErrorAction SilentlyContinue
    exit 1
}
Write-Pass "審核頁已啟動（PID $($listen.OwningProcess)，綁定 $($listen.LocalAddress):$port）"
if ($listen.LocalAddress -ne '127.0.0.1') {
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } |
        ForEach-Object { Write-Info "校內網址：http://$($_.IPAddress):$port/portal（作業牆管理）、http://$($_.IPAddress):$port/（評語審核）" }
} else {
    Write-Info "只給本機：http://127.0.0.1:$port/portal"
}
