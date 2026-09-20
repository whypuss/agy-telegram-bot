"""
Antigravity Native Workflow, Reasoning & Utility Commands (workflow_commands.py)
================================================================================
Implements advanced Antigravity workflow commands for the Telegram Bot:
1. Customization & Knowledge:
   - /skills: Browse, inspect, and list available agent skills.
2. Planning & Requirements:
   - /plan: Structured planning mode before modifying code.
   - /grill-me (or /grill): Interactive interview to clarify requirements and edge cases.
3. Reasoning & Autonomy:
   - /goal: Autonomous goal execution loop until verified complete.
   - /boost: High-reasoning deep analysis for race conditions and tricky bugs.
   - /teamwork: Multi-agent collaboration dispatching specialized subagents.
4. Utilities & Side Tasks:
   - /btw: Background independent side-question without polluting primary session.
   - /browser: Sandboxed web retrieval and UI rendering inspection.
   - /diff: Zero-token instant workspace git diff and change inspection.
   - /schedule: One-shot countdown timer or scheduled prompt runner.
"""

import asyncio
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import WORKSPACE_DIR, is_opencode_model
from session_store import get_user_model

logger = logging.getLogger("agy-tg-bot.workflow")

# ---------------------------------------------------------------------------
# 1. /skills: Skill Discovery & Inspection
# ---------------------------------------------------------------------------
def _discover_skills() -> List[Dict[str, str]]:
    """Scan standard Antigravity skill directories and extract skill metadata."""
    search_roots = [
        Path.home() / ".gemini" / "antigravity-cli" / "builtin" / "skills",
        Path.home() / ".gemini" / "antigravity-cli" / "skills",
        Path.home() / ".hermes" / "skills",
    ]
    if WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR):
        search_roots.append(Path(WORKSPACE_DIR) / ".agents" / "skills")
        search_roots.append(Path(WORKSPACE_DIR) / "skills")

    skills: Dict[str, Dict[str, str]] = {}
    for root in search_roots:
        if not root.exists():
            continue
        for skill_file in root.glob("**/SKILL.md"):
            try:
                content = skill_file.read_text(encoding="utf-8", errors="replace")
                # Parse simple YAML frontmatter
                m_name = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
                m_desc = re.search(r"^description:\s*(?:>-\s*|\s*)(.+?)(?=\n[a-z_]+:|\n---|\n#|$)", content, re.MULTILINE | re.DOTALL)

                s_name = m_name.group(1).strip() if m_name else skill_file.parent.name
                s_desc = m_desc.group(1).strip().replace("\n", " ") if m_desc else "無說明"
                if len(s_desc) > 120:
                    s_desc = s_desc[:117] + "..."

                if s_name not in skills:
                    skills[s_name] = {
                        "name": s_name,
                        "description": s_desc,
                        "path": str(skill_file),
                    }
            except Exception as e:
                logger.debug("Failed parsing skill file %s: %s", skill_file, e)

    return sorted(skills.values(), key=lambda x: x["name"])


async def cmd_skills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /skills command to list or inspect skills."""
    if not update.message:
        return

    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=1)
    target_skill = parts[1].strip().lower() if len(parts) > 1 else ""

    all_skills = _discover_skills()

    if target_skill:
        # Inspect specific skill
        match = next((s for s in all_skills if s["name"].lower() == target_skill), None)
        if not match:
            await update.message.reply_text(
                f"❌ 找不到名稱為 `{target_skill}` 的技能。\n輸入 `/skills` 查看所有可用清單。",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        lines = [
            f"🧩 **Antigravity 技能詳情：`{match['name']}`**\n",
            f"📝 **描述**：{match['description']}",
            f"📂 **路徑**：`{match['path']}`\n",
            "💡 *使用提示：在日常任務對話中提及此技能名稱，Agent 即會自動加載對應的專業流程與工具。*",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    # List all skills
    if not all_skills:
        await update.message.reply_text(
            "🧩 **未發現可用技能 (No Skills Found)**\n可在專案目錄建立 `.agents/skills/<name>/SKILL.md` 定義技能。",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    lines = [
        f"🧩 **Antigravity 技能庫清單 (共 {len(all_skills)} 項)**\n",
    ]
    for s in all_skills:
        lines.append(f"• `{s['name']}` — {s['description']}")

    lines.append("\n💡 *查看特定技能細節：`/skills <名稱>`，例如 `/skills agy-customizations`*")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# 2. /plan: Structured Planning Mode
# ---------------------------------------------------------------------------
PLAN_SYSTEM_PREFIX = """\
【系統指令：Google Antigravity 原生 /plan 架構規劃模式】
你正在執行架構實作規劃模式。
請針對使用者的需求進行全面的代碼庫分析與架構推導。
【重要約束】：在動手修改任何檔案之前，必須先產出一份結構完整的「實作方案工件（Implementation Plan）」，嚴禁直接寫入修改生產代碼！

