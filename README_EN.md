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
- **Frozen Snapshot Pattern**: Inject memories (USER.md + MEMORY.md + latest SESSIONS.md summary) at session start without disturbing LLM prefix caching, conserving tokens while preserving recall.
- **Write Validation Gate (v1.1)**: Every entry carries `[date · auto|manual]` metadata; bidirectional containment dedup (an entry fully covered by a newer one is updated in place); on label conflicts, automatic extractor writes are skipped (model guesses never overwrite curated facts) while manual `/memory add` wins as user authority.
- **Rotating Multi-Generation Backups**: 5 rotating `.bak` generations (`.bak` → `.bak.4`) are taken right before every write for instant rollback; when the 30KB cap is exceeded, eviction is value-based (auto before manual, older before newer) instead of blindly dropping the oldest.

### 6. 🌐 Network Resilience & Proxy Support
- Supports HTTP, HTTPS, and SOCKS5 proxies (`PROXY_URL`).
- Automatic connection pool drainage and exponential backoff retry during Mac sleep/wake cycles, WiFi switches, or network interruptions.

### 7. 🔒 Strict Telegram ID Whitelist & Binding
- **Security Isolation**: Only users explicitly bound in `ALLOWED_USER_IDS` or `TELEGRAM_ALLOWED_USERS` can interact with the bot.
- **Unauthorized Interception**: Unregistered senders are immediately blocked from consuming LLM calls or executing commands, receiving an unauthorized alert showing their user ID.
- **Dynamic Binding CLI**: Add or remove authorized Telegram IDs anytime via `agy-gateway bind <id>` and `agy-gateway unbind <id>`.

### 8. 💻 Dual-Backend Architecture: Antigravity + Local OpenCode (`opencode_runner.py`)
- **Local OpenCode Models**: Runs local models via `opencode run --format json` in non-interactive mode, streaming NDJSON events for real-time tool-call progress and token accounting — never relying on the global `opencode.json` default (which may point at a dead proxy).
- **Seamless Session Continuity**: Per-user OpenCode session IDs are persisted (`opencode run -s <id>`), so conversation context survives restarts.
- **Auto-Fallback**: When the Antigravity backend fails, execution automatically falls back to local OpenCode; the status card labels the actual serving backend (⚡ Antigravity / 💻 Local OpenCode / 🔁 OpenCode Fallback).
- **One-Click Switching**: The `/model` picker includes a dedicated (OC) local-model section — tap to switch backends instantly.
- **Cross-Backend Memory Continuity (Context Handoff)**: Switching models/backends **no longer wipes the conversation** — both native sessions are preserved and resume when you switch back; a rolling transcript automatically injects the other backend's recent turns into the new backend, so switching models due to quota exhaustion never loses memory.

### 9. 📝 Mid-Run Correction Steering
- **Instant Corrections**: Send a text message while a task is running and the bot **immediately cancels the current execution**, then re-runs with the original task plus all corrections merged — no waiting for the first run to finish.
- **Multiple Corrections**: Corrections accumulate in chronological order; the final output is **a single answer integrating every input**, with newer corrections taking precedence on conflicts.
- **Silent Re-runs**: Deliberate terminations (correction steering or `/cancel`) are recognized as cancellations instead of surfacing a spurious `Exit Code: -15` failure message.
- **Command-Level Control**: `/steer <text>` discards accumulated corrections and restarts with a fresh instruction; `/cancel` and `/reset` also clear correction state.

### 10. ⏱️ 1-Minute Watchdog & Auto-Reconnect Recovery
- **Continuous Health Probing**: Periodically checks process liveness and tests Telegram API connectivity every 60 seconds (`watchdog.py`).
- **Automatic Reconnection**: Automatically detects broken connections, hung loops, or DNS/network transitions, triggering a graceful service restart to restore communication.
- **Unified Management CLI `agy-gateway`**: Complete operational command suite (`status`, `start`, `stop`, `restart`, `check`, `logs`), packaged with ready-to-use macOS LaunchAgent and Linux Systemd templates for persistent 24/7 background operation.

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
├── opencode_runner.py # Local OpenCode backend: NDJSON streaming, session persistence & fallback
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

# Local OpenCode backend (optional: enables local models & auto-fallback)
ENABLE_OPENCODE_FALLBACK=true               # Auto-retry with local OpenCode when Antigravity fails
OPENCODE_PATH=                              # Path to opencode executable (auto-detect if blank)
OPENCODE_MODEL=sensenova/deepseek-v4-flash  # Local default / fallback model
OPENCODE_TIMEOUT=600                        # Local execution timeout (seconds)
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
| `/steer <text>` | Abort the current task, discard accumulated corrections, and restart with a new instruction |
| `/clear` | Purge local temporary media cache files |
| `/help` | Display comprehensive command and feature guide |

---

## 🧬 Policy Injection

At the start of every new conversation, every rule in `MEMORY_DIR/policies/`
with `status: active` is injected into the prompt under a
`🚨 HARDLINE BEHAVIOR POLICIES` heading. Rules are **hand-written JSON files**.

- `status: active` is injected, anything else is not. Deleting a rule means
  removing its file.
- `policy_store.set_policy_status(rule_id, "active" | "disabled")` is the only
  switch. Nothing ages automatically.
- Injection writes only `last_injected_at`, purely observational:
  **injection is not usage.** The system knows a rule entered the prompt, not
  that it applied to the task, and not that it changed the output.
- A rule that fails to load logs `POLICY NOT LOADED`; losing all of them logs
  `HARDLINE POLICIES NOT INJECTED`. A constraint that vanishes without a word is
  exactly what this module exists to prevent.

### The automatic pipeline that used to be here

An LLM evaluator → evidence gate → proposal queue → human approval → rule
compiler pipeline lived in `evolution/`. It produced **zero rules** across its
entire lifetime: the evaluator needed an auxiliary API key that was never set,
so no candidate was ever generated. Meanwhile its queue accumulated one
unconsumed job per turn, and its polling was the source of a file descriptor
leak that took the bot off the air for hours.

The package has been removed. Every rule now in force was written by a person.

```bash
venv/bin/python3 -m test_policy_store      # injection, structured fields, manual control, loud failure
venv/bin/python3 -m test_command_menu      # command menu / handler consistency
venv/bin/python3 -m test_stream_limit      # NDJSON stream limit, watchdog restart contract
```

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
