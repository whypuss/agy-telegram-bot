"""
Antigravity IDE Chrome DevTools Protocol (CDP) Controller.
===========================================================
Enables agy-telegram-bot to directly control Antigravity IDE (and Standalone App)
via Chrome DevTools Protocol (CDP), referencing emreturkmencom/antigravity-telegram-suite.

Features:
- WebSocket CDP communication with Electron/Chromium runtime.
- Target discovery and active chat window resolution.
- Remote input injection (focus, clear, type, submit).
- Real-time generation state monitoring (stop button, spinners, idle).
- Interactive question / modal detection (ask_question tool, confirmations).
- Remote screenshot capture (Page.captureScreenshot).
- Auto-accept support (Run, Accept, Allow, Continue).
- DOM & local transcript dual-channel response extraction.
"""

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
import websockets

from config import IDE_CDP_HOST, IDE_CDP_PORT, IDE_AUTO_ACCEPT

logger = logging.getLogger("agy-tg-bot.ide_cdp")

# Common action button texts for auto-accept
PENDING_ACTION_TEXTS = [
    "run", "accept", "allow", "continue", "retry", "proceed",
    "執行", "接受", "允許", "繼續", "重試", "確認",
]

SUBMIT_ACTION_TEXTS = [
    "submit", "send", "send message", "gönder",
    "提交", "發送", "發送消息",
]