規劃工件必須包含以下要素：
1. **現況分析**：既有模組依賴與架構影響範疇
2. **技術取捨**：備選方案對比與推導理由
3. **逐步實施路線圖 (Roadmap)**：明確的步驟清單與具體改動檔案
4. **驗證標準**：最小關聯測試與回歸檢查清單

【使用者規劃需求】：
"""

async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /plan command to enter structured planning mode."""
    if not update.message:
        return
    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "📋 **Antigravity 架構規劃模式 (Planning Mode)**\n\n"
            "用法：`/plan <你的架構或重構需求>`\n"
            "範例：`/plan 重構使用者驗證機制為非同步 JWT Token 驗證`\n\n"
            "💡 *Agent 將深入審視代碼庫並生成完整的實作方案工件供你確認，此階段不會改動現有代碼。*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    from bot import process_agent_turn, _begin_fresh_task
    uid = update.effective_user.id
    user_req = parts[1].strip()
    prompt = f"{PLAN_SYSTEM_PREFIX}\n{user_req}"

    _begin_fresh_task(uid, prompt)
    await process_agent_turn(
        update=update,
        context=context,
        prompt=prompt,
        original_message_id=update.message.message_id,
    )


# ---------------------------------------------------------------------------
# 3. /grill-me: Interactive Requirement Interview
# ---------------------------------------------------------------------------
GRILL_SYSTEM_PREFIX = """\
【系統指令：Google Antigravity 原生 /grill-me 需求深度盤點與面談模式】
你現在是嚴格的技術架構面試官。不要直接給出實現或代碼！
請針對使用者提出的需求進行深度的邊界排查，主動提出 3~5 個關鍵且深刻的澄清問題：
1. 潛在的極端情境與邊界條件 (Edge Cases)
2. 併發、容量預估與效能約束 (Scale & Performance)
3. 容災降級與既有系統相容性 (Compatibility & Resilience)
請提供清晰的選項或評估建議，協助使用者精確敲定規格。

【使用者需求】：
"""

async def cmd_grill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /grill-me (or /grill) command for interactive requirements interview."""
    if not update.message:
        return
    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "🥩 **需求深度盤點與面談 (Grill-me Mode)**\n\n"
            "用法：`/grill-me <你的功能想法或需求>`\n"
            "範例：`/grill-me 設計一個全域 API 請求限流中間件`\n\n"
            "💡 *Agent 將扮演嚴格架構師主動提出邊界情況與技術約束提問，避免需求模糊直接寫錯。*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    from bot import process_agent_turn, _begin_fresh_task
    uid = update.effective_user.id
    user_req = parts[1].strip()
    prompt = f"{GRILL_SYSTEM_PREFIX}\n{user_req}"

    _begin_fresh_task(uid, prompt)
    await process_agent_turn(
        update=update,
        context=context,
        prompt=prompt,
        original_message_id=update.message.message_id,
    )


# ---------------------------------------------------------------------------
# 4. /goal: Autonomous Continuous Goal Solver
# ---------------------------------------------------------------------------
GOAL_SYSTEM_PREFIX = """\
【系統指令：Google Antigravity 原生 /goal 自主目標攻堅模式】
使用者賦予了你一個明確的終端目標。你必須自主推進、持續排查、編寫測試並反覆驗證，直到目標徹底達成！
【硬性紀律】：
1. 嚴禁因指令退出碼 0 或局部編輯成功就宣告完成，必須執行真實測試驗證行為。
2. 測試若失敗，自行閱讀日誌分析根因並進行最小化修剪修復，嚴禁放棄。
3. 保持自主連貫執行，直至終端目標 100% 達成並給出驗證證據。

