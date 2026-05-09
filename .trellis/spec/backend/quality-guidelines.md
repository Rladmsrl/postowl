# Quality Guidelines

> Code quality standards for backend development.

---

## Overview

PostOwl is a small single-developer project. No linter, formatter, or type checker is configured in `pyproject.toml`. No test suite exists yet. Quality comes from consistent patterns and simplicity.

---

## Required Patterns

### Type hints everywhere

All function signatures use type hints. Use PEP 604 union syntax (`X | None` instead of `Optional[X]`), enabled by `from __future__ import annotations` at the top of every file.

```python
from __future__ import annotations

def get_email(self, email_id: int) -> Email | None:
    ...
```

### Pydantic for data models

All data models are Pydantic `BaseModel` subclasses (see `models.py`). Configuration uses Pydantic `BaseSettings` with `pydantic-settings` (see `config.py`).

### Explicit dependency wiring

Dependencies are constructed and passed explicitly. No global singletons, no DI framework, no module-level state (except loggers and constants).

### Context managers for external connections

IMAP connections use `__enter__`/`__exit__`. Database connections are managed via `try/finally` in CLI commands.

### Async bridge for CPU-bound work

In the Telegram bot, CPU-bound LLM and database work is dispatched via `asyncio.to_thread()`:

```python
result = await asyncio.to_thread(self._do_fetch)
```

---

## Forbidden Patterns

- **No ORM** — use raw SQLite queries. The project is intentionally simple.
- **No global mutable state** — no module-level service instances. Wire dependencies in `_get_services()` or `app.run()`.
- **No `print()` for user output** — use Rich `console.print()` in CLI, `update.message.reply_text()` in bot.
- **No bare `python` or `pip`** — use `uv run`, `uv sync`, `uv add`.
- **No storing passwords in config or database** — use `keyring` module.

---

## Code Style

- **Imports**: `from __future__ import annotations` first, then stdlib, then third-party, then local. Standard Python import ordering.
- **Line length**: no enforced limit, but lines are typically kept reasonable.
- **Docstrings**: not used. Functions are self-documenting via descriptive names and type hints.
- **Comments**: minimal. Prompt constants and schema constants speak for themselves.

---

## Testing Requirements

No test suite exists. When tests are added:
- Use `pytest`.
- LLM calls should be mockable via `LLMClient` interface.
- Database tests can use an in-memory SQLite (`:memory:`).

---

## Code Review Checklist

- [ ] Type hints on all function signatures
- [ ] `from __future__ import annotations` at top of file
- [ ] Pydantic models for any new data structures
- [ ] LLM calls wrapped in try/except with fallback return
- [ ] Database writes followed by `self.conn.commit()`
- [ ] No secrets logged or stored in plaintext
- [ ] Parameterized SQL queries (no string interpolation)
- [ ] Logger uses `%s` formatting, not f-strings