# JavaScript locator routines tailored for Antigravity IDE Electron DOM
IDE_LOCATORS_JS = """
var AG_UI = {
    isVisible: function(el) {
        if (!el) return false;
        if (el.classList && el.classList.contains('sr-only') && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) return true;
        var r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
        var s = window.getComputedStyle(el);
        return s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity) > 0;
    },

    getVisibleChatContainer: function() {
        var candidates = [
            '#conversation', '#chat', '#cascade',
            '.chat-container', '.messages-container',
            '[class*="message-list"]', '[class*="Conversation"]',
            '[data-testid*="conversation"]', '[data-testid*="chat"]',
            '.interactive-session', '.agent-chat-view'
        ];
        var containers = Array.from(document.querySelectorAll(candidates.join(', ')));
        for (var i = 0; i < containers.length; i++) {
            var c = containers[i];
            var visible = true;
            var el = c;
            while (el) {
                if (window.getComputedStyle(el).display === 'none') {
                    visible = false;
                    break;
                }
                el = el.parentElement;
            }
            if (visible && c.offsetHeight > 100) return c;
        }
        return document.body;
    },

    getChatInput: function() {
        var candidates = [
            '.interactive-input-editor textarea',
            '#conversation textarea',
            '#chat textarea',
            '.chat-input textarea',
            '.chat-input [contenteditable="true"]',
            '[aria-label*="chat input" i] textarea',
            '[aria-label*="chat input" i] [contenteditable="true"]',
            '[aria-label*="message input" i]',
            '[aria-label*="message input" i] [contenteditable="true"]',
            '[placeholder*="Ask" i] textarea',
            '[placeholder*="Ask" i] [contenteditable="true"]',
            '[placeholder*="Sohbet" i] textarea',
            '[placeholder*="Sohbet" i] [contenteditable="true"]',
            'div[contenteditable="true"]'
        ];
        var editors = Array.from(document.querySelectorAll(candidates.join(', '))).filter(function(el) {
            if (el.className && typeof el.className === 'string' && el.className.includes('xterm')) return false;
            return AG_UI.isVisible(el);
        });
        return editors.length > 0 ? editors[editors.length - 1] : null;
    },

    getStopButton: function() {
        var chatArea = AG_UI.getVisibleChatContainer() || document;
        var stopIcons = Array.from(chatArea.querySelectorAll(
            "svg.lucide-square, [data-tooltip-id*='cancel'], [aria-label*='Stop'], [title*='Stop'], [aria-label*='Cancel'], [title*='Cancel']"
        ));
        for (var i = 0; i < stopIcons.length; i++) {
            var icon = stopIcons[i];
            if (icon.closest('.modal, [role="dialog"], [data-testid*="interactive-modal"]')) continue;
            return icon.closest('button') || icon;
        }
        var allBtns = Array.from(chatArea.querySelectorAll('button'));
        for (var j = 0; j < allBtns.length; j++) {
            var b = allBtns[j];
            if (b.closest('.modal, [role="dialog"], [data-testid*="interactive-modal"]')) continue;
            if (b.querySelector('svg.lucide-square')) return b;
            var t = (b.textContent || '').trim().toLowerCase();
            if (t === 'stop' || t === 'cancel' || t === '中止' || t === '停止') return b;
        }
        return null;
    },

    isLoading: function() {
        var selectors = [
            '.codicon-loading',
            '.loading',
            '[class*="animate-spin"]',
            '[class*="spinner"]',
            '[class*="loader"]',
            '.thinking-indicator'
        ];
        var els = Array.from(document.querySelectorAll(selectors.join(', ')));
        for (var i = 0; i < els.length; i++) {
            var el = els[i];
            if (!AG_UI.isVisible(el)) continue;
            if (el.className && typeof el.className === 'string' && el.className.includes('h-3') && el.className.includes('w-3')) continue;
            return true;
        }
        return false;
    },

    getNewChatButton: function() {
        var svgPath = document.querySelector('path[d="M12 4.5v15m7.5-7.5h-15"]');
        if (svgPath) {
            var btn = svgPath.closest('button, a, [role="button"]');
            if (btn) return btn;
        }
        var iconSelectors = 'svg.lucide-plus, svg.lucide-square-pen, svg.lucide-message-square-plus';
        var icon = document.querySelector(iconSelectors);
        if (icon) {
            var btn2 = icon.closest('button, a, [role="button"]');
            if (btn2) return btn2;
        }
        var selectors = [
            '[aria-label*="New Chat" i]',
            '[title*="New Chat" i]',
            '[aria-label*="New Conversation" i]',
            '[title*="New Conversation" i]'
        ];
        return document.querySelector(selectors.join(', '));
    },

    checkForQuestion: function() {
        var isExcluded = function(el) {
            return !!el.closest('.titlebar, .monaco-workbench .menubar, .monaco-workbench .statusbar, .monaco-workbench .activitybar, .monaco-editor, .editor-widget, .find-widget, .quick-input-widget');
        };
        var allRadios = Array.from(document.querySelectorAll('[role="radio"], input[type="radio"]')).filter(function(el) {
            return AG_UI.isVisible(el) && !isExcluded(el);
        });
        var allCheckboxes = Array.from(document.querySelectorAll('[role="checkbox"], input[type="checkbox"]')).filter(function(el) {
            return AG_UI.isVisible(el) && !isExcluded(el);
        });
        var interactiveElements = [].concat(allRadios, allCheckboxes);

        var container = null;
        if (interactiveElements.length > 0) {
            container = interactiveElements[0].closest('form, fieldset, [role="dialog"], .modal, [class*="rounded"], div.p-4, div.p-3, div.p-2, div.border');
            if (!container) {
                container = interactiveElements[0].parentElement ? interactiveElements[0].parentElement.parentElement : null;
            }
        }

        if (!container) {
            var allContainers = Array.from(document.querySelectorAll('.modal, [role="dialog"], .interactive-session, [data-testid*="interactive-modal"], [data-testid*="question"]')).filter(function(c) {
                return AG_UI.isVisible(c) && !isExcluded(c);
            });
            container = allContainers[0] || null;
        }

        if (!container) {
            var allBtns = Array.from(document.querySelectorAll('button, [role="button"], a')).filter(function(b) {
                return AG_UI.isVisible(b) && !isExcluded(b);
            });
            var submitBtn = allBtns.find(function(b) {
                var t = (b.textContent || '').trim().toLowerCase();
                return t === 'submit' || t === 'skip' || t === 'proceed' || t === '提交' || t === '確認';
            });
            if (submitBtn) {
                container = submitBtn.closest('form, fieldset, [class*="rounded"], div.p-4, div.p-3, div.border') || (submitBtn.parentElement ? submitBtn.parentElement.parentElement : null);
            }
        }

        if (!container) return null;

        var headerEl = container.querySelector('.modal-header, [data-testid*="interactive-modal"] h2, h2, h3, h4, fieldset legend, .font-semibold, .font-medium');
        var header = headerEl ? headerEl.textContent.trim() : '';

        var optionCandidateEls = Array.from(container.querySelectorAll(
            'label, [role="radio"], [role="checkbox"], input[type="radio"], input[type="checkbox"], [data-testid*="option"], div[class*="cursor-pointer"], li'
        )).filter(function(el) {
            return AG_UI.isVisible(el) && !isExcluded(el);
        });

        var options = [];
        var targets = interactiveElements.length > 0 ? interactiveElements : optionCandidateEls;
        for (var i = 0; i < targets.length; i++) {
            var el = targets[i];
            var txt = (el.innerText || el.textContent || '').trim();
            if (!txt || txt.length <= 2) {
                var row = el.closest('label, div.flex, li, [role="button"]') || el.parentElement;
                txt = (row ? (row.innerText || row.textContent || '') : '').trim();
            }
            txt = txt.replace(/^[0-9]+[\\s.)\\-]+/, '').replace(/\\b\\(Recommended\\)\\b/gi, '').trim();
            if (txt && !txt.match(/^(Other|Other \\(write in\\)|Submit|Skip|提交|跳過|\\d+)$/i)) {
                if (options.indexOf(txt) === -1) options.push(txt);
            }
        }

        if (options.length === 0 && !header) return null;

        return {
            header: header || '請選擇操作選項',
            options: options,
            hasWriteIn: !!container.querySelector('textarea, input[type="text"]')
        };
    },

    clickPendingActionButtons: function(allowedTexts) {
        var chatPanel = AG_UI.getVisibleChatContainer();
        if (!chatPanel) return 0;
        var btns = Array.from(chatPanel.querySelectorAll('button')).filter(function(b) {
            return b.offsetParent !== null;
        });
        var clicked = 0;
        for (var i = 0; i < btns.length; i++) {
            var b = btns[i];
            var t = (b.textContent || '').trim().toLowerCase();
            for (var j = 0; j < allowedTexts.length; j++) {
                var candidate = allowedTexts[j].toLowerCase();
                if (t === candidate || t.startsWith(candidate + ' ') || (t.startsWith(candidate) && t.length <= candidate.length + 8)) {
                    b.click();
                    clicked++;
                    break;
                }
            }
        }
        return clicked;
    }
};
"""


