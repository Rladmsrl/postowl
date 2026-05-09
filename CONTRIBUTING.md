# PostOwl 🦉 开发指南 / Contributing Guide

本文档面向 AI Agent 和开发者，提供构建、配置、运行和扩展 PostOwl 的完整指引。

---

## 环境搭建

### 前置要求

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) 包管理器
- 系统密钥链（macOS Keychain / Linux Secret Service）— 用于存储邮箱密码

### 安装依赖

```bash
git clone https://github.com/Rladmsrl/postowl.git
cd postowl
uv sync
```

### 验证安装

```bash
uv run python -c "from postowl.app import run; print('OK')"
```

---

## 项目结构

```
src/postowl/
├── cli.py              # Typer CLI 入口，_get_services() 组装所有依赖
├── app.py              # 生命周期编排，组装 Bot + Scheduler + Listener + Memory
├── bot.py              # Telegram Bot 命令处理、工作记忆、提醒按钮
├── pipeline.py         # 统一邮件处理流水线（并行 LLM + 串行写入 + listener 触发）
├── scheduler.py        # IMAP IDLE 实时推送 + 轮询降级 + 提醒检查
├── config.py           # Pydantic Settings，支持 YAML + 环境变量
├── models.py           # 所有 Pydantic 数据模型和枚举
├── agent/
│   ├── classifier.py   # 邮件分类（含三级重试升级）
│   ├── summarizer.py   # 邮件摘要 + 批量汇总（含三级重试）
│   ├── rag.py          # 两阶段 RAG 引擎（摘要筛选 → 全文分析）
│   └── retry.py        # 通用重试升级策略
├── email/
│   ├── client.py       # IMAP 客户端（IDLE、headers-only、context manager）
│   └── parser.py       # MIME 解析、charset 检测、HTML→纯文本
├── llm/
│   └── client.py       # OpenAI 兼容 LLM 客户端（chat / chat_json）
├── storage/
│   ├── database.py     # SQLite（WAL 模式，7 张表，check_same_thread=False）
│   └── vectorstore.py  # ChromaDB 向量存储 + 自定义 Embedding 适配器
├── memory/
│   ├── index.py        # L1 记忆索引（LLM 生成的用户邮件世界摘要）
│   ├── contacts.py     # L2 联系人画像（从邮件数据自动提取）
│   └── working.py      # 工作记忆（per-user 对话上下文，30 分钟 TTL）
└── listener/
    ├── engine.py       # 事件驱动规则引擎 + ListenerContext
    ├── builtin.py      # 内置 handler：priority_notifier, auto_label, reply_reminder
    └── learner.py      # 自进化规则学习器（检测用户操作模式）
```

---

## 配置

### 配置文件

`~/.postowl/config.yaml`：

```yaml
llm:                                    # 聊天/分类/摘要用的 LLM
  base_url: "https://api.deepseek.com"
  api_key: "sk-xxx"
  chat_model: "deepseek-chat"
  temperature: 0.3
  max_tokens: 2048

embedding:                              # 向量化用的 API（可与 LLM 不同提供商）
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  api_key: "sk-xxx"
  model: "text-embedding-v4"

telegram:
  bot_token: "123456:ABC-DEF"
  allowed_user_ids: [your_user_id]      # 空列表=允许所有人

scheduler:
  fetch_interval_minutes: 10            # 轮询间隔（IDLE 模式下作为降级 fallback）
  reminder_check_interval_seconds: 60   # 提醒检查间隔
  max_workers: 60                       # LLM 并行度
  use_idle: true                        # IMAP IDLE 实时推送
  idle_reconnect_interval_seconds: 300  # IDLE 断连后重试间隔
```

### 环境变量覆盖

前缀 `POSTOWL_`，嵌套用 `__` 分隔：

```bash
export POSTOWL_LLM__API_KEY=sk-xxx
export POSTOWL_EMBEDDING__API_KEY=sk-xxx
export POSTOWL_TELEGRAM__BOT_TOKEN=xxx
```

