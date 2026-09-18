# 05-install-skills.ps1：把 repo 附帶的 Claude Code 技能（開工／收工／初始化專案）裝到本機
# 已存在的技能預設不覆蓋；加 -Force 會先備份再覆蓋
# 用法：powershell -ExecutionPolicy Bypass -File scripts\05-install-skills.ps1 [-Force]
param([switch]$Force)
. "$PSScriptRoot\common.ps1"

$src = Join-Path $script:RepoRoot 'claude-skills'
$dst = Join-Path $env:USERPROFILE '.claude\skills'
New-Item -ItemType Directory -Force -Path $dst | Out-Null

Write-Step "安裝技能到 $dst"
foreach ($d in Get-ChildItem $src -Directory) {
    $target = Join-Path $dst $d.Name
    if (Test-Path $target) {
        if (-not $Force) { Write-Info "$($d.Name) 已存在，略過（要覆蓋請加 -Force）"; continue }
        $bak = "$target.bak-$(Get-Date -Format yyyyMMddHHmm)"
        Move-Item $target $bak
        Write-Info "$($d.Name) 舊版已備份到 $bak"
    }
    Copy-Item $d.FullName $target -Recurse
    Write-Pass "$($d.Name) 已安裝"
}
Write-Host ''
Write-Host '完成。重開 Claude Code 後，說「開工」「收工」「初始化專案」即可使用' -ForegroundColor Green
exit 0