class CDPConnection:
    """Low-level WebSocket wrapper for Chrome DevTools Protocol."""

    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._msg_id = 0
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None

    async def connect(self, timeout: float = 5.0) -> None:
        """Establish WebSocket connection to CDP target."""
        self._ws = await asyncio.wait_for(websockets.connect(self.ws_url, max_size=64 * 1024 * 1024), timeout=timeout)
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def _reader_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    data = json.loads(raw)
                    req_id = data.get("id")
                    if req_id in self._pending_requests:
                        future = self._pending_requests.pop(req_id)
                        if not future.done():
                            if "error" in data:
                                future.set_exception(RuntimeError(data["error"].get("message", "CDP Error")))
                            else:
                                future.set_result(data.get("result", {}))
                except Exception as e:
                    logger.debug("CDP message decode error: %s", e)
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            pass

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected and open across websockets versions."""
        if not self._ws:
            return False
        if hasattr(self._ws, "closed"):
            return not self._ws.closed
        state = getattr(self._ws, "state", None)
        if state is not None:
            return getattr(state, "name", "") == "OPEN" or state == 1
        return True

    async def send_command(self, method: str, params: Optional[dict] = None, timeout: float = 10.0) -> dict:
        """Send a JSON-RPC command over CDP and wait for the response."""
        if not self.is_connected:
            raise RuntimeError("CDP WebSocket is not connected")

        self._msg_id += 1
        cid = self._msg_id
        payload = {"id": cid, "method": method, "params": params or {}}
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending_requests[cid] = fut

        await self._ws.send(json.dumps(payload))
        return await asyncio.wait_for(fut, timeout=timeout)

    async def evaluate(self, expression: str, await_promise: bool = True, timeout: float = 10.0) -> Any:
        """Evaluate a JS expression in the target page context."""
        res = await self.send_command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
            timeout=timeout,
        )
        return res.get("result", {}).get("value")

    async def close(self) -> None:
        """Close the CDP WebSocket connection."""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        for fut in self._pending_requests.values():
            if not fut.done():
                fut.cancel()
        self._pending_requests.clear()


async def list_cdp_targets(host: str = IDE_CDP_HOST, port: int = IDE_CDP_PORT, timeout: float = 2.0) -> List[dict]:
    """Query http://host:port/json for all available CDP targets."""
    url = f"http://{host}:{port}/json"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def is_ide_cdp_online(host: str = IDE_CDP_HOST, port: int = IDE_CDP_PORT) -> bool:
    """Check if Antigravity IDE CDP debugging port is accessible."""
    try:
        targets = await list_cdp_targets(host, port, timeout=1.5)
        return bool(targets)
    except Exception:
        return False


