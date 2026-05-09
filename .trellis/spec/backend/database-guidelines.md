# Database Guidelines

> Database patterns and conventions for this project.

---

## Overview

PostOwl uses **raw SQLite** via Python's `sqlite3` module — no ORM. The `Database` class in `src/postowl/storage/database.py` owns all SQL. WAL mode and foreign keys are enabled on connection. Schema auto-creates on init via `CREATE TABLE IF NOT EXISTS`.

ChromaDB handles vector storage separately in `src/postowl/storage/vectorstore.py`.

---

## Query Patterns

### Direct SQL with parameterized queries

All queries use `?` placeholders. Never interpolate values into SQL strings.

```python
# src/postowl/storage/database.py — parameterized query
rows = self.conn.execute(
    "SELECT * FROM emails WHERE subject LIKE ? OR body_text LIKE ? OR sender_addr LIKE ? "
    "ORDER BY date DESC LIMIT ?",
    (f"%{query}%", f"%{query}%", f"%{query}%", limit),
).fetchall()
```

### Row conversion pattern

`sqlite3.Row` is used as `row_factory`. Static `_row_to_*` methods convert rows to Pydantic models:

```python
# src/postowl/storage/database.py — row converter pattern
@staticmethod
def _row_to_account(row: sqlite3.Row) -> EmailAccount:
    return EmailAccount(
        id=row["id"], name=row["name"], email=row["email"],
        imap_server=row["imap_server"], ...
    )
```

### Commit-per-operation

Each write method calls `self.conn.commit()` immediately after the operation. There are no multi-statement transactions.

### Dynamic query building

Conditional filters use string concatenation with `WHERE 1=1` as the base:

```python
# src/postowl/storage/database.py:get_emails()
query = "SELECT * FROM emails WHERE 1=1"
params: list = []
if account_id is not None:
    query += " AND account_id = ?"
    params.append(account_id)
```

---

## Migrations

There is no migration system. Schema is defined as a single `SCHEMA` constant with `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`. Schema changes require manual SQL `ALTER TABLE` statements or recreating the database.

---

## Naming Conventions

- **Tables**: plural snake_case — `accounts`, `emails`, `reminders`.
- **Columns**: snake_case — `account_id`, `message_id`, `last_uid`, `is_read`, `is_sent`.
- **Indexes**: `idx_{table}_{column}` — `idx_emails_account`, `idx_emails_date`.
- **Boolean columns**: `is_` prefix, stored as `BOOLEAN DEFAULT 0`.
- **Timestamps**: stored as ISO 8601 text — `datetime('now')` default, parsed via `datetime.fromisoformat()`.
- **Foreign keys**: `{referenced_table_singular}_id` — `account_id`, `email_id`.

---

## ChromaDB (Vector Store)

- Single collection named `"emails"` with cosine similarity (`hnsw:space: cosine`).
- Documents combine subject, sender, date, summary, and body (truncated to 3000 chars).
- Metadata includes `email_id`, `account_id`, `sender`, `subject`, `category`, `date`.
- Uses `upsert` for idempotent indexing, with batch size 100.
- Custom `OpenAIEmbeddingFunction` adapter wraps the OpenAI SDK's embeddings API.

---

## Common Mistakes

- **Don't use an ORM** — the project deliberately uses raw SQLite. Keep it that way.
- **Don't forget `self.conn.commit()`** after writes — autocommit is off.
- **Don't store sensitive data in SQLite** — passwords go in system keyring via `keyring` module, not the database.
- **Duplicate insert handling**: `save_email()` catches `sqlite3.IntegrityError` and returns `None` for duplicates (keyed on `account_id, message_id` UNIQUE constraint).
