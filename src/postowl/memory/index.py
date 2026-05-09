from __future__ import annotations

import logging

from postowl.llm.client import LLMClient
from postowl.storage.database import Database

logger = logging.getLogger(__name__)

INDEX_PROMPT = """Based on the following email statistics and recent email data, generate a concise index (<=30 lines) of the user's email world.

Include:
1. Active projects/topics the user is involved in (based on recent email subjects and threads)
2. Key contacts and their relationship to the user (boss, colleague, service provider, etc.)
3. Pending items (invoices due, reminders, action-required emails)
4. Email patterns (most frequent senders, common categories)

Statistics:
{stats}

Recent emails (last 20):
{recent_emails}

Contact frequency:
{contact_freq}

Respond in JSON:
{{
    "index": "<the concise index text, <=30 lines, in Chinese>"
}}
"""


class MemoryIndex:
    def __init__(self, db: Database, llm: LLMClient):
        self.db = db
        self.llm = llm

    def get_index(self) -> str:
        """Get the current L1 index. Returns empty string if not yet generated."""
        row = self.db.conn.execute(
            "SELECT value FROM memory_layers WHERE key = 'l1_index'"
        ).fetchone()
        return row["value"] if row else ""

    def refresh(self) -> str:
        """Regenerate the L1 index from current email data."""
        stats = self.db.get_email_stats()
        stats_str = ", ".join(f"{k}: {v}" for k, v in stats.items())

        recent = self.db.get_emails(limit=20)
        recent_str = "\n".join(
            f"- [{e.category.value}] {e.sender_addr} -> {', '.join(e.recipients) if e.recipients else '?'}: "
            f"{e.subject or '(no subject)'} ({e.date.strftime('%Y-%m-%d') if e.date else '?'})"
            f"{' | ' + e.summary[:80] if e.summary else ''}"
            for e in recent
        )

        rows = self.db.conn.execute("""
            SELECT sender_addr, COUNT(*) as cnt, MAX(date) as last_date
            FROM emails GROUP BY sender_addr ORDER BY cnt DESC LIMIT 15
        """).fetchall()
        contact_str = "\n".join(
            f"- {r['sender_addr']}: {r['cnt']} emails, last: {r['last_date'] or '?'}"
            for r in rows
        )

        prompt = INDEX_PROMPT.format(
            stats=stats_str,
            recent_emails=recent_str,
            contact_freq=contact_str,
        )

        try:
            result = self.llm.chat_json([
                {"role": "system", "content": "You are a memory index generator. Always respond in valid JSON."},
                {"role": "user", "content": prompt},
            ])
            index_text = result.get("index", "")
        except Exception as e:
            logger.warning("Failed to refresh L1 index: %s", e)
            index_text = self.get_index()
            return index_text

        self.db.conn.execute(
            "INSERT INTO memory_layers (key, value, updated_at) VALUES ('l1_index', ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (index_text,),
        )
        self.db.conn.commit()

        logger.info("L1 index refreshed (%d chars)", len(index_text))
        return index_text
