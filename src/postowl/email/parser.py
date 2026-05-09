from __future__ import annotations

import email
import email.header
import email.utils
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from postowl.models import Email


def parse_email(raw_bytes: bytes, account_id: int, uid: int) -> Email | None:
    msg = email.message_from_bytes(raw_bytes)
    message_id = msg.get("Message-ID", "").strip()
    if not message_id:
        message_id = f"no-id-{uid}-{account_id}"

    subject = _decode_header(msg.get("Subject", ""))
    sender_name, sender_addr = _parse_address(msg.get("From", ""))
    recipients = _parse_recipients(msg)
    date = _parse_date(msg.get("Date"))
    body_text = _extract_body(msg)

    return Email(
        account_id=account_id,
        message_id=message_id,
        uid=uid,
        subject=subject,
        sender_name=sender_name,
        sender_addr=sender_addr or "unknown@unknown",
        recipients=recipients,
        date=date,
        body_text=body_text,
    )


def parse_email_headers(raw_bytes: bytes, account_id: int, uid: int) -> Email | None:
    msg = email.message_from_bytes(raw_bytes)
    message_id = msg.get("Message-ID", "").strip()
    if not message_id:
        message_id = f"no-id-{uid}-{account_id}"

    subject = _decode_header(msg.get("Subject", ""))
    sender_name, sender_addr = _parse_address(msg.get("From", ""))
    recipients = _parse_recipients(msg)
    date = _parse_date(msg.get("Date"))

    return Email(
        account_id=account_id,
        message_id=message_id,
        uid=uid,
        subject=subject,
        sender_name=sender_name,
        sender_addr=sender_addr or "unknown@unknown",
        recipients=recipients,
        date=date,
        body_text=None,
    )


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    decoded_parts = email.header.decode_header(value)
    parts = []
    for content, charset in decoded_parts:
        if isinstance(content, bytes):
            parts.append(content.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(content)
    return " ".join(parts).strip()


def _parse_address(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    decoded = _decode_header(value)
    name, addr = email.utils.parseaddr(decoded)
    return name or None, addr or None


def _parse_recipients(msg: email.message.Message) -> list[str]:
    recipients = []
    for header in ("To", "Cc"):
        value = msg.get(header)
        if not value:
            continue
        decoded = _decode_header(value)
        addrs = email.utils.getaddresses([decoded])
        recipients.extend(addr for _, addr in addrs if addr)
    return recipients


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _extract_body(msg: email.message.Message) -> str | None:
    text_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                text = payload.decode("utf-8", errors="replace")
            if content_type == "text/plain":
                text_parts.append(text)
            elif content_type == "text/html":
                html_parts.append(text)
    else:
        content_type = msg.get_content_type()
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                text = payload.decode("utf-8", errors="replace")
            if content_type == "text/plain":
                text_parts.append(text)
            elif content_type == "text/html":
                html_parts.append(text)

    if text_parts:
        return _clean_text("\n".join(text_parts))
    if html_parts:
        return _html_to_text("\n".join(html_parts))
    return None


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    return _clean_text(text)


def _clean_text(text: str) -> str:
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()
