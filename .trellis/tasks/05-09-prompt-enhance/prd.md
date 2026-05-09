# Sub-4: Prompt 增强 + Model 扩展

## Goal

增强分类和摘要 prompt，增加新的返回字段，借鉴 Demo 项目的 AI 分类 prompt 设计。

## Requirements

### 分类 Prompt 增强（`src/postowl/agent/classifier.py`）

1. `ClassificationResult` 模型新增字段：
   - `suggested_action: str` — 值为 `"archive"` / `"star"` / `"notify"` / `"none"`
   - `confidence: float` — 0.0 到 1.0 的分类置信度
   - `requires_reply: bool` — 是否需要用户回复

2. `CLASSIFY_PROMPT` 更新：
   - 增加 suggested_action、confidence、requires_reply 的说明和输出格式
   - 加入防过度分类指令：
     ```
     Be discerning - not every email containing "important" is truly urgent.
     Newsletters and automated notifications should not be marked as high priority.
     Consider context and sender reputation, not just keywords.
     ```
   - 参考 Demo 的 prompt 风格，列出明确的分析维度（sender domain, subject content, body content, transaction indicators）

3. JSON 返回格式更新：
   ```json
   {
     "category": "<category>",
     "priority": 0|1|2,
     "suggested_action": "archive|star|notify|none",
     "confidence": 0.0-1.0,
     "requires_reply": true|false,
     "reason": "<brief reason>"
   }
   ```

### 摘要 Prompt 增强（`src/postowl/agent/summarizer.py`）

1. `SummaryResult` 模型新增字段：
   - `deadline: str | None` — 提取的截止日期（ISO 格式或自然语言描述）
   - `mentioned_amounts: list[str]` — 提取的金额列表（如 `["$5,000", "¥30,000"]`）

2. `SUMMARIZE_PROMPT` 更新：
   - 增加 deadline 和 mentioned_amounts 的提取指令
   - JSON 返回格式增加对应字段

### 模型更新（`src/postowl/models.py`）

- `ClassificationResult` 增加三个字段（都有默认值，向后兼容）
- `SummaryResult` 增加两个字段（都有默认值，向后兼容）

## Acceptance Criteria

- [ ] `classify_email()` 返回的结果包含 suggested_action、confidence、requires_reply
- [ ] confidence 字段在 0.0-1.0 范围内
- [ ] suggested_action 值为 archive/star/notify/none 之一
- [ ] `summarize_email()` 返回的结果包含 deadline 和 mentioned_amounts
- [ ] 现有 `chat_json()` 调用失败时，新字段有合理默认值
- [ ] 现有 fetch 命令正常工作（向后兼容）

## Technical Notes

- 参考 Demo prompt：`claude-agent-sdk-demos-main/email-agent/agent/.claude/skills/listener-creator/templates/ai-classifier.ts`
- 参考 Demo prompt：`claude-agent-sdk-demos-main/email-agent/agent/.claude/skills/listener-creator/templates/urgent-watcher.ts`
- 参考 Demo prompt：`claude-agent-sdk-demos-main/email-agent/agent/custom_scripts/listeners/finance-email-labeler.ts`
