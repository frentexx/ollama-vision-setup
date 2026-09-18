# 00-precheck.ps1：安裝前檢查（只讀取，不改任何設定）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\00-precheck.ps1 [-ModelsDir D:\ollama-models]
param([string]$ModelsDir = '')
. "$PSScriptRoot\common.ps1"

Write-Step '1. 作業系統'
$os = Get-CimInstance Win32_OperatingSystem
if ([int]$os.BuildNumber -ge 22000) { Write-Pass "$($os.Caption) (build $($os.BuildNumber))" }
else { Write-Fail "$($os.Caption) (build $($os.BuildNumber))，需要 Windows 11" }
Write-Info "電腦名稱：$(hostname)；目前是否為系統管理員：$(Test-IsAdmin)"

Write-Step '2. 顯示卡與驅動'
if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    Write-Fail '找不到 nvidia-smi：尚未安裝 NVIDIA 驅動，請到 nvidia.com 下載 Game Ready 或 Studio 驅動'
} else {
    $q = (nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader,nounits | Select-Object -First 1)
    $name, $drv, $mem = $q -split ',\s*'
    if ($name -match '5060 Ti') { Write-Pass "顯示卡：$name" } else { Write-Warn "顯示卡：$name（規格預設是 RTX 5060 Ti 16GB）" }
    # RTX 50 系列（Blackwell）至少要 576 版驅動
    if ([double]$drv -ge 576) { Write-Pass "驅動 $drv" } else { Write-Fail "驅動 $drv 太舊，RTX 50 系列請更新到 576 以上" }
    if ([int]$mem -ge 15000) { Write-Pass "顯存 $mem MiB" } else { Write-Fail "顯存 $mem MiB，不足 16GB" }
}

Write-Step '3. 磁碟空間（模型約 6GB，保留 30GB 餘裕）'
$drive = if ($ModelsDir) { (Split-Path -Qualifier $ModelsDir).TrimEnd(':') } else { $env:SystemDrive.TrimEnd(':') }
$free = [math]::Round((Get-PSDrive $drive).Free / 1GB, 1)
if ($free -ge 30) { Write-Pass "$($drive): 剩 $free GB" }
else { Write-Warn "$($drive): 只剩 $free GB，建議執行 20-configure.ps1 時用 -ModelsDir 把模型放到其他磁碟" }

Write-Step '4. 網路'
foreach ($u in 'https://ollama.com', 'https://registry.ollama.ai', 'https://github.com') {
    try {
        Invoke-WebRequest -Uri $u -Method Head -TimeoutSec 10 -UseBasicParsing | Out-Null
        Write-Pass "連得到 $u"
    } catch {
        # 有些網站回 401／404 也代表網路是通的
        if ($_.Exception.Response) { Write-Pass "連得到 $u（HTTP $([int]$_.Exception.Response.StatusCode)）" }
        else { Write-Fail "連不到 $u：$($_.Exception.Message)" }
    }
}

Write-Step '5. 工具'
foreach ($t in 'winget', 'git') {
    if (Get-Command $t -ErrorAction SilentlyContinue) { Write-Pass "$t 已安裝" } else { Write-Warn "$t 未安裝" }
}
if (Get-Command py -ErrorAction SilentlyContinue) { Write-Pass "Python：$(py -3 --version 2>&1)" }
elseif (Get-Command python -ErrorAction SilentlyContinue) { Write-Pass "Python：$(python --version 2>&1)" }
else { Write-Warn 'Python 未安裝（40-python-env.ps1 會自動安裝）' }
if (Get-Command ollama -ErrorAction SilentlyContinue) { Write-Info "Ollama 已安裝：$(ollama --version 2>&1)" }
else { Write-Info 'Ollama 尚未安裝（10-install-ollama.ps1 會安裝）' }

Write-Step '6. 連接埠 11434'
$busy = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue
if (-not $busy) { Write-Pass '11434 未被占用' }
else {
    $p = Get-Process -Id $busy[0].OwningProcess -ErrorAction SilentlyContinue
    if ($p.Name -like 'ollama*') { Write-Info "Ollama 已在執行，監聽 $($busy.LocalAddress -join ', ')" }
    else { Write-Fail "11434 被 $($p.Name) 占用" }
}

Write-Host ''
if ($script:FailCount -gt 0) { Write-Host "預檢未通過：$script:FailCount 項 FAIL，請先處理再往下" -ForegroundColor Red; exit 1 }
Write-Host '預檢通過，可以進行下一步' -ForegroundColor Green
exit 0