### 邮箱账户

```bash
uv run postowl accounts add
# 密码存入系统密钥链，不写入配置文件或数据库
```

---

## 核心流程

### 邮件处理流水线 (`pipeline.py`)

```
fetch_and_process(account, llm, db, vs, ...)
  ├─ EmailClient(account).fetch_new_emails()
  ├─ db.save_email() 逐封存入 SQLite（UNIQUE 去重）
  ├─ process_emails_batch() 并行处理：
  │   └─ _process_one(email):
  │       ├─ classify_email(llm, email)   # 含 3 级重试（body 1000→500→100）
  │       ├─ summarize_email(llm, email)  # 含 3 级重试（body 3000→1500→500）
  │       ├─ db.update_classification()   # 加锁串行写入
  │       ├─ db.update_summary()
  │       ├─ vs.index_email()             # ChromaDB 向量索引
  │       └─ listener_engine.check_event()
  ├─ db.update_last_uid()                 # UID 水位线
  ├─ memory_index.refresh()               # L1 索引刷新
  └─ rule_learner 检查操作模式 → 通知建议
```

### 两阶段 RAG (`agent/rag.py`)

```
query(question)
  ├─ vectorstore.query(top-20)
  ├─ Phase 1: 只传 {sender, subject, date, category} 列表给 LLM
  │   └─ LLM 返回 {"relevant": [1,3,7]}
  ├─ Phase 2: 只拉取相关邮件全文
  │   ├─ 注入 L1 记忆索引（system prompt）
  │   ├─ 注入工作记忆（user prompt）
  │   └─ LLM 返回 {"answer", "sources", "reminder"}
  └─ 格式化为纯文本 + 来源列表 + 提醒按钮
```

### IMAP IDLE 模式 (`scheduler.py`)

```
_idle_monitor_account(account):
  while True:
    ├─ connect → idle_start
    ├─ loop:
    │   ├─ idle_check(timeout=30)
    │   ├─ 检测 EXISTS/RECENT → 有新邮件 → idle_done → fetch_and_process → idle_start
    │   └─ 28 分钟自动重新 IDLE（RFC 2177）
    └─ 异常 → 降级轮询，5 分钟后重试
```

---

## 数据库 Schema（7 张表）

| 表 | 用途 | 关键字段 |
|---|------|---------|
| `accounts` | 邮箱账户 | email, imap_server, last_uid |
| `emails` | 邮件元数据 + 正文 | message_id, category, priority, summary |
| `reminders` | 提醒 | remind_at, message, is_sent |
| `listeners` | 规则配置 | handler_name, conditions(JSON), enabled |
| `memory_layers` | 记忆层（key-value） | key='l1_index', value=索引文本 |
| `contacts` | 联系人画像 | email, name, topics(JSON), email_count |
| `user_actions` | 用户操作日志 | action_type, email_pattern(JSON) |

---

## 代码规范

### 必须遵循

- 每个文件顶部 `from __future__ import annotations`
- 所有函数签名有类型标注
- 数据模型用 Pydantic `BaseModel`
- SQL 用 `?` 参数化查询，写入后 `self.conn.commit()`
- LLM 调用用 `try/except` + fallback 默认值
- Logger 用 `%s` 格式化，不用 f-string
- 外部连接用 context manager 或 `try/finally`

### 命名规范

- 文件：`snake_case.py`
- 类：`PascalCase` — `PostOwlBot`, `LLMClient`, `RAGEngine`
- 函数：`snake_case` — `classify_email`, `fetch_and_process`
- 私有方法：`_` 前缀 — `_do_fetch`, `_process_one`
- 常量：`UPPER_SNAKE_CASE` — `SCHEMA`, `CLASSIFY_PROMPT`

### 新增模块检查清单

