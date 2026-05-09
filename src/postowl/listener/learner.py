from __future__ import annotations

import logging
from collections import Counter

from postowl.models import Email
from postowl.storage.database import Database

logger = logging.getLogger(__name__)

PATTERN_THRESHOLD = 3


class RuleLearner:
    """Analyzes user actions to detect repeated patterns and suggest listener rules."""

    def __init__(self, db: Database):
        self.db = db

    def log_action(self, user_id: int, action_type: str, email: Email) -> None:
        pattern = {
            "sender_domain": email.sender_addr.split("@")[-1] if email.sender_addr else "",
            "category": email.category.value,
        }
        self.db.log_user_action(user_id, action_type, pattern)

    def detect_patterns(self, user_id: int) -> list[dict]:
        """Detect repeated action patterns from recent user actions.

        Returns list of suggested rules: [{action_type, sender_domain, count, suggestion, ...}]
        """
        actions = self.db.get_recent_actions(user_id, limit=100)
        if not actions:
            return []

        pattern_counts: Counter = Counter()
        for a in actions:
            key = (a["action_type"], a["email_pattern"].get("sender_domain", ""))
            if key[1]:
                pattern_counts[key] += 1

        suggestions = []
        existing = self.db.get_listeners()
        existing_domains = {
            l.conditions.get("sender_domain")
            for l in existing
            if l.conditions.get("sender_domain")
        }

        for (action_type, sender_domain), count in pattern_counts.items():
            if count < PATTERN_THRESHOLD:
                continue
            if sender_domain in existing_domains:
                continue
            suggestion = self._build_suggestion(action_type, sender_domain, count)
            if suggestion:
                suggestions.append(suggestion)

        return suggestions

    def _build_suggestion(self, action_type: str, sender_domain: str, count: int) -> dict | None:
        if action_type == "ignore":
            return {
                "action_type": action_type,
                "sender_domain": sender_domain,
                "count": count,
                "suggestion": f"你已经 {count} 次忽略来自 {sender_domain} 的邮件，是否自动归档？",
                "handler_name": "auto_label",
                "conditions": {
                    "sender_domain": sender_domain,
                    "categories": ["newsletter", "notification", "promotion"],
                },
            }
        elif action_type == "star":
            return {
                "action_type": action_type,
                "sender_domain": sender_domain,
                "count": count,
                "suggestion": f"你已经 {count} 次标星来自 {sender_domain} 的邮件，是否自动通知？",
                "handler_name": "priority_notifier",
                "conditions": {"sender_domain": sender_domain},
            }
        return None
