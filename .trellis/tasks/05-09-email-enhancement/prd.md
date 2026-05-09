# 邮件处理增强：IDLE推送、并行处理、Listener引擎、Prompt优化

## Goal

借鉴 Anthropic 的 Claude Agent SDK email-agent demo（MIT 协议），将 PostOwl 的邮件处理能力从"定时轮询 + 硬编码通知"升级为"实时推送 + 并行处理 + 可扩展规则引擎 + 智能 AI 分析"。

## What I already know

- PostOwl 使用 `imapclient` 库，支持 IDLE 命令
- 当前 `EmailClient` 以 readonly=True 打开邮箱，只拉 `RFC822` 完整邮件
- 当前邮件处理是串行的：逐封 classify → summarize → index
- 通知逻辑硬编码在 `scheduler.py:_do_fetch()` 中（`priority.value >= 1` 就通知）
- 分类 prompt 只返回 category/priority/reason，无 suggested_action
- 摘要 prompt 无 deadline/金额提取
- Demo 项目的 listener 系统是 TypeScript 插件式架构，我们需要适配为 Python 版本

## Requirements

### Sub-1: IMAP IDLE 实时推送

- `EmailClient` 新增 `idle_monitor()` 方法，使用 `imapclient` 的 `idle()` / `idle_check()` 实现长连接监听
- 新邮件到达时触发回调，替代定时轮询
- 保持向后兼容：轮询模式作为 fallback（IDLE 连接断开时自动降级）
- IDLE 超时自动重连（imapclient IDLE 有 29 分钟 RFC 限制，需定期 idle_done + 重新 idle）
- 涉及文件：`src/postowl/email/client.py`, `src/postowl/scheduler.py`

### Sub-2: Headers-only 快速搜索

- `EmailClient.fetch_new_emails()` 新增 `headers_only: bool = False` 参数
- headers_only 模式只拉 `ENVELOPE` + `BODY[HEADER]`，不拉 body
- 用于快速获取邮件列表、数量统计等场景
- 全量 fetch 仅在需要 classify/summarize 时使用
- 涉及文件：`src/postowl/email/client.py`, `src/postowl/email/parser.py`

### Sub-3: 并行批量处理

- 邮件的 classify + summarize + index 流程改为并行（`concurrent.futures.ThreadPoolExecutor` 或 `asyncio`）
- 批量场景（一次 fetch 多封邮件）性能显著提升
- 保持数据库写入的串行性（SQLite 单线程写）
- max_workers 可配置，默认 4
- 涉及文件：`src/postowl/cli.py`, `src/postowl/bot.py`, `src/postowl/scheduler.py`

### Sub-4: Prompt 增强

#### 分类 Prompt 增强（`agent/classifier.py`）
- 返回字段增加：`suggested_action`（archive/star/notify/none）、`confidence`（0.0-1.0）、`requires_reply`（boolean）
- 加入防过度分类指令："Be discerning - not every email containing 'important' is truly urgent. Newsletters and automated notifications should not be marked as high priority."
- `ClassificationResult` 模型对应新增字段

#### 摘要 Prompt 增强（`agent/summarizer.py`）
- 返回字段增加：`deadline`（提取的截止日期，null 如果没有）、`mentioned_amounts`（提取的金额列表）
- `SummaryResult` 模型对应新增字段

#### RAG Prompt 借鉴（`agent/rag.py`）
- 参考 Demo 的假设驱动搜索策略，暂不改架构，但增强 system prompt 的指引

### Sub-5: Listener/规则引擎系统

- 新增 `src/postowl/listener/` 子包
- `ListenerConfig` 数据模型：id, name, description, enabled, event_type, conditions
- `ListenerEngine` 类：注册/加载/执行 listener
- 事件类型：`email_received`（MVP）、后续可扩展 `email_sent` / `scheduled_time`
- 内置 listener：
  - `priority_notifier`：替代现有硬编码通知逻辑，基于 `confidence > 0.7 && suggested_action == "notify"` 触发
  - `auto_archive`：newsletter 类邮件自动标记（暂不做 IMAP 写操作，只标记 DB）
