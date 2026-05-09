# Logging Guidelines

> How logging is done in this project.

---

## Overview

PostOwl uses Python's built-in `logging` module. Each module creates a module-level logger via `logging.getLogger(__name__)`. The root logger is configured once in `cli.py:main()`.

---

## Setup

```python
# src/postowl/cli.py — root logger configuration
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    app()
```

Per-module logger pattern (used in every file that logs):

```python
import logging
logger = logging.getLogger(__name__)
```

---

## Log Levels

| Level | When to use | Example |
|-------|------------|---------|
| `INFO` | Normal operational events: connections, fetch counts, scheduler start | `logger.info("Connected to %s as %s", server, username)` |
| `WARNING` | Recoverable failures where a fallback is used | `logger.warning("Classification failed for email %s: %s", id, e)` |
| `ERROR` | Failures that affect a job or operation but don't crash the process | `logger.error("Scheduled fetch failed: %s", e)` |

`DEBUG` is not currently used. `CRITICAL` is not used.

---

## What to Log

- IMAP connections and email fetch counts (`email/client.py`)
- Scheduler start with interval config (`scheduler.py`)
- Lifecycle events: "PostOwl is running", "PostOwl stopped" (`app.py`)
- LLM call failures with the email message_id (`agent/classifier.py`, `agent/summarizer.py`)
- Per-account fetch results and errors (`scheduler.py`)
- Reminder delivery events (`scheduler.py`)

---

## What NOT to Log

- **Email body content** — only log metadata (message_id, sender, subject).
- **Passwords or API keys** — credentials are in keyring/config, never logged.
- **Full LLM responses** — only log first 200 chars of unparseable JSON responses.

---

## Formatting

Use `%s` string formatting (lazy evaluation), not f-strings, in log calls:

```python
# Correct
logger.info("Fetched %d emails from %s", len(emails), account.email)

# Wrong — evaluates the f-string even if log level is disabled
logger.info(f"Fetched {len(emails)} emails from {account.email}")
```

---

## CLI Output vs Logging

- **User-facing output** goes through Rich console (`console.print()`), not logging.
- **Operational/debug output** goes through `logger`. In `serve` mode (long-running), only logging is visible. In CLI commands, both Rich output and logs appear.
