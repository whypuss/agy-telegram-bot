# AGY Telegram Bot 自我進化系統 (Hermes-Agent Inspired) 計劃與任務清單

> **文檔名稱**：`agymmm.md`  
> **更新時間**：2026-09-12  
> **系統定位**：借鑑 NousResearch Hermes-Agent 的「經驗沉澱與自我進化」哲學，為本地 `agy-telegram-bot` 構建的非阻塞、去沙盒化、受控進化的長期記憶與行為規則閉環。

---

## 🧭 一、核心架構原則與邊界

本系統嚴格遵循用戶設定的 5 大核心原則與 1 項安全紅線：

1. **背景自省（Non-blocking Background Reflection）**
   - Agent 主對話與工具執行完畢後立即回覆 Telegram 用戶。
   - 完整執行軌跡（Trajectory，包含 Prompt、工具調用參數與返回結果）非同步寫入 SQLite 佇列，背景 Worker 自動分析，絕不阻塞前端聊天。
2. **記憶與技能分層（Layered Architecture）**
   - 👤 **用戶偏好（`USER.md`）**：用戶長期溝通風格、操作習慣，可自動安全合流。
   - 🖥️ **環境事實（`MEMORY.md`）**：服務器 IP、端口、硬體型號等客觀環境信息，自動增補。
   - 🛡️ **行為策略（`policies/*.json`）**：涉及生產機操作、網絡設備、重啟服務等安全紅線，強約束運行時注入。
   - 🛠️ **類級別技能（`skills/<class>/`）**：通用任務 SOP、故障排查與常見陷阱（Pitfalls）。
3. **類級別技能（Class-Level Skills）**
   - 拒絕單次、碎片化的 Skill 生成。
   - 所有經驗沉澱歸納至通用大類：`server-operations`、`network-device`、`bot-deployment`、`multimedia-processing` 等。
4. **受控進化與人工確認閥門（Gatekeeper & Proposals）**
   - 低風險偏好/事實：置信度 ≥ 0.85 且無風險關鍵詞時自動 commit。
   - 高風險策略與技能：**嚴格禁止 LLM 自動生效**。一律打包為 `Proposal`（提案）存入 `data/proposals/`，並透過 Telegram 即時推送審批卡片，經用戶點擊按鈕或輸入 `/approve` 後方可轉為正式 Policy / Skill。
5. **確定性生命週期狀態機（Deterministic Lifecycle）**
   - 規則狀態流轉：`active` → `stale`（30 天未使用） → `archived`（90 天歸檔至 `.archive/`）。
   - **只歸檔、不物理刪除**：保留完整歷史審計。
   - **Pinned 豁免**：凡標記為 `pinned: true` 的核心規則，絕對免疫任何自動過期或歸檔。
6. **🛑 安全紅線（No Sandbox & No Uncontrolled Overwrite）**
   - **絕對不做沙盒**：Reviewer 被設計為純函數 API 文本評估器（Zero Tools registered），天然不具備任何終端或代碼執行權限，徹底杜絕沙盒逃逸風險。
   - **禁止全量覆寫**：禁止 LLM 自動整份覆寫 `MEMORY.md` 或系統提示詞，必須使用差量追加或原子替換。

---

## 🏗️ 二、系統模組結構

```
projects/agy-telegram-bot/
├── evolution/                         # 自我進化核心引擎
│   ├── __init__.py                    # 模組統一匯出入口
│   ├── schema.py                      # 數據協議 (EvolutionCandidate, Proposal, Policy)
│   ├── queue.py                       # SQLite 持久化任務佇列 (review_jobs)
│   ├── evaluator.py                   # 純函數無沙盒自省評估器 (SenseNova/OpenRouter)
│   ├── gate.py                        # 確定性分流閥門 & 人工提案審批流
│   ├── lifecycle.py                   # 確定性生命週期狀態機 (active/stale/archived)
│   └── worker.py                      # 非同步後台輪詢守護協程 (Daemon Loop)
├── data/
│   ├── evolution_jobs.db              # 審查任務與軌跡 SQLite 數據庫
│   ├── policies/                      # 已生效的高優先級行為約束 (JSON)
│   │   └── .archive/                  # 歷史歸檔策略
│   ├── proposals/                     # 待審核的提案庫 (JSON)
│   └── skills/                        # 類級別沉澱技能
├── agent_runner.py                    # 軌跡攔截與入隊掛載點
├── memory_manager.py                  # 策略運行時注入 (### 🚨 HARDLINE BEHAVIOR POLICIES)
├── bot.py                             # Telegram 指令 (/proposals, /approve, /reject) & 回調按鈕
└── ui_components.py                   # 說明卡片與 UI 註冊
```

