# 安裝步驟

目標機：**RTX 5060 Ti 16GB、Windows 11**。全程約 40～60 分鐘，大部分時間花在下載模型和跑壓力測試。

## 事前準備（你自己操作）
1. 目標機登入 Windows，確認網路正常
2. 安裝 Git 並 clone：
   ```powershell
   winget install --id Git.Git -e
   # 開一個新的 PowerShell 視窗
   mkdir $env:USERPROFILE\Projects -Force; cd $env:USERPROFILE\Projects
   git clone https://github.com/<你的帳號>/ollama-vision-setup.git
   cd ollama-vision-setup
   ```
   第一次 clone private repo 時，會跳出瀏覽器要你登入 GitHub，請自己完成登入
3. （路線 A 才需要）安裝 Claude Code 並登入

---

## 路線 A：交給 Claude Code（主要）
在 `ollama-vision-setup` 資料夾開 Claude Code，說：

> 開工，然後照 AGENTS.md 開始安裝

Claude 會自動讀取 AGENTS.md，一步一步執行並回報。遇到以下兩件事會先問你：
- winget 要接受授權條款
- `-System` 要改電源和更新設定（這一步請你**自己用系統管理員身分**執行，見下方步驟 3）

## 路線 B：自己跑腳本（備援）
在 PowerShell 按右鍵，選「**以系統管理員身分執行**」，然後輸入：
```powershell
cd $env:USERPROFILE\Projects\ollama-vision-setup
powershell -ExecutionPolicy Bypass -File scripts\run-all.ps1 -System
```
任何一步失敗都會停下來，並說明卡在哪裡。處理好問題後重跑即可，已經完成的步驟會自動略過。

## 路線 C：逐步執行（出問題時用來找原因）
每一步都用 `powershell -ExecutionPolicy Bypass -File scripts\<檔名>` 執行：

| 步驟 | 腳本 | 做什麼 | 會改什麼 |
|---|---|---|---|
| 0 | `00-precheck.ps1` | 檢查系統、顯示卡驅動（需 ≥576）、磁碟、網路、連接埠 | 不改任何東西 |
| 1 | `05-install-skills.ps1` | 安裝開工／收工／初始化專案三個 Claude Code 技能 | `~\.claude\skills\` |
| 2 | `10-install-ollama.ps1` | 用 winget 安裝 Ollama 並啟動 | 安裝程式 |
| 3 | `20-configure.ps1 [-System] [-ModelsDir D:\...]` | 設定只給本機用、模型常駐；加 `-System` 會關閉睡眠並設定更新時段 07–22 | 使用者環境變數；`-System` 會改電源計畫和登錄檔 |
| 4 | `30-pull-model.ps1` | 下載 `qwen3-vl:8b-instruct`（6.1GB） | 模型檔 |
| 5 | `40-python-env.ps1` | 建立 `.venv`，沒有 Python 會先安裝 3.12 | `.venv\` |
| 6 | `50-verify.ps1 [-SkipStress]` | 四層驗收＋壓力測試，產出報告 | `reports\` |

## 常見問題
| 狀況 | 處理 |
|---|---|
| 腳本出現亂碼 | 檔案被存成沒有 BOM 的 UTF-8。請重新 clone，不要用記事本另存 |
| 「無法載入，因為這個系統上已停用指令碼執行」 | 用 `powershell -ExecutionPolicy Bypass -File ...` 執行，不必改系統原則 |
| 驅動 FAIL | 到 NVIDIA 官網更新驅動到 576 以上，重開機後重跑 00 |
| GPU 層不是 100% | 可能有其他程式占用顯存（遊戲、剪輯軟體），關掉後重跑 50 |
| 監聽位址不是 127.0.0.1 | Ollama 視窗 → Settings → 關閉 Expose Ollama to the network，重跑 20 |
| 單張超過 15 秒 | 先看報告裡的 tok/s；若 GPU 層正常，改用長邊 1024 或降低 `NUM_CTX` 再測 |

## 裝完之後
- 拿真實作品試跑：
  ```powershell
  .venv\Scripts\python.exe -X utf8 tools\vision_smoke.py 作品1.jpg 作品2.jpg
  ```
- 說「收工」，然後自己 push 報告：
  ```powershell
  git add handoff.md AGENTS.md reports/*.md
  git commit -m "目標機驗收"
  git push
  ```
