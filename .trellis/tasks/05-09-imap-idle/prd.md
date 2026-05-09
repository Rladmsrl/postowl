# Sub-1: IMAP IDLE 实时推送

## Goal

使用 IMAP IDLE 命令实现实时邮件监听，替代定时轮询，大幅降低新邮件的通知延迟。

## Requirements

1. `EmailClient` 新增方法：
   - `idle_start()` — 进入 IDLE 模式
   - `idle_check(timeout=30) -> list` — 检查是否有新邮件事件
   - `idle_done()` — 退出 IDLE 模式
   - `idle_monitor(callback, *, timeout=30, max_idle_time=1680)` — 高层封装：
     - 循环 idle_check，有新邮件时调用 callback
     - 每 28 分钟（1680 秒）自动重新发起 IDLE（RFC 2177 规定 29 分钟上限）
     - 连接断开时抛出异常

2. `PostOwlScheduler` 新增 IDLE 模式：
   - 新增 `_idle_job()` 异步方法，使用 `asyncio.to_thread` 运行 IDLE 监听
   - IDLE 收到新邮件通知后，触发 fetch + process pipeline
   - IDLE 连接失败时，自动降级为 interval 轮询模式并记录日志
   - 降级后定期尝试重新建立 IDLE 连接

3. 配置：
   - `SchedulerConfig` 新增 `use_idle: bool = True`
   - `use_idle=True` 时优先使用 IDLE，失败降级
   - `use_idle=False` 时保持原有轮询行为

4. 连接管理：
   - IDLE 模式需要独立的 IMAP 连接（不能和 fetch 共用）
   - 需要处理多账户场景（每个账户一个 IDLE 连接）

## Acceptance Criteria

- [ ] IDLE 模式下新邮件到达后 <30 秒触发处理
- [ ] IDLE 连接断开后自动降级为轮询模式
- [ ] 降级后定期尝试重新建立 IDLE（每 5 分钟一次）
- [ ] 28 分钟自动重新发起 IDLE
- [ ] `use_idle=False` 时行为与现有完全一致
- [ ] 多账户场景下每个账户独立监听

## Technical Notes

- imapclient 的 IDLE API：`idle()`, `idle_check(timeout)`, `idle_done()`
- RFC 2177：客户端应每 29 分钟重新发起 IDLE
- IDLE 模式下 imapclient 连接不能用于其他操作（如 fetch），需要 idle_done 后再操作
- Demo 使用 `node-imap` 的 keepalive 选项实现类似功能
