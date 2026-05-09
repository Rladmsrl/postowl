# Sub-0: 统一邮件处理 Pipeline

## Goal

将三处重复的邮件处理流水线（cli.py:fetch / bot.py:_do_fetch / scheduler.py:_do_fetch）统一为一个共享的 `process_emails()` pipeline 函数。

## Requirements

1. 新增 `src/postowl/pipeline.py` 模块（或放在现有模块中）
2. 提供 `process_email(email, llm, db, vs, *, on_progress=None) -> Email` 函数：
   - classify → update DB → summarize → update DB → index vectorstore
   - `on_progress` 回调：`(email, stage: str) -> None`，供调用方输出进度
   - 返回处理后的 Email 对象（带 category/priority/summary）
3. 提供 `fetch_and_process(account, llm, db, vs, *, on_progress=None, on_error=None) -> list[Email]` 函数：
   - 封装 EmailClient 连接 + fetch + 逐封 process + update last_uid
   - `on_error` 回调处理单账户错误
4. 三处调用方改为使用 pipeline 函数：
   - `cli.py:fetch()` — on_progress 用 `console.print`
   - `bot.py:_do_fetch()` — 无 on_progress（batch 完成后统一回复）
   - `scheduler.py:_do_fetch()` — on_progress 用 `logger.info`，处理完后检查通知

## Acceptance Criteria

- [ ] `cli.py:fetch()` 使用 pipeline 函数，行为不变
- [ ] `bot.py:_do_fetch()` 使用 pipeline 函数，行为不变
- [ ] `scheduler.py:_do_fetch()` 使用 pipeline 函数，行为不变（含通知逻辑）
- [ ] 三处重复的 classify→summarize→index 代码已删除
- [ ] 新 pipeline 函数有完整类型标注

## Technical Notes

- 这是后续 Sub-3（并行处理）和 Sub-5（Listener 引擎）的基础
- 通知逻辑暂时保留在 scheduler 中（Sub-5 会迁移到 listener）
- pipeline 函数应接受所有依赖作为参数，不使用全局状态