【終端目標】：
"""

async def cmd_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /goal command for continuous autonomous task execution."""
    if not update.message:
        return
    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "🎯 **自主目標攻堅模式 (Goal Mode)**\n\n"
            "用法：`/goal <你的終端目標>`\n"
            "範例：`/goal 修復前端所有未定義變數與 ESLint 報錯，並通過本地測試`\n\n"
            "💡 *Agent 將持續自主排查、修剪代碼與驗證除錯，直到目標徹底達成，中途不暫停等待確認。*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    from bot import process_agent_turn, _begin_fresh_task
    uid = update.effective_user.id
    user_req = parts[1].strip()
    prompt = f"{GOAL_SYSTEM_PREFIX}\n{user_req}"

    _begin_fresh_task(uid, prompt)
    await process_agent_turn(
        update=update,
        context=context,
        prompt=prompt,
        original_message_id=update.message.message_id,
    )


# ---------------------------------------------------------------------------
# 5. /boost: High-Reasoning Multi-Perspective Analysis
# ---------------------------------------------------------------------------
BOOST_SYSTEM_PREFIX = """\
【系統指令：Google Antigravity 原生 /boost 深度推理與多視角攻堅模式】
本任務涉及高度複雜架構、潛在競態條件 (Race Conditions)、棘手 Bug 或高難度演算法。
必須啟動最高規格深度推理 (High Reasoning)：
1. 進行多視角對立推導（正向實作 vs 極端併發破壞 vs 資源耗盡場景）。
2. 詳細分析因果鏈條與執行時序，找出深層死鎖或競態根因。
3. 給出具備數學或邏輯證明強度的健壯解決方案。

【攻堅難題】：
"""

async def cmd_boost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /boost command for multi-perspective deep reasoning."""
    if not update.message:
        return
    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "🚀 **深度推理與多視角攻堅 (Boost Mode)**\n\n"
            "用法：`/boost <棘手難題或複雜架構分析>`\n"
            "範例：`/boost 分析訂單重複扣款的併發競態條件並給出分散式鎖設計`\n\n"
            "💡 *啟用最高強度深度思考迴圈，專門攻克複雜演算法、並行時序與深層系統 Bug。*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    from bot import process_agent_turn, _begin_fresh_task
    uid = update.effective_user.id
    user_req = parts[1].strip()
    prompt = f"{BOOST_SYSTEM_PREFIX}\n{user_req}"

    _begin_fresh_task(uid, prompt)
    await process_agent_turn(
        update=update,
        context=context,
        prompt=prompt,
        original_message_id=update.message.message_id,
    )


# ---------------------------------------------------------------------------
# 6. /teamwork: Multi-Agent Team Collaboration
# ---------------------------------------------------------------------------
TEAMWORK_SYSTEM_PREFIX = """\
【系統指令：Google Antigravity 原生 /teamwork 多代理協同團隊模式】
本任務適用於大規模重構、跨模組遷移或多維度調研。
請以團隊指揮官架構協同推進：
1. 需求拆解：將總目標分解為獨立專業子任務（例如：Researcher、Coder、Reviewer、QA）。
2. 工具調度：積極使用 invoke_subagent 調度專屬子代理並行處理。
3. 交付整合：匯總各子代理產出，進行全域一致性審查與整合測試。

