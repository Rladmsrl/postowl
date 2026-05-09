# Sub-2: Headers-only 快速搜索

## Goal

在 `EmailClient.fetch_new_emails()` 中新增 headers-only 模式，只拉取邮件头信息，不拉取 body，用于快速获取邮件列表和统计。

## Requirements

1. `EmailClient.fetch_new_emails()` 新增参数 `headers_only: bool = False`
2. headers_only=True 时：
   - 只 fetch `ENVELOPE` 或 `BODY[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)]`
   - 返回的 Email 对象中 `body_text = None`
   - 速度应显著快于全量 fetch
3. `parser.py` 新增 `parse_email_headers(raw_headers, account_id, uid) -> Email` 函数
   - 只解析 From/To/Subject/Date/Message-ID
   - body_text 为 None
4. 应用场景：
   - 未来的邮件列表展示
   - 快速统计新邮件数量
   - IDLE 模式下先快速获取 header，再按需拉取 body

## Acceptance Criteria

- [ ] `fetch_new_emails(headers_only=True)` 返回带 header 信息但无 body 的 Email 列表
- [ ] headers_only 模式比全量模式快（通过 fetch 数据量显著减少验证）
- [ ] 默认 `headers_only=False`，不影响现有行为
- [ ] 返回的 Email 对象可以正常存入数据库

## Technical Notes

- imapclient fetch 支持指定 data 参数：`['ENVELOPE']` 或 `['BODY[HEADER]']`
- Demo 使用 `HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES)` + `struct: true`