async def resolve_active_target(host: str = IDE_CDP_HOST, port: int = IDE_CDP_PORT) -> Optional[dict]:
    """Find and select the most relevant Antigravity IDE chat target.
    
    Excludes devtools, Launchpad, extension background pages, and picks
    the primary editor or chat webview.
    """
    try:
        raw_targets = await list_cdp_targets(host, port)
    except Exception as e:
        logger.debug("Failed listing CDP targets: %s", e)
        return None

    candidates = [
        t for t in raw_targets
        if t.get("webSocketDebuggerUrl")
        and not "devtools://" in t.get("url", "")
        and not "Launchpad" in t.get("title", "")
        and t.get("title") != "Manager"
        and t.get("type") in ("page", "webview", "iframe")
    ]

    if not candidates:
        return None

    # Priority 1: explicitly Antigravity IDE chat or conversation
    for c in candidates:
        text = f"{c.get('title', '')} {c.get('url', '')}".lower()
        if "antigravity" in text or "workbench" in text or "vscode-file" in text or "/c/" in text:
            return c

    return candidates[0]


class AntigravityIDEController:
    """High-level controller for Antigravity IDE via Chrome DevTools Protocol."""

    def __init__(self, host: str = IDE_CDP_HOST, port: int = IDE_CDP_PORT):
        self.host = host
        self.port = port
        self.auto_accept = IDE_AUTO_ACCEPT

    async def get_connection(self) -> CDPConnection:
        """Create and connect a fresh CDP connection to the active IDE window."""
        target = await resolve_active_target(self.host, self.port)
        if not target or not target.get("webSocketDebuggerUrl"):
            raise ConnectionError(
                f"無法連線至 Antigravity IDE (埠 {self.port})。\n"
                f"請確認 Antigravity IDE 已啟動並帶有 `--remote-debugging-port={self.port}` 參數。\n"
                f"例如 macOS 啟動命令: open -a \"Antigravity IDE\" --args --remote-debugging-port={self.port}"
            )
        conn = CDPConnection(target["webSocketDebuggerUrl"])
        await conn.connect()
        # Enable necessary CDP domains
        await conn.send_command("Runtime.enable")
        await conn.send_command("Page.enable")
        return conn

    async def ping(self) -> bool:
        """Check if IDE CDP is responsive."""
        try:
            conn = await self.get_connection()
            try:
                res = await conn.evaluate("1 + 1")
                return res == 2
            finally:
                await conn.close()
        except Exception:
            return False

    async def send_prompt(self, prompt: str) -> bool:
        """Focus the IDE chat input, paste the text, and submit."""
        conn = await self.get_connection()
        try:
            # 1. Inject locators and focus chat input
            js_prepare = IDE_LOCATORS_JS + """
            (function() {
                var editor = AG_UI.getChatInput();
                if (!editor) return false;
                editor.focus();
                try {
                    if (editor.tagName === 'TEXTAREA' || editor.tagName === 'INPUT') {
                        editor.value = '';
                    } else {
                        editor.innerHTML = '';
                    }
                    editor.dispatchEvent(new Event('input', { bubbles: true }));
                } catch(e) {}
                return true;
            })();
            """
            focused = await conn.evaluate(js_prepare)
            if not focused:
                raise RuntimeError("在 Antigravity IDE 中找不到活躍的對話輸入框 (Chat Input)")

            # 2. Insert text via Input.insertText (preserves multiline, emojis, codeblocks)
            await conn.send_command("Input.insertText", {"text": prompt})
            await asyncio.sleep(0.15)

            # 3. Press Enter to submit (or click Send button)
            js_submit = IDE_LOCATORS_JS + f"""
            (function() {{
                var editor = AG_UI.getChatInput();
                var chatArea = AG_UI.getVisibleChatContainer() || document;
                var sendBtn = null;
                
                // Try finding Send / Submit button in chat panel
                var allBtns = Array.from(chatArea.querySelectorAll('button, [role="button"]'));
                var sendKeywords = {json.dumps(SUBMIT_ACTION_TEXTS)};
                for (var i = 0; i < allBtns.length; i++) {{
                    var b = allBtns[i];
                    var aria = (b.getAttribute('aria-label') || '').toLowerCase();
                    var title = (b.getAttribute('title') || '').toLowerCase();
                    var txt = (b.textContent || '').trim().toLowerCase();
                    for (var j = 0; j < sendKeywords.length; j++) {{
                        var kw = sendKeywords[j];
                        if (aria.indexOf(kw) !== -1 || title.indexOf(kw) !== -1 || txt === kw) {{
                            sendBtn = b;
                            break;
                        }}
                    }}
                    if (sendBtn) break;
                    if (b.querySelector('svg.lucide-send, svg.lucide-arrow-up, .codicon-send')) {{
                        sendBtn = b;
                        break;
                    }}
                }}
                
                if (sendBtn) {{
                    sendBtn.click();
                    return 'button-clicked';
                }}

                // Fallback: Dispatch Enter key event
                if (editor) {{
                    var enterDown = new KeyboardEvent('keydown', {{
                        key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true
                    }});
                    editor.dispatchEvent(enterDown);
                    var enterUp = new KeyboardEvent('keyup', {{
                        key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true
                    }});
                    editor.dispatchEvent(enterUp);
                    return 'enter-dispatched';
                }}
                return 'none';
            }})();
            """
            await conn.evaluate(js_submit)
            return True
        finally:
            await conn.close()

    async def get_state(self) -> dict:
        """Query the current state of Antigravity IDE chat including active steps and completion status.
        
        Returns:
            dict containing:
            - is_generating: bool (Stop button present)
            - is_loading: bool (Spinners/Thinking active)
            - question: Optional[dict] (Interactive modal/ask_question prompt)
            - current_step: Optional[str] (e.g. '🧠 Thought for 1s', '⚙️ Running 3 commands')
            - all_steps: List[str] (Full list of steps in current turn)
            - has_response: bool (True if assistant markdown response is rendered)
            - auto_accepted: int
        """
        conn = await self.get_connection()
        try:
            js_check = IDE_LOCATORS_JS + f"""
            (function() {{
                var stopBtn = AG_UI.getStopButton();
                var loading = AG_UI.isLoading();
                var question = AG_UI.checkForQuestion();
                var autoAccepted = 0;
                
                // If auto_accept enabled, click pending buttons
                if ({json.dumps(self.auto_accept)}) {{
                    autoAccepted = AG_UI.clickPendingActionButtons({json.dumps(PENDING_ACTION_TEXTS)});
                }}

                var allSteps = [];
                var currentStep = null;
                var hasResponse = false;

                var turns = Array.from(document.querySelectorAll("div[class*='scroll-mt-4']"));
                if (turns.length > 0) {{
                    var lastTurn = turns[turns.length - 1];
                    if (lastTurn.children.length > 1) {{
                        var asst = lastTurn.children[1];
                        var respEl = Array.from(asst.children).find(function(c) {{
                            return c.tagName === 'DIV' && (c.className === 'px-2 py-1' || c.classList.contains('rendered-markdown'));
                        }});
                        hasResponse = !!respEl;
                    }}

                    var seen = {{}};
                    var items = Array.from(lastTurn.querySelectorAll("button, div.whitespace-nowrap"));
                    for (var i = 0; i < items.length; i++) {{
                        var el = items[i];
                        var txt = (el.innerText || '').trim();
                        if (!txt) continue;
                        var lines = txt.split('\\n').map(function(s) {{ return s.trim(); }}).filter(Boolean);
                        var first = lines[0];
                        var step = null;
                        if (first.startsWith('Thought for') || first.startsWith('Worked for')) {{
                            step = '🧠 ' + first;
                        }} else if (first.startsWith('Running')) {{
                            step = '⚙️ ' + lines.join(' ');
                        }} else if (first.startsWith('Ran')) {{
                            var cmd = lines.join(' ');
                            if (cmd.length > 55) cmd = cmd.slice(0, 52) + '...';
                            step = '✓ ⚙️ ' + cmd;
                        }} else if (first.startsWith('Explored')) {{
                            step = '✓ 📂 ' + lines.join(' ');
                        }} else if (first.startsWith('Edited')) {{
                            var fname = lines[1] ? ' ' + lines[1] : '';
                            step = '✓ ✏️ ' + first + fname;
                        }} else if (first.startsWith('Working')) {{
                            step = '▶ ' + first;
                        }}
                        if (step && !seen[step]) {{
                            seen[step] = true;
                            allSteps.push(step);
                        }}
                    }}
                    if (allSteps.length > 0) {{
                        currentStep = allSteps[allSteps.length - 1];
                    }}
                }}

                return {{
                    is_generating: !!stopBtn,
                    is_loading: loading,
                    question: question,
                    current_step: currentStep,
                    all_steps: allSteps,
                    has_response: hasResponse,
                    auto_accepted: autoAccepted
                }};
            }})();
            """
            res = await conn.send_command("Runtime.evaluate", {"expression": js_check, "returnByValue": True})
            return res.get("result", {}).get("value") or {}
        finally:
            await conn.close()

    async def get_active_model(self) -> str:
        """Get the current model selected in Antigravity IDE UI."""
        try:
            conn = await self.get_connection()
            try:
                js = "document.querySelector('[data-testid=\"model-selector-trigger\"]')?.innerText.trim() || ''"
                res = await conn.send_command("Runtime.evaluate", {"expression": js, "returnByValue": True})
                val = res.get("result", {}).get("value")
                return (val or "").replace("\n", " ").strip()
            finally:
                await conn.close()
        except Exception as e:
            logger.debug("Failed getting IDE active model: %s", e)
            return ""

    async def list_available_models(self) -> List[str]:
        """List available models in the IDE model dropdown."""
        try:
            conn = await self.get_connection()
            try:
                # Open model menu
                await conn.send_command("Runtime.evaluate", {"expression": "document.querySelector('[data-testid=\"model-selector-trigger\"]')?.click()"})
                await asyncio.sleep(0.3)
                js = """
                (function() {
                    return Array.from(document.querySelectorAll("[role=menuitem]")).map(m => m.innerText.split("\\n")[0].trim()).filter(Boolean);
                })()
                """
                res = await conn.send_command("Runtime.evaluate", {"expression": js, "returnByValue": True})
                models = res.get("result", {}).get("value") or []
                # Close menu
                await conn.send_command("Runtime.evaluate", {"expression": "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))"})
                return models
            finally:
                await conn.close()
        except Exception as e:
            logger.debug("Failed listing IDE models: %s", e)
            return []

    async def select_model(self, model_name: str) -> Optional[str]:
        """Switch active model in Antigravity IDE UI by matching target name."""
        try:
            conn = await self.get_connection()
            try:
                # Open menu
                await conn.send_command("Runtime.evaluate", {"expression": "document.querySelector('[data-testid=\"model-selector-trigger\"]')?.click()"})
                await asyncio.sleep(0.3)
                js_select = f"""
                (function() {{
                    var target = {json.dumps(model_name.lower().strip())};
                    var items = Array.from(document.querySelectorAll("[role=menuitem]"));
                    var matched = items.find(function(el) {{
                        var txt = el.innerText.toLowerCase();
                        var first = txt.split('\\n')[0].trim();
                        return txt.includes(target) || target.includes(first);
                    }});
                    if (matched) {{
                        matched.click();
                        return matched.innerText.split('\\n')[0].trim();
                    }}
                    return null;
                }})()
                """
                res = await conn.send_command("Runtime.evaluate", {"expression": js_select, "returnByValue": True})
                picked = res.get("result", {}).get("value")
                await asyncio.sleep(0.3)
                # Ensure menu closed
                await conn.send_command("Runtime.evaluate", {"expression": "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))"})
                # Verify actual active model
                await asyncio.sleep(0.2)
                active = await conn.send_command("Runtime.evaluate", {
                    "expression": "document.querySelector('[data-testid=\"model-selector-trigger\"]')?.innerText.trim() || ''",
                    "returnByValue": True
                })
                active_val = (active.get("result", {}).get("value") or "").replace("\n", " ").strip()
                return active_val or picked
            finally:
                await conn.close()
        except Exception as e:
            logger.error("Failed selecting IDE model %s: %s", model_name, e)
            return None



    async def stop(self) -> bool:
        """Click the Stop / Cancel button in Antigravity IDE."""
        conn = await self.get_connection()
        try:
            js_stop = IDE_LOCATORS_JS + """
            (function() {
                var stopBtn = AG_UI.getStopButton();
                if (stopBtn) {
                    stopBtn.click();
                    return true;
                }
                return false;
            })();
            """
            return bool(await conn.evaluate(js_stop))
        finally:
            await conn.close()

    async def new_chat(self) -> bool:
        """Click the New Chat button to start a fresh session."""
        conn = await self.get_connection()
        try:
            js_new = IDE_LOCATORS_JS + """
            (function() {
                var btn = AG_UI.getNewChatButton();
                if (btn) {
                    btn.click();
                    return true;
                }
                return false;
            })();
            """
            return bool(await conn.evaluate(js_new))
        finally:
            await conn.close()

    async def answer_question(self, choice_text_or_idx: Any) -> bool:
        """Select an option in an interactive modal / ask_question dialog and submit."""
        conn = await self.get_connection()
        try:
            js_answer = IDE_LOCATORS_JS + f"""
            (function() {{
                var q = AG_UI.checkForQuestion();
                if (!q) return false;
                
                var choice = {json.dumps(str(choice_text_or_idx))};
                var choiceIdx = parseInt(choice, 10);
                
                var container = document.querySelector('.modal, [role="dialog"], fieldset, form') || document.body;
                var radios = Array.from(container.querySelectorAll('[role="radio"], input[type="radio"], [role="checkbox"], input[type="checkbox"], label'));
                
                var matched = null;
                if (!isNaN(choiceIdx) && choiceIdx >= 1 && choiceIdx <= radios.length) {{
                    matched = radios[choiceIdx - 1];
                }} else {{
                    for (var i = 0; i < radios.length; i++) {{
                        var r = radios[i];
                        var t = (r.textContent || r.innerText || '').toLowerCase();
                        if (t.includes(choice.toLowerCase())) {{
                            matched = r;
                            break;
                        }}
                    }}
                }}
                
                if (matched) {{
                    matched.click();
                }}
                
                // Click Submit / Proceed button
                var btns = Array.from(container.querySelectorAll('button, [role="button"]'));
                var submitBtn = btns.find(function(b) {{
                    var t = (b.textContent || '').trim().toLowerCase();
                    return t === 'submit' || t === 'proceed' || t === '提交' || t === '確認';
                }});
                if (submitBtn) {{
                    submitBtn.click();
                    return true;
                }}
                return !!matched;
            }})();
            """
            return bool(await conn.evaluate(js_answer))
        finally:
            await conn.close()

    async def capture_screenshot(self, quality: int = 85) -> bytes:
        """Capture a screenshot of the Antigravity IDE window via CDP.
        
        Returns JPEG image bytes.
        """
        conn = await self.get_connection()
        try:
            res = await conn.send_command(
                "Page.captureScreenshot",
                {"format": "jpeg", "quality": quality},
                timeout=15.0,
            )
            raw_base64 = res.get("data", "")
            if not raw_base64:
                raise RuntimeError("Page.captureScreenshot returned empty data")
            return base64.b64decode(raw_base64)
        finally:
            await conn.close()

    async def get_latest_response(self, timeout_read: float = 3.0) -> Tuple[str, Optional[str]]:
        """Extract the latest assistant response and thinking from Antigravity IDE.
        
        Returns:
            Tuple of (response_text, thinking_text)
        """
        try:
            conn = await self.get_connection()
            try:
                js_extract = """
                (function() {
                    var turns = Array.from(document.querySelectorAll("div[class*='scroll-mt-4']"));
                    if (!turns.length) return null;
                    var lastTurn = turns[turns.length - 1];
                    if (lastTurn.children.length < 2) return null;
                    var assistantBlock = lastTurn.children[1];

                    // 1. Primary: Antigravity IDE assistant message container has exact class "px-2 py-1"
                    var respDiv = Array.from(assistantBlock.children).find(function(c) {
                        return c.tagName === 'DIV' && (c.className === 'px-2 py-1' || c.classList.contains('rendered-markdown'));
                    });
                    var responseText = respDiv ? respDiv.innerText.trim() : null;

                    // 2. Fallback: Search for leading-relaxed inside assistantBlock
                    if (!responseText) {
                        var textEl = assistantBlock.querySelector("div[class*='leading-relaxed']");
                        if (textEl) {
                            responseText = textEl.innerText.trim();
                        }
                    }

                    // 3. Fallback: clone assistantBlock and strip buttons/tools
                    if (!responseText) {
                        var clone = assistantBlock.cloneNode(true);
                        clone.querySelectorAll("button, div.whitespace-nowrap, div.relative, [class*='rounded-lg']").forEach(function(el) {
                            el.remove();
                        });
                        responseText = clone.innerText.trim();
                    }

                    // 4. Extract thinking / time
                    var workedBtn = assistantBlock.querySelector("button, div.relative");
                    var thinkingText = null;
                    if (workedBtn && (workedBtn.innerText.includes("Worked") || workedBtn.innerText.includes("Thought"))) {
                        thinkingText = workedBtn.innerText.trim();
                    }

                    return {
                        response: responseText || "",
                        thinking: thinkingText
                    };
                })()
                """
                res = await conn.send_command("Runtime.evaluate", {"expression": js_extract, "returnByValue": True}, timeout=timeout_read)
                val = res.get("result", {}).get("value")
                if isinstance(val, dict):
                    resp = (val.get("response") or "").strip()
                    th = val.get("thinking")
                    if resp:
                        return resp, th
            finally:
                await conn.close()
        except Exception as e:
            logger.debug("DOM response extraction failed: %s", e)

        return "", None


# Singleton instance
ide_controller = AntigravityIDEController()