【團隊協同目標】：
"""

async def cmd_teamwork(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /teamwork (or /teamwork-preview) command for multi-agent dispatch."""
    if not update.message:
        return
    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "👥 **多 Agent 協同團隊 (Teamwork Mode)**\n\n"
            "用法：`/teamwork <大規模專案重構或跨模組任務>`\n"
            "範例：`/teamwork 全站遷移至 TypeScript 並拆解各模組型別定義`\n\n"
            "💡 *啟動多代理自主團隊架構，調度研究、編程、審查多個 Subagent 並行協作。*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    from bot import process_agent_turn, _begin_fresh_task
    uid = update.effective_user.id
    user_req = parts[1].strip()
    prompt = f"{TEAMWORK_SYSTEM_PREFIX}\n{user_req}"

    _begin_fresh_task(uid, prompt)
    await process_agent_turn(
        update=update,
        context=context,
        prompt=prompt,
        original_message_id=update.message.message_id,
    )


# ---------------------------------------------------------------------------
# 7. /btw: Non-blocking Independent Side Question
# ---------------------------------------------------------------------------
async def cmd_btw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /btw command to ask a side question without polluting session context."""
    if not update.message:
        return
    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "💬 **背景獨立插問 (By-The-Way Mode)**\n\n"
            "用法：`/btw <獨立簡短問題>`\n"
            "範例：`/btw Docker compose 的 restart: unless-stopped 是什麼意思？`\n\n"
            "💡 *在不中斷主任務、不污染主要對話上下文的情況下，快速獲得獨立解答。*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    uid = update.effective_user.id
    side_question = parts[1].strip()

    status_msg = await update.message.reply_text("💡 正在背景獨立回答插問...")

    try:
        from agent_runner import _run_agy_turn
        from session_store import get_user_model
        from config import is_opencode_model

        model = get_user_model(uid)
        btw_prompt = (
            f"[系統指示: 請針對以下獨立插問給予簡潔、專業的回答。]\n"
            f"{side_question}"
        )

        if is_opencode_model(model):
            from opencode_runner import run_opencode_turn
            # Pass user_id=0 or isolated to avoid linking to user's active session_id
            reply_text, _, _ = await run_opencode_turn(prompt=btw_prompt, user_id=0, model=model)
        else:
            # Run single turn without user_id's persistent conversation_id
            reply_text, _, _ = await _run_agy_turn(prompt=btw_prompt, user_id=0)

        card = f"💡 **[BTW 獨立插問解答]**\n\n{reply_text}"
        await status_msg.edit_text(card, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.exception("cmd_btw failed: %s", e)
        await status_msg.edit_text(f"❌ BTW 解答失敗：`{e}`", parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# 8. /browser: Web Retrieval & UI Validation
# ---------------------------------------------------------------------------
BROWSER_SYSTEM_PREFIX = """\
【系統指令：Google Antigravity 原生 /browser 網頁檢索與渲染驗證模式】
請調用聯網搜尋、網頁閱讀或沙盒化瀏覽器工具，對以下目標進行即時內容檢索、UI 排版檢查或渲染驗證：

【檢索/驗證目標】：
"""

async def cmd_browser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /browser command for web retrieval and UI inspection."""
    if not update.message:
        return
    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "🌐 **網頁檢索與渲染驗證 (Browser Mode)**\n\n"
            "用法：`/browser <網址或搜尋目標>`\n"
            "範例：`/browser 檢索 React 19 最新 Server Actions 官方文檔與範例`\n\n"
            "💡 *啟動沙盒化瀏覽工具，進行即時網路資料檢索、文件閱讀或 Web UI 驗證。*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    from bot import process_agent_turn, _begin_fresh_task
    uid = update.effective_user.id
    user_req = parts[1].strip()
    prompt = f"{BROWSER_SYSTEM_PREFIX}\n{user_req}"

    _begin_fresh_task(uid, prompt)
    await process_agent_turn(
        update=update,
        context=context,
        prompt=prompt,
        original_message_id=update.message.message_id,
    )


