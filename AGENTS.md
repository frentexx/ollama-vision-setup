# ollama-vision-setup

> 在美術課批改用的電腦（RTX 5060 Ti 16GB、Windows 11）安裝 Ollama＋`qwen3-vl:8b-instruct`，並完成四層驗收與壓力測試
> 建立日期：2026-09-18
> 需求規格：Google Drive `07專案開發\地端模型安裝\rdq\RDQ-spec-ollama-vision-setup-20260918.md`（status: confirmed）

## 產出
- 目標機上可用的 Ollama 服務，只給本機使用、開機自動啟動、模型常駐顯存
- 驗收報告 `reports/acceptance-<電腦名稱>-<日期>.md`
- Python 環境與 `tools/vision.py`，下一階段的批改主程式直接沿用
- 批改系統 `grade.py`：讀 Padlet 作品 → 依 rubric 寫評語草稿 → 老師審核頁核准 → 留言回 Padlet（說明見 GRADING.md）

## 時程
| 節點 | 日期 | 狀態 |
|------|------|------|
| repo 完成 | 2026-09-18 | ✅ |
| 目標機安裝＋驗收 | 2026-09-18（ppsh-VR-1） | 🟡 自動驗收全過，三項人工確認待做 |
| 批改系統雛形（grade.py、審核頁） | 2026-09-18 | 🟡 已接真的 Padlet（讀取、下載、草稿 OK），「發布評語」尚未實測 |
| 審核頁校內開放（方案 C：密碼＋防火牆） | 2026-09-18 | ✅ 美術老師可從校內連線 |
| 作業牆自動建立網頁＋後台（`/portal`） | 2026-09-18 規格卡 confirmed（修訂 1：區段＝作業） | 🟡 已實作；真的建牆 17 秒通過（測試牆 `padlet.com/pad02_98/padlet-s023p27e54sox5ezh9aj`）；學生上傳→分區段批改→發布還沒實測 |
| rubric 分層＋一致率報告＋檢查點題庫 | 方向已定 | ⬜ 等美術老師用文字給一份作業標準 |
| 用 5 份真實作品試評語 | 等美術老師提供規準 | ⬜ |

## 資料夾結構
```
ollama-vision-setup/
├── AGENTS.md / CLAUDE.md / handoff.md   藍圖、Claude Code 橋接、交接檔
├── INSTALL.md       安裝步驟（給人看）
├── ACCEPTANCE.md    驗收標準與簽核
├── scripts/         00～50 依序執行的 PowerShell，run-all.ps1 一次跑完
├── tools/           vision.py 共用模組、vision_smoke.py 手動試跑
├── grade.py         批改系統入口（run／fetch／draft／review／export／check）
├── grader/          批改系統：padlet.py、rubric.py、feedback.py、workflow.py、server.py、static/review.html；
│                    portal.py＋static/portal.html＝作業牆管理（建牆、範本、rubric、背景抓作業）
├── rubrics/         評分規準（TOML），範例-攝影三作業.toml（共同檢查點＋[[assignments]] 各區段作業）
├── GRADING.md       批改系統使用說明（給老師看）
├── docs/            方案說明（給美術老師檢視）
├── rdq/             RDQ 需求規格卡（status: draft 的不得動工）
├── data/            批改進度與學生作品（含個資，不進 git）
├── tests/verify.py  驗收主程式；tests/test_grader.py 批改系統離線測試
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
- repo 裡不放金鑰與學生資料；`.env`、`data/` 已列入 .gitignore。Padlet API key 放 `.env` 的 `PADLET_API_KEY`
- ⚠️ **評語發到 Padlet 後無法用系統撤回**：只有老師在審核頁核准、再按「發布已核准」並輸入「發布」才會送出。Claude 不可代為核准或發布
- 審核頁預設只綁 `127.0.0.1:8765`。要給美術老師從校內其他電腦用（方案 C）：`.env` 設 `GRADER_HOST=0.0.0.0`，**必須先** `grade.py set-password`（沒密碼伺服器會拒絕啟動）；防火牆規則由使用者以系統管理員身分自己加，只允許 LocalSubnet。密碼只存雜湊，Claude 不經手密碼
- **重開審核頁（讓老師用到新程式）**：`powershell -ExecutionPolicy Bypass -File "D:uwen\地端LLM\ollama-vision-setup\scriptsestart-review.ps1"`（已在 `D:uwen\地端LLM\.claude\settings.local.json` 允許 Claude 直接執行）。只停佔用連接埠的 python，背景執行、輸出在 `logs\`。若舊伺服器是用系統管理員身分開的會停不掉，要使用者到工作管理員結束
- 學生繳交的實測狀況：會把照片「留言」在說明卡底下、標題亂寫（例如只寫「33」、寫別人名字），所以分組一律以 Padlet 帳號／名字為準，標題只當參考並提醒老師
- AI 建板（create_board）產生的說明卡 author 是 null；新版子預設關閉留言
- **一個區段＝一份作業**（2026-09-18 使用者定案）：建牆時老師設定區段數量與名稱；批改單位是「學生×區段」，評語留在該區段那篇。rubric 用 `[[assignments]]`（name 對應區段名稱，包含即算）＋共同 `[[criteria]]`；沒有 assignments 的舊 rubric 走「整面牆一份作業」（unit=student）
- rubric 的 `work` 定義「什麼算作品」，判斷請重傳用；不可寫死「拍到人物就不是作品」（人物攝影會被誤判）
- 作業牆管理（`/portal`）用 AI Recipe 建牆；API 不能改牆的設定，所以「開留言、關貼文審核、確認版型」是老師手動待辦，三項勾完才給學生網址／QR。AI 建的欄位 sortIndex 會重複，讀回檢查只比對欄位名稱，順序靠人工確認
- 帶 Padlet key 的請求只准送 `api.padlet.dev/v1/` 與 `padlet.dev/api/public/v1/`（padlet.py 的 TRUSTED），statusUrl 是 Padlet 回傳的網址，一律先檢查
- Padlet MCP（mathruffian-dot/padlet-mcp，**鎖定 commit 8b1ac45**＝0.2.0，已審查）設定在上層 `D:\fuwen\地端LLM\.mcp.json`，key 由使用者環境變數 `PADLET_API_KEY` 提供；更新版本前要先審查新版程式碼
- 測試看板：`https://padlet.com/pad02_98/202609-s023ouxqszxj3kriwhu2`（202609美術課程作業繳交，三欄：全貌／特寫／過程）
- 批改的 prompt 經驗（qwen3-vl 8B）：JSON 欄位要「evidence 在 level 前面」，等級才會跟觀察一致；模型常把沒做到的地方當優點稱讚，所以 feedback.py 有一道檢查，被抓到就只重寫那一句
- 「評分」只到各檢查點等級與分數，給老師看（審核頁、CSV）；貼給學生的留言不提分數與等級
- **同步方式**：這個 repo 靠 **GitHub** 同步，不靠 Google Drive。收工技能完成後，提醒使用者自己執行 `git add handoff.md AGENTS.md reports/*.md`、`git commit`、`git push`；只有使用者明確要求時，Claude 才代為 commit／push
- PowerShell 腳本必須存成 **UTF-8 with BOM**，否則 Windows PowerShell 5.1 讀中文會變亂碼
- 規格來源：`Padlet_AI批改_討論紀錄.md`（2026-09-18，第七節）
