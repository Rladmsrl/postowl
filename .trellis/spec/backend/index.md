# Backend Development Guidelines

> Best practices for backend development in this project.

---

## Overview

PostOwl is a Python 3.12+ application using raw SQLite, OpenAI-compatible LLM APIs, ChromaDB for vector search, Typer for CLI, and python-telegram-bot for the Telegram interface. Dependencies are managed by `uv`. All data models use Pydantic.

---

## Pre-Development Checklist

Before writing code, verify:

1. File has `from __future__ import annotations` at the top
2. New data structures are Pydantic `BaseModel` subclasses
3. LLM calls are wrapped in try/except with fallback returns
4. Database writes are followed by `self.conn.commit()`
5. SQL uses parameterized queries (`?` placeholders)
6. No secrets in logs or plaintext storage

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | Module organization and file layout | Done |
| [Database Guidelines](./database-guidelines.md) | Raw SQLite patterns, ChromaDB, naming | Done |
| [Error Handling](./error-handling.md) | Boundary-level catch with fallback returns | Done |
| [Quality Guidelines](./quality-guidelines.md) | Type hints, Pydantic, forbidden patterns | Done |
| [Logging Guidelines](./logging-guidelines.md) | stdlib logging, levels, formatting | Done |

---

## Quality Check

When reviewing code, verify:

- [ ] Type hints on all function signatures
- [ ] `from __future__ import annotations` present
- [ ] Pydantic models for new data structures
- [ ] LLM calls have try/except with fallback
- [ ] Database writes commit immediately
- [ ] No secrets logged or stored in plaintext
- [ ] Parameterized SQL queries
- [ ] Logger uses `%s` formatting, not f-strings
- [ ] External connections use context managers or try/finally

---

**Language**: All documentation should be written in **English**.
