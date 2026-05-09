from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from postowl.llm.client import LLMClient
from postowl.models import Email, ListenerConfig
from postowl.storage.database import Database

logger = logging.getLogger(__name__)


class ListenerContext:
    """Context passed to listener handlers, providing capabilities."""

    def __init__(self, llm: LLMClient, db: Database,
                 notify_fn: Callable[[str, str], Awaitable[None]] | None = None):
        self.llm = llm
        self.db = db
        self._notify_fn = notify_fn

    async def notify(self, message: str, priority: str = "normal") -> None:
        if self._notify_fn:
            await self._notify_fn(message, priority)

    def classify_deep(self, email: Email, prompt: str) -> dict:
        """Call LLM for deep analysis on an email. Returns parsed JSON."""
        messages = [
            {"role": "system", "content": "You are an email analysis assistant. Always respond in valid JSON."},
            {"role": "user", "content": prompt},
        ]
        try:
            return self.llm.chat_json(messages)
        except Exception as e:
            logger.warning("classify_deep failed for email %s: %s", email.message_id, e)
            return {}


HandlerFn = Callable[[Email, ListenerContext, dict], None]


class ListenerEngine:
    def __init__(self, db: Database, llm: LLMClient,
                 notify_fn: Callable[[str, str], Awaitable[None]] | None = None):
        self.db = db
        self.llm = llm
        self._handlers: dict[str, HandlerFn] = {}
        self._listeners: list[ListenerConfig] = []
        self._notify_fn = notify_fn

    def register_handler(self, name: str, handler: HandlerFn) -> None:
        self._handlers[name] = handler

    def load_listeners(self) -> None:
        self._listeners = self.db.get_listeners(enabled_only=True)
        logger.info("Loaded %d enabled listener(s)", len(self._listeners))

    def check_event(self, event_type: str, email: Email) -> None:
        ctx = ListenerContext(self.llm, self.db, self._notify_fn)
        for listener in self._listeners:
            if listener.event_type != event_type:
                continue
            handler = self._handlers.get(listener.handler_name)
            if not handler:
                logger.warning("No handler registered for '%s'", listener.handler_name)
                continue
            try:
                handler(email, ctx, listener.conditions)
            except Exception as e:
                logger.error("Listener '%s' failed: %s", listener.name, e)
