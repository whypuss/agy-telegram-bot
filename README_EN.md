# Antigravity Telegram Bot 🤖

[繁體中文](README.md) | **English**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![Telegram](https://img.shields.io/badge/Telegram-Bot%20API-blue.svg)](https://core.telegram.org/bots/api)
[![Google Antigravity](https://img.shields.io/badge/Antigravity-Agent%202.0-orange.svg)](https://antigravity.google)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Remotely command and chat with your **Google Antigravity Agent & Gemini** right from Telegram on your phone, desktop, or any device.  
Re-architected with inspiration from [NousResearch Hermes Agent](https://github.com/nousresearch/hermes-agent) Telegram gateway platform, providing industrial-grade network resilience, MarkdownV2 formatting engine, multimodal ingestion (photos, voice, files), smart inbound batching, and interactive UI components.

---

## ✨ Key Features & Architecture Highlights

### 1. 🛡️ Industrial-Grade MarkdownV2 & Fallback Engine (`formatter.py`)
- **GFM Table Conversion**: Automatically transforms GitHub Markdown pipe tables (`| col | col |`) into clean, mobile-friendly bulleted list cards.
- **Code Block Boundary Integrity**: When splitting messages over the 4096-character limit, automatically closes open fences ` ``` ` at the boundary and re-opens them with the correct syntax highlighting tag on the next chunk.
- **Graceful Fallback**: If Telegram API returns a Markdown parse error due to special characters, automatically strips formatting markers and resends as clean Plain Text, guaranteeing **100% message delivery without drops**.

### 2. ⚡ Inbound Debouncing & Message Batching
- **Long Text Aggregation**: Seamlessly aggregates split message chunks sent in rapid succession by Telegram clients when pasting massive prompts.
- **Photo Album & Burst Batching**: Automatically groups Telegram photo albums (`media_group_id`) and rapid photo bursts into a single turn for multimodal analysis.

### 3. 🎙️ Comprehensive Multimodal Support (`media_handler.py`)
- 📸 **Photos & Albums**: Downloads high-res photos to local cache for Gemini Vision processing.
- 🎙️ **Voice Notes**: Supports Telegram `.ogg` voice notes for audio comprehension and voice interaction.
- 📁 **Files & Documents**: Supports Python scripts, PDFs, JSON, CSVs, logs, etc., enabling direct codebase inspection and debugging.
- 📍 **Location Pins**: Recognizes and parses shared GPS coordinates and venues.
- 📤 **Outbound Media Delivery**: Detects generated plots, images, or exported files from agent responses and sends them natively as Telegram photos or documents.

### 4. 🎛️ Interactive UI & Real-Time Progress
- **Paginated `/model` Selector**: Clickable inline keyboard buttons to switch instantly between Gemini 3.8 Flash, Gemini 3.7 Flash (High), Claude Opus 4.6 (Thinking), and more.
- **Live Progress Updates**: Continuous typing heartbeat with dynamic status editing reflecting tools (🔧 Tool execution, 🔍 Search, 📄 File read/write, 🧠 Thinking), accompanied by an instant **🛑 Cancel** button.

### 5. 🧠 Persistent Local Memory System (Hermes Agent Architecture)
- **Dual-Store Architecture**: Compatible with Hermes Agent, managing `USER.md` (communication habits, preferences, rules) and `MEMORY.md` (server IPs, credentials, ports, architectural decisions) delimited by `§`.
- **Crash-Resilient State Persistence**: Active session IDs, model selections, and token counters are atomically persisted to disk. Reboots or network drops will **never wipe conversation context**.
- **Continual Listening & Runtime Extractor**: Actively listens to Telegram chats and monitors execution outcomes to extract and store durable facts and guidelines.
- **Frozen Snapshot Pattern**: Inject memories at session start without disturbing LLM prefix caching, conserving tokens while preserving recall.

### 6. 🌐 Network Resilience & Proxy Support
- Supports HTTP, HTTPS, and SOCKS5 proxies (`PROXY_URL`).
- Automatic connection pool drainage and exponential backoff retry during Mac sleep/wake cycles, WiFi switches, or network interruptions.

---

## 📁 Repository Structure

```
tg-antigravity-bot/
├── bot.py             # Main entry point, Telegram update router & turn pipeline
├── config.py          # Environment settings, authentication checks & defaults
├── memory_manager.py  # Local dual-store persistent memory (USER.md / MEMORY.md) & listener
├── session_store.py   # Crash-resilient state persistence engine (atomic JSON serialization)
├── formatter.py       # MarkdownV2 converter, GFM table wrapper & code-aware chunking
├── agent_runner.py    # agy CLI subprocess management, stderr streaming & task cancellation
├── media_handler.py   # Inbound media download cache & outbound media detection
├── ui_components.py   # Model selector inline keyboards, status & help formatters
├── requirements.txt   # Python package dependencies
├── .env.example       # Configuration template
├── README.md          # Traditional Chinese documentation
├── README_EN.md       # English documentation
└── LICENSE            # MIT License
```

---

## 🚀 Quick Start

### 1. Create a Telegram Bot
1. Search [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`, follow the instructions, and obtain your **Bot Token**

### 2. Clone & Install Dependencies

```bash
git clone https://github.com/whypuss/agy-telegram-bot.git
cd agy-telegram-bot

# Set up virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=123456789:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
ALLOWED_USER_IDS=                           # Leave blank initially, get in Step 4
PROXY_URL=                                  # Optional: http://127.0.0.1:7890 or socks5://127.0.0.1:1080
```

### 4. Start & Authorize Your User ID

```bash
python bot.py
```

1. Open Telegram, search for your Bot, and send `/start`
2. The bot will respond with your **User ID**
3. Fill your User ID into `.env` (e.g. `ALLOWED_USER_IDS=1120349178`)
4. Restart the bot to lock access exclusively to authorized users!

---

## 📖 Commands

| Command | Description |
|---|---|
| `/start` | Welcome screen, view User ID and quick start guide |
| `/usage` | 📊 View session, turn, and total token usage statistics |
| `/model` | Open interactive button menu to switch AI models with pagination |
| `/memory` or `/mem` | 🧠 View, search, and manage local persistent memories (`USER.md` / `MEMORY.md`) |
| `/compact` | 📦 Compact current conversation history while preserving key decisions |
| `/reset` or `/new` | Reset conversation memory and start a fresh session |
| `/status` | View agent health, token usage, active model, session ID, workspace, and uptime |
| `/cancel` or `/stop` | Abort a long-running agent turn immediately |
| `/clear` | Purge local temporary media cache files |
| `/help` | Display comprehensive command and feature guide |

---

## 🔄 Background Daemon Deployment

### Option A: tmux (Recommended)

```bash
tmux new -s agy-bot
cd ~/tg-antigravity-bot
source venv/bin/activate
python bot.py

# Detach: Press Ctrl + B, then D
# Re-attach: tmux attach -t agy-bot
```

### Option B: nohup

```bash
nohup python bot.py > bot.log 2>&1 &
```

### Option C: macOS Launchd Service (Auto-start on boot)

Create `~/Library/LaunchAgents/com.antigravity.tgbot.plist`:

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

Load service:
```bash
launchctl load ~/Library/LaunchAgents/com.antigravity.tgbot.plist
```

---

## 📄 License

[MIT](LICENSE) © 2026 whypuss
