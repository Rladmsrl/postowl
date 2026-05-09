from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class EmailCategory(str, Enum):
    WORK = "work"
    PERSONAL = "personal"
    NEWSLETTER = "newsletter"
    NOTIFICATION = "notification"
    PROMOTION = "promotion"
    IMPORTANT = "important"
    ACTION_REQUIRED = "action_required"
    UNKNOWN = "unknown"


class EmailPriority(int, Enum):
    NORMAL = 0
    IMPORTANT = 1
    URGENT = 2


class EmailAccount(BaseModel):
    id: int | None = None
    name: str
    email: str
    imap_server: str
    imap_port: int = 993
    username: str
    use_ssl: bool = True
    last_uid: int = 0
    created_at: datetime | None = None


class Email(BaseModel):
    id: int | None = None
    account_id: int
    message_id: str
    uid: int
    subject: str | None = None
    sender_name: str | None = None
    sender_addr: str
    recipients: list[str] = Field(default_factory=list)
    date: datetime | None = None
    body_text: str | None = None
    category: EmailCategory = EmailCategory.UNKNOWN
    priority: EmailPriority = EmailPriority.NORMAL
    summary: str | None = None
    is_read: bool = False
    fetched_at: datetime | None = None


class Reminder(BaseModel):
    id: int | None = None
    email_id: int | None = None
    remind_at: datetime
    message: str
    is_sent: bool = False
    created_at: datetime | None = None


class ClassificationResult(BaseModel):
    category: EmailCategory
    priority: EmailPriority
    reason: str
    suggested_action: str = "none"
    confidence: float = 0.5
    requires_reply: bool = False


class SummaryResult(BaseModel):
    summary: str
    action_items: list[str] = Field(default_factory=list)
    should_remind: bool = False
    remind_reason: str | None = None
    deadline: str | None = None
    mentioned_amounts: list[str] = Field(default_factory=list)


class ListenerEventType(str, Enum):
    EMAIL_RECEIVED = "email_received"


class ListenerConfig(BaseModel):
    id: int | None = None
    name: str
    description: str = ""
    enabled: bool = True
    event_type: str = "email_received"
    handler_name: str
    conditions: dict = Field(default_factory=dict)
    created_at: datetime | None = None