---

## 📋 三、詳細任務與執行進度清單

### Phase 1：基礎設施與數據規範 (Done)
- [x] 建立 `evolution/` 套件目錄
- [x] 編寫 `evolution/schema.py` 數據規格（Proposal、Candidate、Policy 實體）
- [x] 編寫 `evolution/queue.py` 基於 SQLite 的重啟不丟失審查佇列

### Phase 2：純函數自省評估器與安全分流 (Done)
- [x] 編寫 `evolution/evaluator.py` 純函數軌跡自省模組（嚴格提示詞過濾無效寒暄，提取根因）
- [x] 編寫 `evolution/gate.py` 確定性分流閥門（0.85 置信度門檻、臨時關鍵詞黑名單、高風險自動生成 Proposal、手動批准與駁回邏輯）
- [x] 編寫 `evolution/lifecycle.py` 確定性生命週期管理（Pinned 豁免、30天 stale、90天 archived）

### Phase 3：後台 Worker 與執行時掛載 (Done)
- [x] 編寫 `evolution/worker.py` 非同步輪詢協程與 Telegram 即時審批推送
- [x] 修改 `memory_manager.py`，新增 `read_active_policies()` 並注入至 `build_memory_context()` 最高優先級
- [x] 修改 `agent_runner.py`，在每輪 Agent 任務完成後非同步捕獲完整 Trajectory 並入隊

### Phase 4：Telegram 互動與審批閉環 (Done)
- [x] 修改 `bot.py`，導入 Evolution 模組並在 `post_init` 啟動背景 Worker
- [x] 在 Telegram 註冊 `/proposals`、`/approve <id>`、`/reject <id>`
- [x] 在 `handle_callback_query` 中新增 `evo:app:` 與 `evo:rej:` 的 Inline 按鈕即時點擊審批
- [x] 更新 `ui_components.py` 的 `/help` 說明卡片

### Phase 5：編譯校驗與服務部署 (Current / Ongoing)
- [x] 全項目 Python 語法校驗 (`python3 -m py_compile` 100% 通過)
- [ ] 透過 `launchctl` 重啟 `com.whypuss.agy-telegram-bot` 載入新版本
- [ ] 檢查 `bot.log` 與 `bot.err.log`，驗證 Worker 初始化日誌

### Phase 6：端到端整合測試 (Pending)
- [ ] **對話無阻塞測試**：發送指令確認 Telegram 回覆流暢度不受背景分析影響
- [ ] **低風險偏好捕獲測試**：發送偏好指示驗證 `USER.md` 自動沉澱
- [ ] **高風險策略審批測試**：模擬伺服器操作糾正對話，確認生成 Proposal 並推送 Telegram 審批卡片
- [ ] **點擊審批測試**：點擊「✅ 批准」按鈕，驗證生成 `policies/pol_xxx.json` 且下輪對話即時生效

### Phase 7：長期運維與生命週期排程 (Pending)
- [ ] 驗證 30 天 stale 標記與 90 天 archive 移入機制
- [ ] 驗證 `pinned: true` 策略豁免生命週期回收

---

## 🎮 四、Telegram 審批與操作指南

| 指令 | 說明 | 範例 |
| :--- | :--- | :--- |
| `/proposals` | 查看當前所有待審批的進化提案列表（附帶快捷操作按鈕） | `/proposals` |
| `/approve <id>` | 批准指定提案，將其轉為永久生效的 Policy 或 Skill | `/approve pol_1a2b3c4d` |
| `/reject <id>` | 駁回指定提案，將其從待審池中移除 | `/reject pol_1a2b3c4d` |
| `/memory` | 查看本機持久化記憶（USER.md / MEMORY.md） | `/memory` |
| `/memory search <關鍵字>` | 檢索本地記憶庫 | `/memory search 192.168` |

---

## 🛠️ 五、維護與故障排除

1. **服務重啟**
   ```bash
   cd projects/agy-telegram-bot
   ./restart.sh
   # 或手動 launchctl 重啟
   launchctl kickstart -k gui/$(id -u)/com.whypuss.agy-telegram-bot
   ```
2. **查看運作狀態與日誌**
   ```bash
   ./status.sh
   tail -f bot.log
   ```
3. **數據庫與策略檔案位置**
   - SQLite 佇列：`projects/agy-telegram-bot/data/evolution_jobs.db`
   - 生效策略庫：`projects/agy-telegram-bot/data/policies/*.json`
   - 待審提案庫：`projects/agy-telegram-bot/data/proposals/*.json`
