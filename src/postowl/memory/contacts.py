from __future__ import annotations

import json
import logging

from postowl.storage.database import Database

logger = logging.getLogger(__name__)


class ContactManager:
    def __init__(self, db: Database):
        self.db = db

    def refresh_from_emails(self) -> int:
        """Rebuild contact profiles from email data. Returns number of contacts updated."""
        rows = self.db.conn.execute("""
            SELECT sender_addr,
                   GROUP_CONCAT(DISTINCT sender_name) as names,
                   COUNT(*) as email_count,
                   MAX(date) as last_contact,
                   GROUP_CONCAT(DISTINCT category) as categories
            FROM emails
            GROUP BY sender_addr
            ORDER BY email_count DESC
        """).fetchall()

        count = 0
        for r in rows:
            names = r["names"] or ""
            name = names.split(",")[0] if names else ""
            categories = (r["categories"] or "").split(",")

            self.db.conn.execute(
                "INSERT INTO contacts (email, name, topics, last_contact, email_count, updated_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT(email) DO UPDATE SET "
                "name = COALESCE(NULLIF(excluded.name, ''), contacts.name), "
                "topics = excluded.topics, last_contact = excluded.last_contact, "
                "email_count = excluded.email_count, updated_at = excluded.updated_at",
                (r["sender_addr"], name, json.dumps(categories), r["last_contact"], r["email_count"]),
            )
            count += 1

        self.db.conn.commit()
        logger.info("Refreshed %d contact profiles", count)
        return count

    def get_contacts_for_query(self, senders: list[str]) -> list[dict]:
        """Get contact profiles relevant to a list of sender addresses."""
        if not senders:
            return []
        placeholders = ",".join("?" for _ in senders)
        rows = self.db.conn.execute(
            f"SELECT * FROM contacts WHERE email IN ({placeholders})",
            senders,
        ).fetchall()
        return [
            {
                "email": r["email"],
                "name": r["name"] or "",
                "relationship": r["relationship"] or "",
                "topics": json.loads(r["topics"]) if r["topics"] else [],
                "email_count": r["email_count"],
                "last_contact": r["last_contact"] or "",
            }
            for r in rows
        ]
