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

### 5. 🌐 網路容錯與 Proxy 支援
- 支援 HTTP / HTTPS / SOCKS5 代理。
- 具備連線池重設與指數退避重試機制，在電腦睡眠喚醒、WiFi 切換時自動平滑重連。

---

## 📁 模組化專案架構

```
tg-antigravity-bot/
├── bot.py             # 核心應用程式入口、Telegram Update 路由與批次調度
├── config.py          # 環境變數載入、設定解析與權限校驗
├── formatter.py       # Telegram MarkdownV2 轉換、GFM 表格重構與代碼感知分段
├── agent_runner.py    # agy CLI 子進程管理、stderr 即時解析與任務中止機制
├── media_handler.py   # 照片、語音、檔案下載快取與本機生成媒體偵測
├── ui_components.py   # 模型切換 Inline Keyboard、狀態與說明卡片排版
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
| `/reset` 或 `/new` | 重置當前對話記憶，開啟全新 Session |
| `/status` | 查看目前 Agent 運作狀態、Token 用量、會話 ID、工作目錄與上線時間 |
| `/cancel` 或 `/stop` | 中止目前正在執行的長時間任務 |
| `/clear` | 清理本機快取的多模態暫存檔案 |
| `/help` | 顯示完整功能說明卡片 |

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
