# Error Handling

> How errors are handled in this project.

---

## Overview

PostOwl uses a simple, pragmatic error handling approach: no custom exception hierarchy. Errors are caught at boundaries (CLI commands, bot handlers, scheduler jobs, LLM calls) and either logged + returned as fallback values or surfaced to the user.

---

## Error Types

The project uses Python built-in exceptions only:
- `ValueError` — missing credentials (`email/client.py`)
- `RuntimeError` — calling methods before connection (`email/client.py`)
- `sqlite3.IntegrityError` — duplicate email insert (caught and returns `None`)
- `json.JSONDecodeError` — malformed LLM JSON response (caught in `llm/client.py`)

---

## Error Handling Patterns

### 1. LLM calls: catch-all with fallback return

Agent functions (`classifier.py`, `summarizer.py`, `rag.py`) wrap LLM calls in `try/except Exception` and return a safe default when the call fails:

```python
# src/postowl/agent/classifier.py
try:
    result = llm.chat_json([...])
    return ClassificationResult(...)
except Exception as e:
    logger.warning("Classification failed for email %s: %s", email.message_id, e)
    return ClassificationResult(
        category=EmailCategory.UNKNOWN,
        priority=EmailPriority.NORMAL,
        reason=f"Classification failed: {e}",
    )
```

### 2. Per-account error isolation in fetch loops

When processing multiple accounts, errors in one account don't stop others:

```python
# src/postowl/cli.py — fetch command
for account in accounts:
    try:
        with EmailClient(account) as client:
            ...
    except Exception as e:
        console.print(f"  [red]Error: {e}[/red]")
```

### 3. Scheduler jobs: log and continue

Scheduler errors are caught at the job level so the scheduler keeps running:

```python
# src/postowl/scheduler.py
async def _fetch_job(self) -> None:
    try:
        await asyncio.to_thread(self._do_fetch)
    except Exception as e:
        logger.error("Scheduled fetch failed: %s", e)
```

### 4. Cleanup in finally blocks

Database connections are closed in `finally` blocks in CLI commands:

```python
# src/postowl/cli.py
db = Database(settings.db_path)
try:
    ...
finally:
    db.close()
```

### 5. Silent cleanup on disconnect

IMAP logout errors are silently swallowed since we're tearing down anyway:

```python
# src/postowl/email/client.py
def disconnect(self) -> None:
    if self._client:
        try:
            self._client.logout()
        except Exception:
            pass
```

---

## User-Facing Error Responses

- **CLI**: uses Rich console with `[red]Error: ...[/red]` formatting, or `typer.Exit(1)`.
- **Telegram bot**: replies with plain text error messages (`"Could not parse time."`, `"No email accounts configured."`).
- **RAG**: returns Chinese error messages (`"查询失败: {e}"`, `"没有找到相关的邮件内容..."`).
- **Batch summary**: returns Chinese error (`"总结生成失败: {e}"`).

---

## Common Mistakes

- **Don't raise custom exceptions** — keep it simple with built-in types.
- **Don't let LLM failures crash the pipeline** — always provide a fallback return value.
- **Don't swallow errors silently** except during teardown — always log with `logger.warning` or `logger.error`.