- 用户可通过 Telegram bot 命令管理 listener（`/listeners` 查看、`/listener_toggle <id>` 启停）
- AI 子代理能力：listener handler 可以调用 `LLMClient` 做深度分析（结构化返回）
- Listener 配置持久化到 SQLite（新表 `listeners`）或 YAML 配置
- 涉及新文件：`src/postowl/listener/__init__.py`, `src/postowl/listener/engine.py`, `src/postowl/listener/builtin.py`
- 涉及修改：`src/postowl/scheduler.py`, `src/postowl/bot.py`, `src/postowl/models.py`, `src/postowl/storage/database.py`

## Acceptance Criteria

- [ ] IMAP IDLE 模式下新邮件到达后 <30 秒触发处理（相比原来 10 分钟轮询间隔）
- [ ] IDLE 连接断开后自动降级为轮询模式并重连
- [ ] headers_only 模式 fetch 速度比全量快 3x 以上
- [ ] 10 封邮件并行处理时间 < 串行时间的 40%
- [ ] 分类结果包含 suggested_action、confidence、requires_reply 字段
- [ ] confidence < 0.7 时不触发自动操作
- [ ] 摘要结果包含 deadline、mentioned_amounts 字段
- [ ] Listener 引擎能加载/执行内置 listener
- [ ] 通过 Telegram `/listeners` 命令可查看 listener 列表和状态
- [ ] 现有 CLI fetch 命令功能不受影响（向后兼容）

## Definition of Done

- 所有修改过的文件通过 type check（如果配置了的话）
- 现有 CLI 命令（fetch, summary, ask, serve）正常工作
- CLAUDE.md 更新以反映新架构
- 新增文件遵循项目现有代码规范（`from __future__ import annotations`、Pydantic 模型、`logger = logging.getLogger(__name__)`）

## Decisions (ADR-lite)

### Pipeline 统一方式

**Context**: 三处重复的邮件处理流水线（cli/bot/scheduler）需要统一以支持并行处理和 listener 触发。
**Decision**: 统一到 pipeline 函数 — 新增 `process_emails()` 函数封装 classify→summarize→index→listener 全流程，三处调用统一使用它，通过回调区分输出方式。
**Consequences**: 减少代码重复，新功能只需改一处。需要设计好回调接口。

### Listener 持久化

**Context**: Listener 配置需要持久化以支持用户通过 Telegram 管理。
**Decision**: SQLite 新表 — 新增 `listeners` 表，存储 id/name/enabled/event_type/conditions(JSON)。
**Consequences**: 和现有数据统一管理，bot 可直接读写。内置 listener 在首次启动时自动写入。

## Out of Scope

- IMAP 写操作（markAsRead, star, archive, addLabel）— 当前保持 readonly
- Web UI / React 前端
- Action 系统（用户触发的一键操作模板）
- 多 LLM 提供商的 AI 子代理切换
- Listener 的 YAML 文件热加载 / 文件监听
- 发送邮件功能

## Technical Notes

### 依赖关系与实现顺序

```
Sub-4 (Prompt增强)     ← 无依赖，可最先做
Sub-2 (Headers-only)   ← 无依赖，可并行
Sub-3 (并行处理)       ← 无依赖，可并行
Sub-1 (IMAP IDLE)      ← 需要在 scheduler 中集成
Sub-5 (Listener引擎)   ← 依赖 Sub-4 的新分类字段 + Sub-1 的实时触发
```

### 来源项目

- 项目路径：`/Users/zhengu/PycharmProjects/claude-agent-sdk-demos-main/email-agent/`
- 协议：MIT License
- 关键参考文件：
  - `database/imap-manager.ts` — IDLE 监听、并行 fetch、headers-only
  - `agent/custom_scripts/listeners/` — listener 架构
  - `ccsdk/listeners-manager.ts` — listener 引擎实现
  - `agent/.claude/skills/listener-creator/templates/ai-classifier.ts` — AI 分类 prompt
  - `agent/.claude/skills/listener-creator/templates/urgent-watcher.ts` — 紧急度分析 prompt

### imapclient IDLE API

```python
# imapclient 的 IDLE 支持
client.idle()                    # 进入 IDLE 模式
responses = client.idle_check(timeout=30)  # 等待事件，超时返回空列表
client.idle_done()               # 退出 IDLE 模式
# RFC 2177 要求客户端每 29 分钟重新发起 IDLE
```

### 现有代码中的处理流水线（三处重复）

```
cli.py:fetch()           — 串行处理
bot.py:_do_fetch()       — 串行处理
scheduler.py:_do_fetch() — 串行处理 + 硬编码通知
```

这三处应统一为一个共享函数，被 Sub-3 和 Sub-5 共同使用。