# ---------------------------------------------------------------------------
# 9. /diff: Zero-Token Local Workspace Diff Inspector
# ---------------------------------------------------------------------------
async def cmd_diff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /diff command: zero-token local git diff inspection."""
    if not update.message:
        return

    repo_dir = WORKSPACE_DIR if (WORKSPACE_DIR and os.path.isdir(WORKSPACE_DIR)) else os.getcwd()

    try:
        # Check git status short
        status_proc = subprocess.run(
            ["git", "status", "--short"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        status_out = status_proc.stdout.strip()
        if not status_out:
            await update.message.reply_text(
                f"🌿 **工作目錄代碼差異報告**\n\n"
                f"📂 目錄：`{repo_dir}`\n"
                f"✅ 目前工作目錄完全乾淨，沒有任何未提交的變更 (Working tree clean)。",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # Check git diff --stat
        stat_proc = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        stat_out = stat_proc.stdout.strip()

        # Check concise diff preview (first 25 lines)
        diff_proc = subprocess.run(
            ["git", "diff", "-U2"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        diff_lines = diff_proc.stdout.splitlines()
        preview = "\n".join(diff_lines[:25])
        if len(diff_lines) > 25:
            preview += f"\n... (其餘 {len(diff_lines) - 25} 行已截斷)"

        msg_parts = [
            f"🔍 **工作目錄代碼變更報告 (Git Diff)**\n",
            f"📂 **路徑**：`{repo_dir}`\n",
            f"📊 **變更檔案清單 (`git status --short`)**：\n```\n{status_out[:600]}\n```",
        ]
        if stat_out:
            msg_parts.append(f"📈 **統計摘要 (`git diff --stat`)**：\n```\n{stat_out[:400]}\n```")
        if preview.strip():
            msg_parts.append(f"📝 **差異預覽**：\n```diff\n{preview[:1000]}\n```")

        await update.message.reply_text("\n\n".join(msg_parts), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.exception("cmd_diff failed: %s", e)
        await update.message.reply_text(f"❌ 取得 Git Diff 失敗：`{e}`", parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# 10. /schedule: Countdown Timer & Scheduled Task
# ---------------------------------------------------------------------------
def _parse_duration(text: str) -> Optional[int]:
    """Parse time expression like '60', '30s', '5m', '2h' into seconds."""
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    m = re.match(r"^(\d+)\s*(s|m|h|sec|min|hour|hours|分鐘|小時|秒)?$", text)
    if not m:
        return None
    val = int(m.group(1))
    unit = m.group(2) or "s"
    if unit in ("m", "min", "分鐘"):
        return val * 60
    elif unit in ("h", "hour", "hours", "小時"):
        return val * 3600
    return val


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /schedule command for countdown timers and scheduled reminders."""
    if not update.message:
        return
    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        await update.message.reply_text(
            "⏱️ **定時排程與倒數計時器 (Schedule Mode)**\n\n"
            "用法：`/schedule <時間> <指令或提醒內容>`\n"
            "範例：\n"
            "• `/schedule 60 檢查日誌` (60 秒後提醒)\n"
            "• `/schedule 10m 重新跑測試` (10 分鐘後提醒)\n"
            "• `/schedule 1h 檢查伺服器健康狀態` (1 小時後提醒)\n\n"
            "💡 *在背景非同步計時，到期後主動發送通知或觸發任務。*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    dur_str = parts[1]
    task_desc = parts[2].strip()
    seconds = _parse_duration(dur_str)

    if not seconds or seconds <= 0:
        await update.message.reply_text(f"❌ 無法解析時間 `{dur_str}`。請使用例如 `60`、`5m`、`1h`。", parse_mode=ParseMode.MARKDOWN)
        return

    chat_id = update.effective_chat.id
    target_time_str = time.strftime("%H:%M:%S", time.localtime(time.time() + seconds))

    await update.message.reply_text(
        f"⏱️ **排程已建立**\n\n"
        f"• 倒數時長：`{seconds}` 秒\n"
        f"• 預計觸發時間：`{target_time_str}`\n"
        f"• 任務內容：`{task_desc}`\n\n"
        f"💡 *計時已在背景啟動，到期後將自動向本聊天室發送提醒！*",
        parse_mode=ParseMode.MARKDOWN,
    )

    async def _timer_worker():
        await asyncio.sleep(seconds)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⏰ **[定時排程到期提醒]**\n\n"
                    f"預定任務：`{task_desc}`\n"
                    f"設定時間：`{seconds}s` 前 ({target_time_str})"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.warning("Failed sending scheduled message to %s: %s", chat_id, e)

    asyncio.create_task(_timer_worker())
