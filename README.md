# Antigravity Telegram Bot 🤖

**繁體中文** | [English](README_EN.md)

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![Telegram](https://img.shields.io/badge/Telegram-Bot%20API-blue.svg)](https://core.telegram.org/bots/api)
[![Google Antigravity](https://img.shields.io/badge/Antigravity-Agent%202.0-orange.svg)](https://antigravity.google)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

透過 Telegram 在手機、電腦或任何裝置上遠端調度你的 **Google Antigravity Agent & Gemini**。  
參考 [NousResearch Hermes Agent](https://github.com/nousresearch/hermes-agent) 的 Telegram 平台架構進行深度重構，具備工業級的網路容錯、Markdown 格式化引擎、多模態多媒體處理、智慧訊息批次防抖與互動式選單。

---

## ✨ 核心特色與升級亮點

### 1. 🛡️ 深度參考 Hermes 的 MarkdownV2 格式化引擎 (`formatter.py`)
- **GFM 表格轉換**：自動將 Markdown 管道表格（`| col | col |`）轉換為適合手機端閱讀的卡片式條列格式。
- **程式碼區塊完整性防護**：訊息超過 4096 字元需分段發送時，自動在截斷處補齊 ` ``` ` 並在下一段重新開啟對應語言標籤，程式碼格式絕不跑版破壞。
- **語法錯誤自動降級（Fallback）**：若因特殊字元導致 Telegram Markdown 解析報錯，自動剝除格式標記以乾淨純文字（Plain Text）重新發送，**訊息 100% 成功送達不丟失**。

### 2. ⚡ 智慧訊息與相簿批次防抖 (Debounce & Batching)
- **長文字自動聚合**：當使用者在 Telegram 貼上超過 4000 字元長文時，Telegram 客戶端會自動切分成多條訊息連續發送。Bot 會自動聚合為單一完整 Prompt 再傳給 Agent。
- **相簿/多圖聚合**：連續發送多張圖片或 Telegram 相簿時，自動合併下載並傳送給 Gemini 進行多圖視覺分析。

### 3. 🎙️ 全方位多模態支援 (Multimodal Ingestion)
- 📸 **圖片與相簿**：自動下載高畫質照片至快取，交由 Gemini 視覺模型分析。
- 🎙️ **語音訊息**：支援 Telegram `.ogg` 語音輸入，直接交給 Gemini 聽覺與語音理解。
- 📁 **檔案與文件**：支援傳送 Python 程式碼、PDF、JSON、CSV 等，Agent 可直接讀取與除錯。
- 📍 **地理位置**：支援 Telegram 位置圖釘分享。
- 📤 **生成檔案主動回傳**：若 Agent 在回答中生成圖片或匯出文件，Bot 會自動擷取本機檔案並作為 Telegram 原生附件發送給使用者。

### 4. 🎛️ 互動式 UI 與即時進度更新
- **點擊式模型切換器**：輸入 `/model` 即彈出帶分頁的 Inline Keyboard 按鈕，支援 Gemini 3.8 Flash、Gemini 3.7 Flash、Claude Opus 4.6 (Thinking) 等一鍵切換。
- **即時執行狀態與進度指示**：執行時持續維持 Typing 指示器，並動態解析 Agent 工具調用（🔧 工具執行、🔍 搜尋、📄 檔案讀寫、🧠 思考），附帶「🛑 中止執行 (Cancel)」按鈕。

### 5. 🧠 本機雙軌持久記憶庫 (Hermes Agent 架構)
- **雙軌本地文字庫**：採用與 Hermes Agent 相容架構，本機維護 `USER.md`（個人風格、溝通習慣、指令原則）與 `MEMORY.md`（伺服器 IP、帳密、部署端點、避坑經驗），以 `§` 分段。
- **斷線與崩潰防丟 (Crash-Resilient State)**：每次輪次執行時會話 ID、模型與 Token 統計均原子寫入磁碟，即使 Telegram 掉線或進程重啟，上下文 **100% 無縫承接不丟失**。
- **持續監聽與運行內容檢測 (Continual Memory Extractor)**：自動監聽 Telegram 對話關鍵偏好與 Agent 執行產出的系統端點/配置，即時落盤沉澱。
- **靜態快照注入 (Frozen Snapshot Pattern)**：對話啟動時自動注入記憶快照（USER.md + MEMORY.md + 最近一次 SESSIONS.md 摘要），不破壞 LLM Prefix Caching，節省 Token 並極速回應。
- **寫入驗證閘 (v1.1)**：所有條目帶 `[日期 · auto/manual]` 元數據；雙向包含去重（舊條目被新條目涵蓋時自動更新）；同標籤衝突時自動寫入一律跳過（模型猜測永不覆蓋人工策展事實），人手 `/memory add` 以用戶為準。
- **多代輪轉備份**：每次寫入前自動輪轉保留 5 代 `.bak` 備份（`.bak` → `.bak.4`），誤寫可即時 rollback；30KB 上限爆滿時按價值驅逐（auto 先於 manual、舊先於新），唔再單純丟最舊。

### 6. 🌐 網路容錯與 Proxy 支援
- 支援 HTTP / HTTPS / SOCKS5 代理。
- 具備連線池重設與指數退避重試機制，在電腦睡眠喚醒、WiFi 切換時自動平滑重連。

### 7. 🔒 嚴格 Telegram ID 白名單綁定 (Strict User Binding)
- **安全隔離**：只有明確在 `ALLOWED_USER_IDS` 或 `TELEGRAM_ALLOWED_USERS` 綁定的 Telegram ID 才能使用 Bot。
- **陌生人自動攔截**：未授權使用者傳送訊息會立刻被安全攔截並提示其 Telegram ID，防止未授權使用與 API 額度消耗。
- **動態綁定支援**：可透過 `agy-gateway bind <id>` 與 `agy-gateway unbind <id>` 即時管理授權名單。

### 8. 💻 雙後端架構：Antigravity + 本地 OpenCode (`opencode_runner.py`)
- **本地 OpenCode 模型接入**：透過 `opencode run --format json` 非互動模式執行本地模型，NDJSON 串流即時解析工具調用與 Token 用量，無需依賴全域 `opencode.json` 預設（避免指向失效代理）。
- **會話無縫承接**：每個使用者嘅 OpenCode Session ID 獨立持久化（`opencode run -s <id>`），跨重啟對話上下文不丟失。
- **自動備援 (Auto-Fallback)**：當 Antigravity 後端失敗時，自動切換本地 OpenCode 接續執行，狀態卡會標示實際服務後端（⚡ Antigravity / 💻 本地 OpenCode / 🔁 OpenCode 備援）。
- **一鍵切換**：`/model` 選單內建 (OC) 本地模型分區，點擊即切換後端。
- **跨後端記憶連續 (Context Handoff)**：切換模型/後端**唔會再清空會話**——兩邊 native session 各自保留，切返轉頭可無縫恢復；Bot 內部維護滾動對話紀錄（rolling transcript），切換後自動把對方後端嘅近期對話注入新後端，因用量耗盡而切換模型都唔會再丟失記憶。

### 9. 📝 執行中即時修正整合 (Mid-Run Correction Steering)
- **修正即時生效**：任務運行途中直接傳送文字訊息，Bot 會**立即中止目前執行**，並把「原始任務 + 所有修正」合併重新執行——唔使等第一次跑完。
- **支援多則修正**：可連續傳送多則修正，全部按時間順序累積；最終只輸出**一個整合所有輸入嘅答案**，若修正與原始任務衝突以最新修正為準。
- **靜默重跑**：人為中止（修正或 `/cancel`）觸發嘅 SIGTERM 退出會被正確識別為取消，唔會再誤顯示 `Exit Code: -15` 失敗訊息。
- **指令級控制**：`/steer <內容>` 可清空已累積修正並以新指示重跑；`/cancel`、`/reset` 會一併清除修正狀態。

### 10. ⏱️ 1 分鐘守護與掉線自動恢復 (Watchdog & Auto-Reconnect)
- **全自動狀態探測**：每 60 秒透過 `watchdog.py` 檢查進程存活與 Telegram API 連通性。
- **自動拉起與重連**：若發現網路重置或進程異常，守護程序會自動執行優雅重連與重啟，杜絕靜默斷線。
- **專屬維運指令 `agy-gateway`**：整合 `status`、`start`、`stop`、`restart`、`check`、`logs`，並內建 macOS LaunchAgent 與 Linux Systemd 開機持久自啟服務模板。

---

## 📁 模組化專案架構

```
tg-antigravity-bot/
├── bot.py             # 核心應用程式入口、Telegram Update 路由與批次調度
├── config.py          # 環境變數載入、設定解析與權限校驗
├── memory_manager.py  # 本機持久記憶雙軌庫 (USER.md/MEMORY.md)、持續監聽與快照注入
├── session_store.py   # 會話與進程抗掉線持久化狀態儲存庫 (JSON 磁碟原子寫入)
├── formatter.py       # Telegram MarkdownV2 轉換、GFM 表格重構與代碼感知分段
├── agent_runner.py    # agy CLI 子進程管理、stderr 即時解析與任務中止機制
├── opencode_runner.py # 本地 OpenCode 後端：NDJSON 串流、Session 持久化與備援執行
├── media_handler.py   # 照片、語音、檔案下載快取與本機生成媒體偵測
├── ui_components.py   # 模型切換 Inline Keyboard、狀態與說明卡片排版
├── policy_store.py    # 行為準則讀取與注入（手寫規則，無自動管線）
├── watchdog.py        # 60 秒守護：存活、alive-but-broken 偵測與可驗證重啟
├── test_*.py          # 驗收矩陣（注入、指令選單、串流上限與重啟契約）
├── requirements.txt   # Python 依賴清單
├── .env.example       # 設定檔範本
└── README.md          # 說明文件
```

---

## 🚀 快速開始

### 1. 取得 Telegram Bot Token
1. 在 Telegram 搜尋 [@BotFather](https://t.me/BotFather)
2. 發送 `/newbot`，依照提示設定名稱並取得 **Bot Token**

### 2. 下載專案與安裝依賴

```bash
cd ~/tg-antigravity-bot

# 建立並啟用虛擬環境（可選）
python3 -m venv venv
source venv/bin/activate

# 安裝依賴
pip install -r requirements.txt
```

### 3. 設定環境變數

```bash
cp .env.example .env
```

編輯 `.env` 檔案：

```env
TELEGRAM_BOT_TOKEN=123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
ALLOWED_USER_IDS=                           # 先留空，步驟 4 取得
PROXY_URL=                                  # 若在大陸地區可設定 http://127.0.0.1:7890

# 本地 OpenCode 後端（可選，啟用本機模型與自動備援）
ENABLE_OPENCODE_FALLBACK=true               # Antigravity 失敗時自動用本地 OpenCode 重試
OPENCODE_PATH=                              # opencode 可執行檔路徑（留空自動尋找）
OPENCODE_MODEL=sensenova/deepseek-v4-flash  # 本地預設／備援模型
OPENCODE_TIMEOUT=600                        # 本地執行超時（秒）
```

### 4. 啟動並取得你的 User ID

```bash
python bot.py
```

1. 在 Telegram 搜尋你的 Bot 並發送 `/start`
2. Bot 會回覆你的 **User ID**
3. 將此 ID 填入 `.env` 的 `ALLOWED_USER_IDS` 中（例如 `ALLOWED_USER_IDS=1120349178`）
4. 重啟 Bot 即可完成身份安全綁定！

---

## 📖 指令一覽表

| 指令 | 說明 |
|---|---|
| `/start` | 歡迎頁面、查看目前 User ID 與快速導覽 |
| `/usage` | 📊 查看當前會話、最近單輪與全域累計的 Token 用量統計 |
| `/model` | 開啟互動式選單切換 AI 模型（支援點擊切換與分頁） |
| `/memory` 或 `/mem` | 🧠 查看、搜尋與管理本機持久記憶 (`USER.md` / `MEMORY.md`) |
| `/compact` | 📦 壓縮當前會話上下文（提煉決策並瘦身） |
| `/reset` 或 `/new` | 重置當前對話記憶，開啟全新 Session |
| `/status` | 查看目前 Agent 運作狀態、Token 用量、會話 ID、工作目錄與上線時間 |
| `/cancel` 或 `/stop` | 中止目前正在執行的長時間任務 |
| `/steer <內容>` | 中止目前任務，清空已累積修正並以新指示立即重跑 |
| `/clear` | 清理本機快取的多模態暫存檔案 |
| `/help` | 顯示完整功能說明卡片 |

---

## 🧬 行為準則注入 (Policy Injection)

每個新對話開始時，`MEMORY_DIR/policies/` 下所有 `status: active` 的規則會注入 prompt，置於 `🚨 HARDLINE BEHAVIOR POLICIES` 標題之下。規則是**手寫的 JSON 檔**。

```json
{
  "id": "rule_evidence-discipline",
  "summary": "一句指令式規則",
  "root_cause": "為何需要這條規則",
  "evidence": "來源／依據",
  "trigger": "（選填）何時適用",
  "constraint": "（選填）禁止或必須做什麼",
  "verification": "（選填）如何自證遵守",
  "version": 1, "created_at": 0, "last_updated": 0,
  "status": "active"
}
```

- `status: active` 會注入，其餘不會。刪除規則就是移除檔案。
- `policy_store.set_policy_status(rule_id, "active" | "disabled")` 是唯一的開關。沒有自動老化。
- 注入只寫 `last_injected_at`，純屬觀察：**注入不等於使用**。系統只知道規則進了 prompt，不知道它是否與任務相關，更不知道是否影響了輸出。
- 規則載入失敗會記錄 `POLICY NOT LOADED`，全部注入失敗會記錄 `HARDLINE POLICIES NOT INJECTED`。一條無聲消失的約束正是這個模組要防的事。

### 曾經存在的自動管線

一套 LLM evaluator → 證據門檻 → 提案佇列 → 人工審批 → 規則編譯的管線曾經存在於 `evolution/`。它在整個生命週期中產出了 **0 條規則**：evaluator 需要輔助 API key，未曾設定，因此從未產生過任何 candidate。與此同時它的佇列每輪對話寫入一筆無人消費的 job，其輪詢是一次 file descriptor 洩漏的來源，並曾導致 Bot 停止回應數小時。

該套件已整個移除。目前生效的規則全部由人撰寫。

```bash
venv/bin/python3 -m test_policy_store      # 注入、結構化欄位、手動開關、失敗可見性
venv/bin/python3 -m test_command_menu      # 指令選單與 handler 一致性
venv/bin/python3 -m test_stream_limit      # NDJSON 串流上限、watchdog 重啟契約
```

---

## 🕳️ 避坑記錄 (Pitfalls)

全部為 2026-09-12 實測所得，非推論。每則附上真實症狀與可搜尋的錯誤字串 —— 這些坑的共同點是**失敗時看起來像正常運作**。

### 1. Bot 停止回應，但 log 全綠

**症狀**：`getUpdates` 持續 `200 OK`，進程存活，watchdog 每分鐘檢查都通過 —— 但所有回覆都發不出去。持續四小時無人察覺。

**真因**：file descriptor 洩漏。`launchctl limit maxfiles` 在 macOS 上 soft limit 只有 **256**，所有 launchd 啟動的服務都繼承它（shell 的 `ulimit -n` 是 1048576，所以手動跑完全正常）。撞頂後 `socket()` 失敗，但已建立的 keep-alive 連線不需要新 fd，因此 polling 照常成功。

**症狀不會說「too many open files」**，而是：
- `sqlite3.connect()` → `unable to open database file`（檔案明明存在、目錄可寫）
- `socket()` / `getaddrinfo()` → `[Errno 8] nodename nor servname provided, or not known`（看起來像 DNS 壞了，其實不是）

**診斷**：`lsof -p <pid> | awk '$5=="REG"{print $9}' | sort | uniq -c | sort -rn` —— 同一個檔案開了幾十次就是洩漏。

**加固**：plist 加 `SoftResourceLimits` → `NumberOfFiles` = 8192。

### 2. `with sqlite3.connect(path) as conn:` 不會關閉連線

Python 的 sqlite3 context manager **只管 transaction 的 commit/rollback**，不 close 連線。每 3 秒輪詢一次即每 3 秒洩漏 2 個 fd。必須自己包一層 `finally: conn.close()`。這是上一則的直接成因。

### 3. `/compact` 回傳原始 NDJSON 當「摘要」

**真因**：`asyncio.StreamReader.readline()` 預設單行上限 **64 KiB**。agy 每行一個 JSON 物件，長對話（實測 126 萬 tokens）單行輕易超過。超過時 `readline()` 拋 `ValueError: Separator is found, but chunk is longer than limit`，若被 `except: break` 吞掉，整個 stream 就此中斷 —— 結尾的 `result` 事件永遠收不到，fallback 再用 `json.loads()` 解整份 NDJSON（多於一行必然失敗），最後把原始事件流當成回應輸出。

**修法**：`create_subprocess_exec(..., limit=32*1024*1024)`，並且**不要吞掉 readline 例外**。

同一根因會讓 usage 統計變成 `input_tokens: 0`，因為 usage 在 `result` 事件裡。

### 4. 一句 `hi` 花掉 19,333 tokens

逐層對照實測（模型 Gemini 3.8 Flash (High)）：

| 條件 | input tokens |
|---|---|
| 空目錄，無 `--add-dir` | 13,048 |
| 409 個 dummy 目錄，無 `--add-dir` | 13,050 |
| `cd ~` 但無 `--add-dir` | 13,065 |
| `--add-dir ~` | 19,333 |

**`--add-dir` 是唯一開關**：加上它才會載入該目錄的 `AGENTS.md`（+1,477）與 `.agents/skills/` 全部 SKILL.md 描述（+4,808，70 個 × 約 69 tokens），且每個 tool round 重送一次。**cwd 不會觸發**，而不傳 `--add-dir` 並不會失去檔案存取 —— agent 仍以 cwd 讀寫。

agy **不會**注入目錄清單：409 個目錄與空目錄同價，所以貴不是因為 workspace 大。`--disable-slash-commands` 無效（19,339），它只禁 slash 展開。

### 5. `cache_read_tokens: 0` 不代表沒有 prompt caching

單輪測試永遠看到 0，很容易誤判。多輪任務第 3 輪起就命中（實測 16,286 / 16,283 / 16,278 / 20,341）。**用單輪數據下結論會得到相反的答案。**

### 6. Watchdog 報告重啟成功，但 bot 沒有回來

`launchctl kickstart` exit 0 只代表指令被接受，不代表進程起來了。若重啟函式不回報結果、呼叫端又無條件開始冷卻，一次失敗的重啟會被記錄成成功，並靜音 15 分鐘。**成功的定義必須是「進程真的回來了」**，輪詢確認。

### 7. `pkill -f "python.*bot\.py"` 會殺掉別的 bot

本機另有 `~/projects/drawbot/bot.py`。停止腳本必須以 **cwd 範圍**過濾：

```bash
for PID in $(pgrep -f "[Pp]ython.*bot\.py"); do
    CWD="$(lsof -a -p "$PID" -d cwd -Fn | grep '^n' | cut -c2-)"
    [ "$CWD" = "$DIR" ] && kill "$PID"
done
```

watchdog 的存活偵測也一樣：範圍太寬會讓一個**已死的 bot 看起來還活著**，永遠不重啟。

### 8. 注入不等於使用

`injected != matched != affected_output`。把「規則進了 prompt」寫進 `last_used_at`，會讓閒置計時器永遠歸零，自動老化因而永不觸發 —— 而依賴該機制的一切（例如豁免老化的 pin）就變成純粹裝飾。**建模前先確認你量度的事件與你要的語義是同一件事。**

### 9. 什麼時候該吞例外

本專案曾有 44 處靜默 `except: pass / break`。逐一審視後只有 5 處是錯的。分界線：

> **這個例外被吞掉之後，有沒有東西已經消失了而沒人知道？**

- 是 → 必須出聲（規則沒載入、狀態綁定遺失、重啟其實失敗了）
- 否 → 保持安靜（Telegram `edit_text` 失敗、殺一個已死的進程、跳過一行非 JSON）

把 best-effort 的 UI 更新也改成報錯只會製造噪音，**噪音本身是另一種沉默** —— 真正的 ERROR 會被淹沒。

### 10. 驗證中間步驟不等於驗證結果

本次除錯中犯過兩次：確認「protocol.md 載入了 357 tokens」就當規則生效（實際上使用者有既存對話，注入條件 `if not conv_id` 根本不成立，規則從未到達模型）；確認「指令 exit 0」就當重啟成功。

**edit 成功不是行為正確的證據。** 驗證必須落在最終可觀察的行為上 —— 例如直接讀 agy 的 `brain/<conv_id>/.system_generated/logs/transcript_full.jsonl` 確認規則真的進了 prompt。

---

## 🔄 背景長效運行 (Daemon)

### 方式 A：使用 tmux (推薦)

```bash
tmux new -s agy-bot
cd ~/tg-antigravity-bot
source venv/bin/activate
python bot.py

# 按下 Ctrl + B，然後按 D 鍵即可後台掛起
# 重新進入: tmux attach -t agy-bot
```

### 方式 B：使用 nohup

```bash
nohup python bot.py > bot.log 2>&1 &
```

### 方式 C：使用 macOS Launchd 服務（開機自啟）

建立 `~/Library/LaunchAgents/com.antigravity.tgbot.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.antigravity.tgbot</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/my/tg-antigravity-bot/venv/bin/python</string>
        <string>/Users/my/tg-antigravity-bot/bot.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/my/tg-antigravity-bot</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/my/tg-antigravity-bot/bot_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/my/tg-antigravity-bot/bot_stderr.log</string>
</dict>
</plist>
```

啟用服務：
```bash
launchctl load ~/Library/LaunchAgents/com.antigravity.tgbot.plist
```

---

## 🤝 參考與致謝
- [NousResearch / hermes-agent](https://github.com/nousresearch/hermes-agent) — 參考其優異的 Telegram Gateway 平台設計、MarkdownV2 轉換與分段容錯邏輯。
- [Google Antigravity](https://antigravity.google) — 提供強大的全功能 AI 代理與程式設計工具鏈。
