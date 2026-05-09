# 智能邮件代理增强：分层记忆、自进化规则、工作记忆

## Goal

借鉴 GenericAgent 的架构思想，将 PostOwl 从"RAG 问答工具"升级为"智能邮件代理"。核心理念：分层记忆替代暴力检索、对话上下文持续记忆、从用户行为中自动学习规则、处理失败时智能升级策略。

## Research References

- [`research/generic-agent-analysis.md`](../05-09-email-enhancement/research/generic-agent-analysis.md) — GenericAgent 5层记忆架构、自进化技能、工作记忆检查点、失败升级策略

## Requirements

分两批实现。

---

### 第一批：分层记忆 + 对话摘要优化 RAG

#### Sub-1: 分层记忆系统（Memory Layers）

新增 `src/postowl/memory/` 子包，实现邮件知识的分层管理：

**L1 索引层** — `memory/index.py`
- 维护一个 ≤30 行的轻量索引，包含：用户的活跃项目/话题、重要联系人及其关系、待办事项计数、近期邮件模式
- 由 LLM 在邮件处理后自动更新（每处理 N 封邮件触发一次索引刷新）
- 存储在 SQLite `memory_layers` 表（key-value 格式，key=层级标识）
- RAG 查询时，L1 索引**始终注入到 system prompt**中，让 LLM 知道"用户的世界"

**L2 事实层** — `memory/facts.py`
- 结构化的联系人画像：`{email, name, relationship, 常见话题, 最后联系时间}`
- 从邮件历史中自动提取（LLM 分析发件人模式）
- 存储在 SQLite `contacts` 表
- RAG 查询时，按需检索相关联系人上下文

**L3 规则层** — 现有 `listener/` 系统
- 已有，无需重复建设

**L4 归档层** — 现有 ChromaDB
- 已有，是 RAG 的底层数据

**查询流程改进：**
```
用户问题 → 读取 L1 索引（始终在 context 中）
         → L1 指引精准检索方向（哪些联系人、哪个时间段、什么话题）
         → L2 补充联系人上下文
         → L4 向量检索具体邮件
         → LLM 综合回答
```

#### Sub-2: 对话摘要优化 RAG

现在 RAG 每次把完整邮件内容（每封 2000 chars）塞进 context，10 封就 20K token。

**优化方案：**
- RAG 检索后，先展示邮件的**一行摘要列表**给 LLM
- LLM 判断需要哪几封的全文，再按需拉取
- 分两阶段：Phase 1 = 摘要筛选（便宜），Phase 2 = 全文分析（仅必要的几封）

**实现：**
- `RAGEngine.query()` 改为两阶段：
  1. 向量检索 top-20 → 只传 `{sender, subject, date, summary}` 给 LLM
  2. LLM 返回 JSON `{"relevant_ids": [1, 3, 7], "reasoning": "..."}`
  3. 仅拉取这几封的全文，再次调用 LLM 生成最终回答
- 总 token 消耗下降 60-80%，同时检索面更广（top-20 vs top-10）

---

### 第二批：自进化规则、失败升级、工作记忆

#### Sub-3: 自进化邮件规则

当用户通过 Telegram 对邮件执行重复操作时，系统自动建议生成 listener 规则。

**触发场景：**
- 用户连续 3 次对同一类发件人的邮件做相同操作（如"忽略 newsletter 类"）
- 用户手动说 "以后 X 公司的发票帮我标记"

**实现：**
- 新增 `listener/learner.py`
- 记录用户操作日志到 SQLite `user_actions` 表（action_type, email_pattern, timestamp）
- 定期（或实时）分析操作日志，发现重复模式
- 检测到模式后通过 Telegram 询问用户 "我发现你经常忽略来自 X 的邮件，要自动归档吗？"
- 用户确认后自动创建 listener 规则

#### Sub-4: 失败升级策略

现在 LLM/IMAP 调用失败就 fallback 默认值，静默丢失信息。

**三级升级：**
1. 第一次失败 → 读错误信息，换参数重试（如减少 body 长度）
2. 第二次失败 → 分析邮件结构（编码、MIME 类型、大小），尝试替代解析
3. 第三次失败 → 标记为 `processing_failed`，通过 Telegram 通知用户，附带诊断信息

**涉及文件：** `pipeline.py`, `agent/classifier.py`, `agent/summarizer.py`

#### Sub-5: 工作记忆（Working Memory）

在 Telegram 对话中维护一个跨消息的短期上下文。

**实现：**
- `PostOwlBot` 新增 `_working_memory: dict[int, WorkingMemory]`（per-user）
- `WorkingMemory` 包含：`topic`（当前关注话题）、`context`（最近 5 条问答摘要）、`last_active`（超时自动清除）
- RAG 查询时，working memory 作为额外上下文注入，让 LLM 知道"用户在追问什么"
- 每次 RAG 回答后，自动把 `{question, answer_summary}` 追加到 working memory
- 30 分钟无活动自动过期

**效果：**
```
用户: "最近有什么账单？"
→ RAG 回答，working_memory = {topic: "账单", context: ["Q: 账单? A: 七牛云¥15.61, 腾讯云¥65, Mkcloud¥268"]}

用户: "哪个最贵？"
→ LLM 看到 working_memory，知道在追问账单，不需要重新搜索
```

## Acceptance Criteria

### 第一批
- [ ] L1 索引在 RAG system prompt 中始终存在，≤30 行
- [ ] L1 索引在每次 fetch 后自动刷新
- [ ] 联系人画像表存在，RAG 查询时按需补充联系人上下文
- [ ] RAG 两阶段查询工作：先摘要筛选，再全文分析
- [ ] RAG token 消耗相比单阶段降低 50%+

### 第二批
- [ ] 用户重复操作 3 次后，Telegram 提示是否生成规则
- [ ] 用户确认后自动创建 listener
- [ ] LLM 调用三级失败升级可观测（日志中可见升级过程）
- [ ] 第三次失败通过 Telegram 通知用户
- [ ] 工作记忆支持追问，30 分钟超时自动清除
- [ ] `/ask` 和直接发消息都经过 working memory

## Definition of Done

- 现有 CLI 命令和 Telegram bot 功能不受影响
- 新增文件遵循项目规范
- CLAUDE.md 更新

## Out of Scope

- 外部技能库/市场（GenericAgent 的 Sophub）
- 子代理协作（GenericAgent 的 subagent 文件协议）
- 预算模式/目标模式（GenericAgent 的 goal_mode）
- 浏览器/桌面自动化工具

## Technical Notes

### 实现顺序与依赖
```
Sub-1 (分层记忆)  ← 无依赖，新增 memory/ 子包
Sub-2 (两阶段 RAG) ← 依赖 Sub-1 的 L1 索引
Sub-5 (工作记忆)   ← 独立，改 bot.py + rag.py
Sub-4 (失败升级)   ← 独立，改 pipeline.py + agent/
Sub-3 (自进化规则) ← 依赖 listener 系统 + 新增 user_actions 表
```

### 新增数据库表
```sql
-- L1/L2 记忆存储
CREATE TABLE IF NOT EXISTS memory_layers (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- 联系人画像
CREATE TABLE IF NOT EXISTS contacts (
    email TEXT PRIMARY KEY,
    name TEXT,
    relationship TEXT,
    topics TEXT,  -- JSON array
    last_contact TEXT,
    email_count INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- 用户操作日志（自进化规则用）
CREATE TABLE IF NOT EXISTS user_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    email_pattern TEXT NOT NULL,  -- JSON: {sender_pattern, category, ...}
    created_at TEXT DEFAULT (datetime('now'))
);
```
