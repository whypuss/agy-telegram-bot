# 繁體中文高效代理協定 (Token-Efficient Agent Protocol)

## 輸出規範 (Output)
- 嚴禁打印大型終端輸出。
- 預期超過 50 行的指令必須重定向到檔案，使用 grep、head、tail 進行定向閱讀。
- 除非使用者明確要求，否則不打印完整 git diff。

## 狀態維護 (State)
- 維護 `~/.antigravity/task_state.md`，記錄核心發現、決策、阻礙與下一步。
- 不重複記錄已知資訊，專注實質進展。

## 程式碼編輯 (Editing)
- 堅持精確修剪（Surgical Edits），能用局部補丁解決的絕不全檔重寫。

## 驗證原則 (Verification)
- 優先執行最小關聯測試。
- 測試失敗時，重定向至日誌並僅檢查相關片段，不重複跑未變更的耗時測試。
- 嚴禁因指令返回碼 0 或編輯成功就假設修改有效，必須驗證受影響的行為。

## Git 規範 (Git)
- 變更前檢查 `git status --short`。
- 詳細 diff 前優先查看 `git diff --stat`。
- 保持提交語義連貫。

## 思考與排版規範 (Thinking & Formatting Style)
- **思維語言（Thinking Language）**：
  思考過程（Thinking / Chain of Thought）必須**全程使用繁體中文**進行深度推理、用戶需求分析與步驟規劃，**嚴禁使用英文思考**！
- **實質思考（Real Reasoning）**：
  思考內容必須是實質的技術取捨、架構分析與方案推導，嚴禁輸出機械式模板套話、空代碼塊或重複前文。
- **直出不折疊（Unfolded Display）**：
  絕不使用可折疊引用塊（**>）或 HTML `<details>` 標籤，所有思考與推導必須以標準段落直出呈現，確保在 Telegram 中一目了然。
