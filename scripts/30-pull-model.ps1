# 30-pull-model.ps1：下載視覺模型（約 6.1GB）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\30-pull-model.ps1 [-Model qwen3-vl:8b-instruct]
param([string]$Model = '')
. "$PSScriptRoot\common.ps1"
if (-not $Model) { $Model = $script:DefaultModel }

Write-Step "下載 $Model"
if ($Model -eq 'qwen3-vl:8b') { Write-Warn 'qwen3-vl:8b 指向 thinking 版，批改評語請用 qwen3-vl:8b-instruct' }
ollama pull $Model
if ($LASTEXITCODE -ne 0) { Write-Fail "下載失敗（exit $LASTEXITCODE），可以直接重跑，會從中斷處續傳"; exit 1 }

Write-Step '確認模型'
ollama list
$caps = (Invoke-RestMethod -Uri "$script:OllamaUrl/api/show" -Method Post -Body (@{ model = $Model } | ConvertTo-Json) -ContentType 'application/json').capabilities
if ($caps -contains 'vision') { Write-Pass "模型能力：$($caps -join ', ')" } else { Write-Fail "模型沒有 vision 能力：$($caps -join ', ')"; exit 1 }
exit 0
