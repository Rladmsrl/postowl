from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from postowl.models import Email, EmailAccount, EmailCategory, EmailPriority, ListenerConfig, Reminder

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    imap_server TEXT NOT NULL,
    imap_port INTEGER DEFAULT 993,
    username TEXT NOT NULL,
    use_ssl BOOLEAN DEFAULT 1,
    last_uid INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    message_id TEXT NOT NULL,
    uid INTEGER NOT NULL,
    subject TEXT,
    sender_name TEXT,
    sender_addr TEXT NOT NULL,
    recipients TEXT,
    date TEXT,
    body_text TEXT,
    category TEXT DEFAULT 'unknown',
    priority INTEGER DEFAULT 0,
    summary TEXT,
    is_read BOOLEAN DEFAULT 0,
    fetched_at TEXT DEFAULT (datetime('now')),
    UNIQUE(account_id, message_id)
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id INTEGER REFERENCES emails(id),
    remind_at TEXT NOT NULL,
    message TEXT NOT NULL,
    is_sent BOOLEAN DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_emails_account ON emails(account_id);
CREATE INDEX IF NOT EXISTS idx_emails_category ON emails(category);
CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(date);
CREATE INDEX IF NOT EXISTS idx_reminders_remind_at ON reminders(remind_at);

CREATE TABLE IF NOT EXISTS listeners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    enabled BOOLEAN DEFAULT 1,
    event_type TEXT DEFAULT 'email_received',
    handler_name TEXT NOT NULL,
    conditions TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS memory_layers (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contacts (
    email TEXT PRIMARY KEY,
    name TEXT,
    relationship TEXT,
    topics TEXT,
    last_contact TEXT,
    email_count INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    email_pattern TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- Accounts ---

    def add_account(self, account: EmailAccount) -> int:
        cur = self.conn.execute(
            "INSERT INTO accounts (name, email, imap_server, imap_port, username, use_ssl) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (account.name, account.email, account.imap_server, account.imap_port,
             account.username, account.use_ssl),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_accounts(self) -> list[EmailAccount]:
        rows = self.conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return [self._row_to_account(r) for r in rows]

    def get_account(self, account_id: int) -> EmailAccount | None:
        row = self.conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return self._row_to_account(row) if row else None

    def delete_account(self, account_id: int) -> None:
        self.conn.execute("DELETE FROM emails WHERE account_id = ?", (account_id,))
        self.conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        self.conn.commit()

    def update_last_uid(self, account_id: int, uid: int) -> None:
        self.conn.execute("UPDATE accounts SET last_uid = ? WHERE id = ?", (uid, account_id))
        self.conn.commit()

    # --- Emails ---

    def save_email(self, email: Email) -> int | None:
        try:
            cur = self.conn.execute(
                "INSERT INTO emails (account_id, message_id, uid, subject, sender_name, "
                "sender_addr, recipients, date, body_text, category, priority, summary, is_read) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (email.account_id, email.message_id, email.uid, email.subject,
                 email.sender_name, email.sender_addr,
                 json.dumps(email.recipients), email.date.isoformat() if email.date else None,
                 email.body_text, email.category.value, email.priority.value,
                 email.summary, email.is_read),
            )
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def update_email_classification(self, email_id: int, category: EmailCategory,
                                     priority: EmailPriority) -> None:
        self.conn.execute(
            "UPDATE emails SET category = ?, priority = ? WHERE id = ?",
            (category.value, priority.value, email_id),
        )
        self.conn.commit()

    def update_email_summary(self, email_id: int, summary: str) -> None:
        self.conn.execute("UPDATE emails SET summary = ? WHERE id = ?", (summary, email_id))
        self.conn.commit()

    def get_emails(self, *, account_id: int | None = None, category: str | None = None,
                   since: datetime | None = None, limit: int = 50) -> list[Email]:
        query = "SELECT * FROM emails WHERE 1=1"
        params: list = []
        if account_id is not None:
            query += " AND account_id = ?"
            params.append(account_id)
        if category:
            query += " AND category = ?"
            params.append(category)
        if since:
            query += " AND date >= ?"
            params.append(since.isoformat())
        query += " ORDER BY date DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_email(r) for r in rows]

    def get_email(self, email_id: int) -> Email | None:
        row = self.conn.execute("SELECT * FROM emails WHERE id = ?", (email_id,)).fetchone()
        return self._row_to_email(row) if row else None

    def search_emails(self, query: str, limit: int = 20) -> list[Email]:
        rows = self.conn.execute(
            "SELECT * FROM emails WHERE subject LIKE ? OR body_text LIKE ? OR sender_addr LIKE ? "
            "ORDER BY date DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        return [self._row_to_email(r) for r in rows]

    def get_email_stats(self, since: datetime | None = None) -> dict[str, int]:
        query = "SELECT category, COUNT(*) as cnt FROM emails"
        params: list = []
        if since:
            query += " WHERE date >= ?"
            params.append(since.isoformat())
        query += " GROUP BY category"
        rows = self.conn.execute(query, params).fetchall()
        return {row["category"]: row["cnt"] for row in rows}

    def get_unclassified_emails(self, limit: int = 50) -> list[Email]:
        rows = self.conn.execute(
            "SELECT * FROM emails WHERE category = 'unknown' ORDER BY date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_email(r) for r in rows]

    # --- Reminders ---

    def add_reminder(self, reminder: Reminder) -> int:
        cur = self.conn.execute(
            "INSERT INTO reminders (email_id, remind_at, message) VALUES (?, ?, ?)",
            (reminder.email_id, reminder.remind_at.isoformat(), reminder.message),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_pending_reminders(self) -> list[Reminder]:
        now = datetime.now().isoformat()
        rows = self.conn.execute(
            "SELECT * FROM reminders WHERE is_sent = 0 AND remind_at <= ? ORDER BY remind_at",
            (now,),
        ).fetchall()
        return [self._row_to_reminder(r) for r in rows]

    def get_all_reminders(self, include_sent: bool = False) -> list[Reminder]:
        if include_sent:
            rows = self.conn.execute("SELECT * FROM reminders ORDER BY remind_at").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM reminders WHERE is_sent = 0 ORDER BY remind_at"
            ).fetchall()
        return [self._row_to_reminder(r) for r in rows]

    def mark_reminder_sent(self, reminder_id: int) -> None:
        self.conn.execute("UPDATE reminders SET is_sent = 1 WHERE id = ?", (reminder_id,))
        self.conn.commit()

    def delete_reminder(self, reminder_id: int) -> None:
        self.conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        self.conn.commit()

    # --- Listeners ---

    def add_listener(self, listener: ListenerConfig) -> int:
        cur = self.conn.execute(
            "INSERT INTO listeners (name, description, enabled, event_type, handler_name, conditions) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (listener.name, listener.description, listener.enabled,
             listener.event_type, listener.handler_name,
             json.dumps(listener.conditions)),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_listeners(self, enabled_only: bool = False) -> list[ListenerConfig]:
        if enabled_only:
            rows = self.conn.execute(
                "SELECT * FROM listeners WHERE enabled = 1 ORDER BY id"
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM listeners ORDER BY id").fetchall()
        return [self._row_to_listener(r) for r in rows]

    def get_listener(self, listener_id: int) -> ListenerConfig | None:
        row = self.conn.execute(
            "SELECT * FROM listeners WHERE id = ?", (listener_id,)
        ).fetchone()
        return self._row_to_listener(row) if row else None

    def toggle_listener(self, listener_id: int) -> bool | None:
        """Toggle enabled state. Returns new state or None if not found."""
        listener = self.get_listener(listener_id)
        if not listener:
            return None
        new_state = not listener.enabled
        self.conn.execute(
            "UPDATE listeners SET enabled = ? WHERE id = ?",
            (new_state, listener_id),
        )
        self.conn.commit()
        return new_state

    # --- User Actions ---

    def log_user_action(self, user_id: int, action_type: str, email_pattern: dict) -> None:
        self.conn.execute(
            "INSERT INTO user_actions (user_id, action_type, email_pattern) VALUES (?, ?, ?)",
            (user_id, action_type, json.dumps(email_pattern)),
        )
        self.conn.commit()

    def get_recent_actions(self, user_id: int, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM user_actions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "action_type": r["action_type"],
                "email_pattern": json.loads(r["email_pattern"]) if r["email_pattern"] else {},
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # --- Built-in Listeners ---

    def ensure_builtin_listeners(self) -> None:
        """Insert built-in listeners if they don't exist yet."""
        existing = {listener.handler_name for listener in self.get_listeners()}
        builtins = [
            ListenerConfig(
                name="Priority Notifier",
                description="Notify when important/urgent emails arrive",
                handler_name="priority_notifier",
                conditions={"min_confidence": 0.7},
            ),
            ListenerConfig(
                name="Auto Label",
                description="Mark newsletter and promotion emails",
                handler_name="auto_label",
                conditions={"categories": ["newsletter", "promotion"]},
            ),
            ListenerConfig(
                name="Reply Reminder",
                description="Create reminders for emails requiring reply",
                enabled=False,
                handler_name="reply_reminder",
                conditions={},
            ),
        ]
        for bl in builtins:
            if bl.handler_name not in existing:
                self.add_listener(bl)

    # --- Row converters ---

    @staticmethod
    def _row_to_account(row: sqlite3.Row) -> EmailAccount:
        return EmailAccount(
            id=row["id"], name=row["name"], email=row["email"],
            imap_server=row["imap_server"], imap_port=row["imap_port"],
            username=row["username"], use_ssl=bool(row["use_ssl"]),
            last_uid=row["last_uid"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        )

    @staticmethod
    def _row_to_email(row: sqlite3.Row) -> Email:
        recipients = json.loads(row["recipients"]) if row["recipients"] else []
        return Email(
            id=row["id"], account_id=row["account_id"], message_id=row["message_id"],
            uid=row["uid"], subject=row["subject"],
            sender_name=row["sender_name"], sender_addr=row["sender_addr"],
            recipients=recipients,
            date=datetime.fromisoformat(row["date"]) if row["date"] else None,
            body_text=row["body_text"], category=EmailCategory(row["category"]),
            priority=EmailPriority(row["priority"]), summary=row["summary"],
            is_read=bool(row["is_read"]),
            fetched_at=datetime.fromisoformat(row["fetched_at"]) if row["fetched_at"] else None,
        )

    @staticmethod
    def _row_to_reminder(row: sqlite3.Row) -> Reminder:
        return Reminder(
            id=row["id"], email_id=row["email_id"],
            remind_at=datetime.fromisoformat(row["remind_at"]),
            message=row["message"], is_sent=bool(row["is_sent"]),
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        )

    @staticmethod
    def _row_to_listener(row: sqlite3.Row) -> ListenerConfig:
        return ListenerConfig(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            event_type=row["event_type"],
            enabled=bool(row["enabled"]),
            handler_name=row["handler_name"],
            conditions=json.loads(row["conditions"]) if row["conditions"] else {},
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        )
