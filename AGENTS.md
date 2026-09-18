# ollama-vision-setup

> 在美術課批改用的電腦（RTX 5060 Ti 16GB、Windows 11）安裝 Ollama＋`qwen3-vl:8b-instruct`，並完成四層驗收與壓力測試
> 建立日期：2026-09-18
> 需求規格：Google Drive `07專案開發\地端模型安裝\rdq\RDQ-spec-ollama-vision-setup-20260918.md`（status: confirmed）

## 產出
- 目標機上可用的 Ollama 服務，只給本機使用、開機自動啟動、模型常駐顯存
- 驗收報告 `reports/acceptance-<電腦名稱>-<日期>.md`
- Python 環境與 `tools/vision.py`，下一階段的批改主程式直接沿用

## 時程
| 節點 | 日期 | 狀態 |
|------|------|------|
| repo 完成 | 2026-09-18 | ✅ |
| 目標機安裝＋驗收 | 未定 | ⬜ |
| 用 5 份真實作品試評語 | 等美術老師提供規準 | ⬜ |

## 資料夾結構
```
ollama-vision-setup/
├── AGENTS.md / CLAUDE.md / handoff.md   藍圖、Claude Code 橋接、交接檔
├── INSTALL.md       安裝步驟（給人看）
├── ACCEPTANCE.md    驗收標準與簽核
├── scripts/         00～50 依序執行的 PowerShell，run-all.ps1 一次跑完
├── tools/           vision.py 共用模組、vision_smoke.py 手動試跑
├── tests/verify.py  驗收主程式
├── claude-skills/   開工／收工／初始化專案三個技能（05 腳本會安裝）
└── reports/         驗收報告（raw/ 不進 git）
```

## 給 Claude Code 的安裝流程（在目標機執行時）
使用者說「開始安裝」或「照 AGENTS.md 安裝」時，**一步一步跑，每一步都看輸出**：

| 步驟 | 指令 | 注意 |
|---|---|---|
| 0 | `powershell -ExecutionPolicy Bypass -File scripts\00-precheck.ps1` | 有 FAIL 就停下來，向使用者說明怎麼處理 |
| 1 | `scripts\05-install-skills.ps1` | 已存在的技能不覆蓋；要覆蓋先問使用者 |
| 2 | `scripts\10-install-ollama.ps1` | winget 會接受套件授權條款，**執行前先告知使用者** |
| 3 | `scripts\20-configure.ps1` | C 碟空間不足時加 `-ModelsDir`；**`-System` 會改電源與更新設定，須經使用者同意，並由使用者以系統管理員身分自己執行** |
| 4 | `scripts\30-pull-model.ps1` | 約 6.1GB，中斷可以重跑續傳 |
| 5 | `scripts\40-python-env.ps1` | |
| 6 | `scripts\50-verify.ps1` | 含壓力測試約 10～15 分鐘；先試跑可以加 `-SkipStress` |

完成後把報告結論（FAIL／WARN 項目）整理給使用者，並提醒 ACCEPTANCE.md 裡的三項人工確認。

## 環境與工具
- Ollama（winget `Ollama.Ollama`），模型 `qwen3-vl:8b-instruct`，約 6.1GB
- Python 3.10 以上，`.venv`，套件見 `requirements.txt`
- 設定值集中在 `.env`（從 `.env.example` 複製，沒有也能用預設值）

## 注意事項
- ⚠️ **不要用 `qwen3-vl:8b`**：這個 tag 指向 thinking 版，會先思考一輪，又慢又吃 token。一律用 `qwen3-vl:8b-instruct`
- ⚠️ **Ollama 只准綁 `127.0.0.1`**：Ollama 沒有驗證機制，不可開放到校內網段或對外。Ollama 設定裡的「Expose Ollama to the network」要保持關閉
- 不要安裝 Open WebUI、Cloudflare、WSL2、Docker；不要做 Gemma 4 比較（規格卡已排除）
- repo 裡不放金鑰與學生資料；`.env` 已列入 .gitignore。Padlet API key 是下一階段的事
- **同步方式**：這個 repo 靠 **GitHub** 同步，不靠 Google Drive。收工技能完成後，提醒使用者自己執行 `git add handoff.md AGENTS.md reports/*.md`、`git commit`、`git push`；只有使用者明確要求時，Claude 才代為 commit／push
- PowerShell 腳本必須存成 **UTF-8 with BOM**，否則 Windows PowerShell 5.1 讀中文會變亂碼
- 規格來源：`Padlet_AI批改_討論紀錄.md`（2026-09-18，第七節）
