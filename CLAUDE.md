# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

For full development guide including code conventions, extension patterns, and troubleshooting, see [CONTRIBUTING.md](CONTRIBUTING.md).

## ⚠️ 这是一个公开的开源项目

**每次 git push 之前，必须检查以下隐私项：**

1. **API Key / Token** — 不得出现在代码或配置文件中（`sk-`、`AIza`、bot token 等）。所有密钥只能在 `~/.postowl/config.yaml` 中，该文件已被 `.gitignore` 排除
2. **邮箱地址 / 密码** — 用户的真实邮箱地址、IMAP 密码、授权码不得出现在任何被追踪的文件中
3. **Telegram User ID** — 不得硬编码真实用户 ID，示例中使用 `your_user_id`
4. **个人信息** — 不得包含真实姓名关联的私人邮箱地址（项目署名邮箱 `mail@bzr.ink` 除外）

**检查命令：**
```bash
# 扫描所有追踪文件中的敏感信息
grep -rn "sk-[a-zA-Z0-9]\{10,\}\|AIza[a-zA-Z0-9]\{30,\}\|noreply\|授权码" --include="*.py" --include="*.md" --include="*.yaml" --include="*.json" .
# 检查 git diff 中是否有密钥
git diff --cached | grep -iE "api_key|password|token|secret"
```

## Project Overview

PostOwl is a smart email agent that fetches emails via IMAP, classifies and summarizes them using an OpenAI-compatible LLM, indexes them in a vector store for RAG Q&A, and exposes functionality through both a CLI (Typer) and a Telegram bot. Batch summaries and RAG responses default to Chinese (中文).

## Commands

```bash
uv sync                          # Install dependencies
uv run postowl <command>         # Run any CLI command
uv run postowl serve             # Start Telegram bot + scheduler
uv run postowl fetch             # One-shot email fetch & process
```

No test suite exists yet.

## Architecture

The processing pipeline for each email is: **IMAP fetch -> parse -> classify (LLM) -> summarize (LLM) -> index (ChromaDB) -> store metadata (SQLite) -> trigger listeners**. This pipeline is unified in `pipeline.py` and called from the CLI, Telegram bot, and scheduler. LLM calls are parallelized via `ThreadPoolExecutor`.

### Key layers

- **`cli.py`** - Typer CLI entry point. `_get_services()` wires up all dependencies. `postowl serve` delegates to `app.run()`.
- **`app.py`** - Lifecycle orchestrator for long-running mode: creates `PostOwlBot` + `PostOwlScheduler` + `ListenerEngine`, hooks them into the Telegram `Application` lifecycle.
- **`pipeline.py`** - Unified email processing pipeline. `process_email()` handles single-email classify→summarize→index→listener flow. `process_emails_batch()` parallelizes LLM calls via ThreadPoolExecutor. `fetch_and_process()` wraps IMAP fetch + batch processing.
- **`bot.py`** - `PostOwlBot` wraps the Telegram bot. CPU-bound LLM/DB work is dispatched via `asyncio.to_thread`. Auth is decorator-based (`_auth`), gated by `allowed_user_ids`. Free-text messages go straight to RAG. Includes `/listeners` and `/listener_toggle` commands.
- **`scheduler.py`** - `PostOwlScheduler` supports two modes: **IMAP IDLE** (real-time push, default) and **polling** (interval-based fallback). IDLE auto-degrades to polling on connection failure. Reminder checks run on a separate interval.
- **`listener/`** - Event-driven rule engine. `engine.py` has `ListenerEngine` (loads/executes listeners) and `ListenerContext` (provides LLM sub-agent capability). `builtin.py` has built-in handlers: `priority_notifier`, `auto_label`, `reply_reminder`. Listener configs are stored in SQLite.
- **`llm/client.py`** - `LLMClient` wraps the OpenAI SDK. `chat()` returns text, `chat_json()` parses JSON. Any OpenAI-compatible API works.
- **`storage/database.py`** - SQLite with WAL mode. Schema auto-creates on init. Four tables: `accounts`, `emails`, `reminders`, `listeners`.
- **`storage/vectorstore.py`** - ChromaDB with a custom `OpenAIEmbeddingFunction` adapter. Cosine similarity. Documents combine subject, sender, date, summary, and body (truncated to 3000 chars).
- **`agent/`** - Stateless LLM prompt functions. `classifier.py` returns category, priority, suggested_action, confidence, requires_reply. `summarizer.py` extracts summary, action_items, deadline, mentioned_amounts. `rag.py` is a class (`RAGEngine`) that queries the vector store then sends context + question to the LLM.
- **`email/`** - `client.py` uses `imapclient` for IMAP (including IDLE support), `keyring` for credential storage. Supports `headers_only` mode for fast metadata fetch. `parser.py` handles MIME decoding, charset detection, and HTML-to-text (BeautifulSoup).

### Configuration

Config loads from `~/.postowl/config.yaml`, overridden by env vars with `POSTOWL_` prefix and `__` nesting (e.g. `POSTOWL_LLM__API_KEY`). Data (SQLite DB, ChromaDB) also lives under `~/.postowl/` by default.
