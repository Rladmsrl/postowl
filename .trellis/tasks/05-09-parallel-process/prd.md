# Sub-3: 并行批量处理

## Goal

将邮件的 classify + summarize + index 流程从串行改为并行，提升多邮件场景下的处理速度。

## Requirements

1. 在 `pipeline.py` 中新增 `process_emails_batch(emails, llm, db, vs, *, max_workers=4, on_progress=None) -> list[Email]`
   - 使用 `concurrent.futures.ThreadPoolExecutor` 并行执行 classify + summarize
   - LLM 调用（CPU-bound 中的 I/O-bound）是并行的主要收益点
   - 数据库写入保持串行（SQLite 单线程写限制）— 收集结果后统一写入
   - vectorstore 的 upsert 也需串行或使用批量 API

2. `max_workers` 通过 `SchedulerConfig` 配置：
   - 新增 `max_workers: int = 4` 字段
   - CLI 和 bot 也使用此配置

3. 处理流程：
   ```
   [email1, email2, email3, ...] 
     → ThreadPoolExecutor(max_workers=4)
       → worker: classify(llm, email) + summarize(llm, email)  # 并行
     → 收集结果
     → 串行: db.update_classification + db.update_summary + vs.index_email  # 串行写
   ```

4. 错误处理：单封邮件处理失败不影响其他邮件

## Acceptance Criteria

- [ ] 10 封邮件并行处理时间 < 串行时间的 40%
- [ ] 单封邮件处理失败时，其他邮件正常完成
- [ ] max_workers 可通过 config.yaml 配置
- [ ] 数据库和 vectorstore 写入不会出现并发冲突
- [ ] 现有 CLI fetch 命令使用并行模式

## Technical Notes

- 依赖 Sub-0（统一 pipeline）完成后再实现
- `LLMClient.chat()` / `chat_json()` 是线程安全的（每次调用创建独立 HTTP 请求）
- SQLite WAL 模式支持多线程读，但写需要串行
- `VectorStore.index_emails()` 已有批量 API，可直接使用
