from __future__ import annotations

import logging

from postowl.agent.retry import retry_with_escalation
from postowl.llm.client import LLMClient
from postowl.models import Email, SummaryResult

logger = logging.getLogger(__name__)

SUMMARIZE_PROMPT = """用中文（Chinese）简洁地总结以下邮件。识别所有待办事项、截止日期和提及的金额。

Instructions:
- summary 必须使用中文（Chinese），即使原始邮件是英文。
- Extract any deadline or due date mentioned in the email. Use ISO 8601 format (YYYY-MM-DD) when possible, otherwise use a natural language description. Set to null if no deadline is found.
- Extract all monetary amounts mentioned (e.g. "$5,000", "¥30,000", "EUR 1,200"). Return as a list of strings preserving the original currency symbol and formatting. Return an empty list if none found.
- Determine if a reminder should be set based on deadlines or pending actions.

Respond in JSON format:
{{
    "summary": "<2-3 sentence summary>",
    "action_items": ["<action item 1>", ...],
    "should_remind": <true|false>,
    "remind_reason": "<why a reminder is needed, or null>",
    "deadline": "<ISO date or natural language description, or null>",
    "mentioned_amounts": ["<amount 1>", ...]
}}

Email:
From: {sender}
Subject: {subject}
Date: {date}
Body:
{body}
"""

BATCH_SUMMARY_PROMPT = """You are given a list of email summaries. Provide a concise overview in Chinese (中文).

Group by category and highlight:
1. Important/urgent items
2. Action items that need attention
3. General overview of what came in

Emails:
{emails}

You MUST respond in JSON format:
{{
    "important": ["<important item 1>", ...],
    "action_items": ["<action item 1>", ...],
    "overview": "<general overview paragraph>"
}}
"""


def summarize_email(llm: LLMClient, email: Email) -> SummaryResult:
    def _do_summarize(body_len: int = 3000) -> SummaryResult:
        body = (email.body_text or "")[:body_len]
        prompt = SUMMARIZE_PROMPT.format(
            sender=f"{email.sender_name or ''} <{email.sender_addr}>",
            subject=email.subject or "(no subject)",
            date=email.date.isoformat() if email.date else "unknown",
            body=body,
        )

        result = llm.chat_json([
            {"role": "system", "content": "You are an email summarization assistant. Always respond in valid JSON."},
            {"role": "user", "content": prompt},
        ])

        mentioned_amounts = result.get("mentioned_amounts", [])
        if not isinstance(mentioned_amounts, list):
            mentioned_amounts = []

        return SummaryResult(
            summary=result.get("summary", ""),
            action_items=result.get("action_items", []),
            should_remind=result.get("should_remind", False),
            remind_reason=result.get("remind_reason"),
            deadline=result.get("deadline"),
            mentioned_amounts=mentioned_amounts,
        )

    def _on_retry(attempt: int, error: Exception) -> dict:
        if attempt == 1:
            logger.info("Retrying summarization with shorter body for %s", email.message_id)
            return {"body_len": 1500}
        if attempt == 2:
            logger.info("Retrying summarization with minimal body for %s", email.message_id)
            return {"body_len": 500}
        return {}

    try:
        return retry_with_escalation(_do_summarize, on_retry=_on_retry)
    except Exception as e:
        logger.error("Summarization failed after all retries for %s: %s", email.message_id, e)
        return SummaryResult(summary=f"Summary failed after retries: {e}")


def summarize_emails(llm: LLMClient, emails: list[Email]) -> str:
    if not emails:
        return "没有邮件需要总结。"

    email_texts = []
    for e in emails:
        summary = e.summary or (e.body_text or "")[:200]
        email_texts.append(
            f"- [{e.category.value}] From: {e.sender_addr}, "
            f"Subject: {e.subject or '(no subject)'}, "
            f"Summary: {summary}"
        )

    prompt = BATCH_SUMMARY_PROMPT.format(emails="\n".join(email_texts))

    try:
        data = llm.chat_json([
            {"role": "system", "content": "You are a helpful email assistant. Always respond in valid JSON. Use Chinese (中文)."},
            {"role": "user", "content": prompt},
        ])
        return _format_batch_summary(data)
    except Exception as e:
        logger.warning("Batch summarization failed: %s", e)
        return f"总结生成失败: {e}"


def _format_batch_summary(data: dict) -> str:
    parts: list[str] = []

    important = data.get("important", [])
    if important:
        parts.append("🔴 重要事项:")
        for item in important:
            parts.append(f"  - {item}")

    action_items = data.get("action_items", [])
    if action_items:
        parts.append("\n✅ 待办事项:")
        for item in action_items:
            parts.append(f"  - {item}")

    overview = data.get("overview", "")
    if overview:
        parts.append(f"\n📬 总览:\n{overview}")

    return "\n".join(parts) if parts else "没有邮件需要总结。"