- [ ] `from __future__ import annotations`
- [ ] `import logging` + `logger = logging.getLogger(__name__)`
- [ ] 类型标注完整
- [ ] LLM 调用有 fallback
- [ ] DB 写入有 commit
- [ ] 无循环导入（用 `TYPE_CHECKING` 防护）

---

## 扩展指南

### 添加新的 LLM Agent 函数

在 `agent/` 下新建文件，遵循现有模式：

```python
from __future__ import annotations
import logging
from postowl.agent.retry import retry_with_escalation
from postowl.llm.client import LLMClient

logger = logging.getLogger(__name__)

PROMPT = """..."""

def my_agent_fn(llm: LLMClient, ...) -> MyResult:
    def _do_call(body_len: int = 1000) -> MyResult:
        result = llm.chat_json([...])
        return MyResult(...)

    def _on_retry(attempt: int, error: Exception) -> dict:
        if attempt == 1: return {"body_len": 500}
        if attempt == 2: return {"body_len": 100}
        return {}

    try:
        return retry_with_escalation(_do_call, on_retry=_on_retry)
    except Exception as e:
        logger.error("Failed: %s", e)
        return MyResult(...)  # fallback
```

### 添加新的 Listener Handler

在 `listener/builtin.py` 中注册：

```python
def my_handler(email: Email, ctx: ListenerContext, conditions: dict) -> None:
    if not some_condition(email):
        return
    # 调用 LLM 做深度分析
    result = ctx.classify_deep(email, "分析这封邮件...")
    # 发送通知
    import asyncio
    loop = asyncio.get_running_loop()
    loop.create_task(ctx.notify("检测到重要内容", "high"))

# 在 register_builtin_handlers 中注册
engine.register_handler("my_handler", my_handler)
```

然后在数据库中添加配置：

```python
db.add_listener(ListenerConfig(
    name="My Handler",
    description="...",
    handler_name="my_handler",
    conditions={...},
))
```

### 添加新的 Telegram 命令

在 `bot.py` 的 `build_app()` handlers 列表中添加，然后实现 handler 方法：

```python
# build_app() 中
handlers = [
    ...
    ("mycommand", self._cmd_mycommand),
]

# handler 方法
async def _cmd_mycommand(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await self._react(update.message, "👀")
    result = await asyncio.to_thread(self._do_something)
    await self._reply(update.message, result)
```

在 `app.py` 的 `post_init` 中注册到 Telegram 菜单：

```python
BotCommand("mycommand", "命令说明"),
```

### 添加新的数据库表

在 `storage/database.py` 的 `SCHEMA` 常量中追加 `CREATE TABLE IF NOT EXISTS ...`，重启后自动创建。

---

## 运行命令速查

```bash
uv sync                              # 安装依赖
uv run postowl init                  # 交互式配置
uv run postowl accounts add          # 添加邮箱
uv run postowl accounts list         # 查看邮箱
uv run postowl fetch                 # 拉取邮件
uv run postowl fetch --limit 500     # 限制拉取数量
uv run postowl summary               # 邮件摘要
uv run postowl search "关键词"        # 搜索
uv run postowl ask "问题"             # RAG 问答
uv run postowl remind "时间" "内容"   # 设置提醒
uv run postowl config                # 查看配置
uv run postowl serve                 # 启动 Bot + IDLE 监听
```

---

## 故障排查

| 症状 | 原因 | 解决 |
|------|------|------|
| `No password found` | 密钥链中没有密码 | `uv run postowl accounts add` 重新添加 |
| `IDLE failed, falling back` | IMAP 连接断开 | 自动降级轮询，5 分钟后自动重试 |
| `Classification failed after retries` | LLM API 不可用 | 检查 API key 和网络，邮件标记为 UNKNOWN |
| `Can't parse entities` | Telegram Markdown 错误 | 已自动降级纯文本，无需处理 |
| `Polling crashed` | Telegram 网络波动 | 自动重试（5s→10s→...→30s backoff） |
| embedding batch 报错 | 百炼 batch size 限制 | 已固定为 10，无需处理 |
