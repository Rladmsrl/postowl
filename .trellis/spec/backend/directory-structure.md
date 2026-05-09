# Directory Structure

> How backend code is organized in this project.

---

## Overview

PostOwl uses a single-package `src/` layout managed by Hatch, with the CLI entry point at `postowl.cli:main`. The code is organized by domain responsibility: agent logic, email handling, storage, and LLM interaction each get their own sub-package.

---

## Directory Layout

```
src/postowl/
├── __init__.py
├── cli.py              # Typer CLI entry point, wires up all dependencies via _get_services()
├── app.py              # Lifecycle orchestrator for long-running mode (Telegram bot + scheduler)
├── bot.py              # PostOwlBot: Telegram bot handlers, auth decorator
├── scheduler.py        # PostOwlScheduler: APScheduler periodic jobs
├── config.py           # Pydantic Settings + YAML config loader
├── models.py           # Pydantic data models and enums (shared across all layers)
├── agent/              # Stateless LLM prompt functions
│   ├── classifier.py   # Email classification (chat_json + structured prompt)
│   ├── summarizer.py   # Email summarization (single + batch)
│   └── rag.py          # RAGEngine class: vector search → LLM answer
├── email/              # IMAP fetch and MIME parsing
│   ├── client.py       # EmailClient (context manager), keyring credential storage
│   └── parser.py       # parse_email(): MIME decode, charset detection, HTML→text
├── llm/                # LLM abstraction
│   └── client.py       # LLMClient: wraps OpenAI SDK, chat() and chat_json()
└── storage/            # Persistence
    ├── database.py     # Database class: raw SQLite with WAL, manual row↔model conversion
    └── vectorstore.py  # VectorStore: ChromaDB with custom OpenAIEmbeddingFunction adapter
```

---

## Module Organization

- **Top-level files** (`cli.py`, `app.py`, `bot.py`, `scheduler.py`, `config.py`, `models.py`) handle application wiring, lifecycle, and shared types.
- **Sub-packages** (`agent/`, `email/`, `llm/`, `storage/`) group by infrastructure concern, not by feature.
- New LLM prompt tasks go in `agent/` as standalone functions or small classes.
- New storage backends go in `storage/`.
- New external integrations (e.g., a Slack bot) would be a new top-level file similar to `bot.py`.

### Dependency wiring

Dependencies are assembled manually in `cli.py:_get_services()` and `app.py:run()`. There is no DI framework. Each constructor takes its required collaborators as explicit arguments.

```python
# src/postowl/cli.py — the pattern for wiring
def _get_services(config_path: Path | None = None):
    settings = load_config(config_path)
    db = Database(settings.db_path)
    llm = LLMClient(settings.llm)
    vs = VectorStore(settings.chroma_path, llm.openai_client, settings.llm.embedding_model)
    rag = RAGEngine(llm, db, vs)
    return settings, db, llm, vs, rag
```

---

## Naming Conventions

- **Files**: `snake_case.py`, short descriptive names.
- **Classes**: `PascalCase` — `PostOwlBot`, `LLMClient`, `RAGEngine`, `VectorStore`.
- **Functions**: `snake_case` — `classify_email`, `summarize_emails`, `parse_email`.
- **Private helpers**: prefix with `_` — `_get_services`, `_decode_header`, `_do_fetch`.
- **Constants**: `UPPER_SNAKE_CASE` — `SCHEMA`, `KEYRING_SERVICE`, `CLASSIFY_PROMPT`.
- **All files** use `from __future__ import annotations` for PEP 604 union syntax (`X | None`).

---

## Examples

- Well-organized agent module: `src/postowl/agent/classifier.py` — prompt constant, single public function, fallback return.
- Good context manager pattern: `src/postowl/email/client.py` — `EmailClient.__enter__`/`__exit__` for IMAP connections.
- Service wiring: `src/postowl/cli.py:_get_services()` and `src/postowl/app.py:run()`.
