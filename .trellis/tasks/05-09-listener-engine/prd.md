# Sub-5: Listener/规则引擎系统

## Goal

构建可扩展的事件驱动 Listener 引擎，替代硬编码的通知逻辑，支持 AI 子代理分析能力。

## Requirements

### 数据模型（`src/postowl/models.py`）

```python
class ListenerEventType(str, Enum):
    EMAIL_RECEIVED = "email_received"

class ListenerConfig(BaseModel):
    id: int | None = None
    name: str
    description: str = ""
    enabled: bool = True
    event_type: ListenerEventType = ListenerEventType.EMAIL_RECEIVED
    handler_name: str                # 内置 handler 的注册名
    conditions: dict = Field(default_factory=dict)  # handler 特定的配置参数
    created_at: datetime | None = None
```

### 数据库（`src/postowl/storage/database.py`）

新增 `listeners` 表：
```sql
CREATE TABLE IF NOT EXISTS listeners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    enabled BOOLEAN DEFAULT 1,
    event_type TEXT DEFAULT 'email_received',
    handler_name TEXT NOT NULL,
    conditions TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);
```

### Listener 引擎（`src/postowl/listener/engine.py`）

- `ListenerEngine` 类：
  - `register_handler(name, handler_fn)` — 注册内置 handler
  - `load_listeners(db)` — 从数据库加载启用的 listener
  - `check_event(event_type, data)` — 遍历匹配的 listener，执行 handler
  - handler 签名：`async def handler(email: Email, context: ListenerContext, conditions: dict) -> None`

- `ListenerContext` 类，提供：
  - `notify(message, priority)` — 发送通知（通过回调）
  - `classify_deep(email, prompt)` — 调用 LLM 做深度分析（AI 子代理能力）
  - `log(message)` — 记录日志

### 内置 Listener（`src/postowl/listener/builtin.py`）

1. **priority_notifier** — 替代现有硬编码通知：
   - 条件：`confidence > 0.7 && suggested_action == "notify"`
   - 动作：通过 Telegram 通知所有 allowed_user_ids
   - 消息格式：`[{category}] From: {sender}\n  Subject: {subject}\n  {summary}`

2. **auto_label** — 自动标记 newsletter/promotion 类邮件：
   - 条件：`category in ["newsletter", "promotion"] && confidence > 0.8`
   - 动作：在 DB 中标记（暂不做 IMAP 写操作）

3. **reply_reminder** — 需要回复的邮件提醒：
   - 条件：`requires_reply == True && confidence > 0.7`
   - 动作：自动创建 reminder

### Pipeline 集成

- `process_email()` 完成 classify + summarize 后，调用 `listener_engine.check_event("email_received", email)`
- scheduler 中的硬编码通知逻辑迁移到 `priority_notifier` listener

### Telegram Bot 命令

- `/listeners` — 查看所有 listener 的列表和启停状态
- `/listener_toggle <id>` — 切换 listener 的 enabled 状态

## Acceptance Criteria

- [ ] `ListenerEngine` 能加载和执行内置 listener
- [ ] `priority_notifier` 替代原有硬编码通知，行为一致
- [ ] `confidence < 0.7` 时不触发自动操作
- [ ] `/listeners` 命令显示 listener 列表
- [ ] `/listener_toggle` 命令能启停 listener
- [ ] listener 配置持久化到 SQLite
- [ ] 单个 listener 执行失败不影响其他 listener

## Technical Notes

- 依赖 Sub-4（Prompt 增强）提供 suggested_action/confidence/requires_reply 字段
- 依赖 Sub-0（统一 pipeline）提供 process_email 挂载点
- 参考 Demo 架构：`ccsdk/listeners-manager.ts`、`agent/custom_scripts/listeners/`
- AI 子代理能力：ListenerContext.classify_deep() 允许 listener 调用 LLM 做额外分析
- 首次启动时自动注册内置 listener 到数据库
