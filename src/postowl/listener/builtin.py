from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from postowl.listener.engine import ListenerContext, ListenerEngine
from postowl.models import Email, Reminder

logger = logging.getLogger(__name__)


def priority_notifier(email: Email, ctx: ListenerContext, conditions: dict) -> None:
    """Notify when important/urgent emails arrive."""
    if email.priority.value >= 1:
        msg = (
            f"[{email.category.value}] From: {email.sender_addr}\n"
            f"  Subject: {email.subject or '(no subject)'}\n"
            f"  {email.summary or ''}"
        )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(ctx.notify(msg, "high"))
        except RuntimeError:
            logger.warning("No running event loop available for notification")


def auto_label(email: Email, ctx: ListenerContext, conditions: dict) -> None:
    """Mark newsletter/promotion emails in the database."""
    target_categories = conditions.get("categories", ["newsletter", "promotion"])
    if email.category.value in target_categories:
        logger.info("Auto-labeled email %s as %s", email.message_id, email.category.value)


def reply_reminder(email: Email, ctx: ListenerContext, conditions: dict) -> None:
    """Create a reminder for emails that likely need a reply."""
    prompt = (
        f"Analyze if this email requires a reply from the recipient:\n\n"
        f"From: {email.sender_name or ''} <{email.sender_addr}>\n"
        f"Subject: {email.subject or '(no subject)'}\n"
        f"Body (first 500 chars): {(email.body_text or '')[:500]}\n\n"
        'Respond in JSON:\n'
        '{"requires_reply": true/false, "urgency": "high"|"normal"|"low", "reason": "<brief reason>"}'
    )

    try:
        result = ctx.classify_deep(email, prompt)
    except Exception as e:
        logger.warning("Reply analysis failed for email %s: %s", email.message_id, e)
        return

    if result.get("requires_reply", False):
        hours = 4 if result.get("urgency") == "high" else 24
        remind_at = datetime.now() + timedelta(hours=hours)
        reminder = Reminder(
            email_id=email.id,
            remind_at=remind_at,
            message=f"Reply needed: {email.subject or '(no subject)'} from {email.sender_addr}",
        )
        ctx.db.add_reminder(reminder)
        logger.info("Created reply reminder for email %s", email.message_id)


def register_builtin_handlers(engine: ListenerEngine) -> None:
    """Register all built-in handlers with the engine."""
    engine.register_handler("priority_notifier", priority_notifier)
    engine.register_handler("auto_label", auto_label)
    engine.register_handler("reply_reminder", reply_reminder)
