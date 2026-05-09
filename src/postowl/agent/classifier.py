from __future__ import annotations

import logging

from postowl.agent.retry import retry_with_escalation
from postowl.llm.client import LLMClient
from postowl.models import ClassificationResult, Email, EmailCategory, EmailPriority

logger = logging.getLogger(__name__)

CLASSIFY_PROMPT = """You are an email classifier. Analyze the following email across multiple dimensions and classify it.

## Analysis Dimensions

1. **Sender domain**: Is this from a known organization, a person, or an automated system (noreply@, notifications@)?
2. **Subject content**: Does the subject indicate urgency, a transaction, a newsletter, or a routine notification?
3. **Email body**: What is the actual content about? Does it require action from the recipient?
4. **Transaction indicators**: Are there financial amounts, invoices, receipts, or payment references?

## Categories (pick exactly one)

- work: Work-related emails (from colleagues, clients, business)
- personal: Personal emails (friends, family)
- newsletter: Subscriptions, newsletters, mailing lists
- notification: Automated notifications (social media, services, alerts)
- promotion: Marketing, sales, promotional offers
- important: Critical emails requiring attention (financial, legal, health)
- action_required: Emails that need a response or action from the user

## Priority levels

- 0: Normal — no urgency
- 1: Important — deserves attention soon
- 2: Urgent — needs immediate attention

## Suggested actions

- archive: Low-value email, safe to archive without reading
- star: Worth flagging for later reference
- notify: Important enough to push a notification to the user
- none: No special action needed

## Important guidelines

Be discerning - not every email containing "important" is truly urgent. Newsletters and automated notifications should not be marked as high priority. Consider context and sender reputation, not just keywords.

## Response format

Respond in JSON:
{{
    "category": "<category>",
    "priority": 0|1|2,
    "suggested_action": "archive|star|notify|none",
    "confidence": 0.0-1.0,
    "requires_reply": true|false,
    "reason": "<brief reason for classification>"
}}

## Email to classify

From: {sender}
To: {recipients}
Subject: {subject}
Date: {date}
Body (first 1000 chars):
{body}
"""


def classify_email(llm: LLMClient, email: Email) -> ClassificationResult:
    recipients_str = ", ".join(email.recipients) if email.recipients else "unknown"

    def _do_classify(body_len: int = 1000) -> ClassificationResult:
        truncated_body = (email.body_text or "")[:body_len]
        prompt = CLASSIFY_PROMPT.format(
            sender=f"{email.sender_name or ''} <{email.sender_addr}>",
            recipients=recipients_str,
            subject=email.subject or "(no subject)",
            date=email.date.isoformat() if email.date else "unknown",
            body=truncated_body,
        )

        result = llm.chat_json([
            {"role": "system", "content": "You are an email classification assistant. Always respond in valid JSON."},
            {"role": "user", "content": prompt},
        ])

        confidence = result.get("confidence", 0.5)
        if not isinstance(confidence, (int, float)) or confidence < 0.0 or confidence > 1.0:
            confidence = 0.5

        suggested_action = result.get("suggested_action", "none")
        if suggested_action not in ("archive", "star", "notify", "none"):
            suggested_action = "none"

        return ClassificationResult(
            category=EmailCategory(result.get("category", "unknown")),
            priority=EmailPriority(result.get("priority", 0)),
            reason=result.get("reason", ""),
            suggested_action=suggested_action,
            confidence=confidence,
            requires_reply=bool(result.get("requires_reply", False)),
        )

    def _on_retry(attempt: int, error: Exception) -> dict:
        if attempt == 1:
            logger.info("Retrying classification with shorter body for %s", email.message_id)
            return {"body_len": 500}
        if attempt == 2:
            logger.info("Retrying classification with minimal body for %s", email.message_id)
            return {"body_len": 100}
        return {}

    try:
        return retry_with_escalation(_do_classify, on_retry=_on_retry)
    except Exception as e:
        logger.error("Classification failed after all retries for %s: %s", email.message_id, e)
        return ClassificationResult(
            category=EmailCategory.UNKNOWN,
            priority=EmailPriority.NORMAL,
            reason=f"Classification failed after retries: {e}",
        )
