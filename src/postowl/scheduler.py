from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from postowl.config import Settings
from postowl.email.client import EmailClient
from postowl.llm.client import LLMClient
from postowl.models import Email, EmailAccount
from postowl.pipeline import fetch_and_process
from postowl.storage.database import Database
from postowl.storage.vectorstore import VectorStore

if TYPE_CHECKING:
    from postowl.listener.engine import ListenerEngine
    from postowl.listener.learner import RuleLearner
    from postowl.memory.index import MemoryIndex

logger = logging.getLogger(__name__)

# RFC 2177 requires clients to re-issue IDLE before 29 minutes.
# We use 28 minutes (1680s) to leave a safety margin.
_MAX_IDLE_SECONDS = 1680


class PostOwlScheduler:
    def __init__(self, settings: Settings, db: Database, llm: LLMClient,
                 vs: VectorStore,
                 notify_callback: Callable[[int, str], Awaitable[None]] | None = None,
                 listener_engine: ListenerEngine | None = None,
                 memory_index: MemoryIndex | None = None,
                 rule_learner: RuleLearner | None = None,
                 notify_suggestions: Callable[[list[dict]], None] | None = None):
        self.settings = settings
        self.db = db
        self.llm = llm
        self.vs = vs
        self.notify_callback = notify_callback
        self.listener_engine = listener_engine
        self.memory_index = memory_index
        self.rule_learner = rule_learner
        self.notify_suggestions = notify_suggestions
        self.scheduler = AsyncIOScheduler()
        self._idle_tasks: list[asyncio.Task] = []
        self._idle_fallback_active = False

    def start(self) -> None:
        if self.settings.scheduler.use_idle:
            asyncio.get_event_loop().call_soon(self._start_idle_monitors)
            self.scheduler.add_job(
                self._fallback_fetch_job,
                "interval",
                minutes=self.settings.scheduler.fetch_interval_minutes,
                id="fetch_emails_fallback",
                name="Fetch emails (fallback)",
            )
        else:
            self.scheduler.add_job(
                self._fetch_job,
                "interval",
                minutes=self.settings.scheduler.fetch_interval_minutes,
                id="fetch_emails",
                name="Fetch emails",
            )

        self.scheduler.add_job(
            self._reminder_job,
            "interval",
            seconds=self.settings.scheduler.reminder_check_interval_seconds,
            id="check_reminders",
            name="Check reminders",
        )
        self.scheduler.start()

        mode = "IDLE" if self.settings.scheduler.use_idle else "polling"
        logger.info(
            "Scheduler started (%s mode): reminders every %d sec",
            mode, self.settings.scheduler.reminder_check_interval_seconds,
        )

    def stop(self) -> None:
        for task in self._idle_tasks:
            task.cancel()
        self._idle_tasks.clear()
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    # ------------------------------------------------------------------
    # IDLE monitoring
    # ------------------------------------------------------------------

    def _start_idle_monitors(self) -> None:
        accounts = self.db.get_accounts()
        for account in accounts:
            task = asyncio.create_task(self._idle_monitor_account(account))
            self._idle_tasks.append(task)
        if accounts:
            logger.info("Started IDLE monitors for %d account(s)", len(accounts))
        self._idle_fallback_active = False

    async def _idle_monitor_account(self, account: EmailAccount) -> None:
        reconnect_interval = self.settings.scheduler.idle_reconnect_interval_seconds

        while True:
            client = EmailClient(account)
            try:
                await asyncio.to_thread(client.connect)
                await asyncio.to_thread(client.idle_start)
                logger.info("IDLE started for %s", account.email)
                self._idle_fallback_active = False

                idle_elapsed = 0
                while True:
                    responses = await asyncio.to_thread(client.idle_check, 30)
                    idle_elapsed += 30

                    has_new = any(
                        resp for resp in responses
                        if isinstance(resp, tuple) and len(resp) >= 2
                        and resp[1] in (b"EXISTS", b"RECENT")
                    )

                    if has_new:
                        await asyncio.to_thread(client.idle_done)
                        logger.info("New email detected for %s via IDLE", account.email)
                        await asyncio.to_thread(self._do_fetch_account, account)
                        await asyncio.to_thread(client.idle_start)
                        idle_elapsed = 0

                    if idle_elapsed >= _MAX_IDLE_SECONDS:
                        await asyncio.to_thread(client.idle_done)
                        await asyncio.to_thread(client.idle_start)
                        idle_elapsed = 0

            except asyncio.CancelledError:
                try:
                    client.disconnect()
                except Exception:
                    pass
                return
            except Exception as e:
                logger.warning("IDLE failed for %s: %s, falling back to polling", account.email, e)
                self._idle_fallback_active = True
                try:
                    client.disconnect()
                except Exception:
                    pass
                await asyncio.sleep(reconnect_interval)

    def _do_fetch_account(self, account: EmailAccount) -> None:
        try:
            new_emails = fetch_and_process(
                account, self.llm, self.db, self.vs,
                max_workers=self.settings.scheduler.max_workers,
                listener_engine=self.listener_engine,
                memory_index=self.memory_index,
                rule_learner=self.rule_learner,
                notify_suggestions=self.notify_suggestions,
            )
            if new_emails:
                logger.info("Processed %d emails from %s", len(new_emails), account.email)
                new_important = [e for e in new_emails if e.priority.value >= 1]
                if new_important and self.notify_callback:
                    self._notify_important(new_important)
        except Exception as e:
            logger.error("Fetch failed for %s: %s", account.email, e)

    async def _fallback_fetch_job(self) -> None:
        if not self._idle_fallback_active:
            return
        logger.info("IDLE is down, running fallback fetch...")
        await self._fetch_job()

    # ------------------------------------------------------------------
    # Polling mode (original behavior)
    # ------------------------------------------------------------------

    async def _fetch_job(self) -> None:
        logger.info("Scheduled email fetch starting...")
        try:
            await asyncio.to_thread(self._do_fetch)
        except Exception as e:
            logger.error("Scheduled fetch failed: %s", e)

    def _do_fetch(self) -> None:
        accounts = self.db.get_accounts()
        for account in accounts:
            try:
                new_emails = fetch_and_process(
                    account, self.llm, self.db, self.vs,
                    max_workers=self.settings.scheduler.max_workers,
                    listener_engine=self.listener_engine,
                    memory_index=self.memory_index,
                    rule_learner=self.rule_learner,
                    notify_suggestions=self.notify_suggestions,
                )
                if not new_emails:
                    continue
                logger.info("Fetched %d emails from %s", len(new_emails), account.email)
                new_important = [e for e in new_emails if e.priority.value >= 1]
                if new_important and self.notify_callback:
                    self._notify_important(new_important)
            except Exception as e:
                logger.error("Fetch failed for %s: %s", account.email, e)

    def _notify_important(self, emails: list[Email]) -> None:
        lines = ["*New important emails:*\n"]
        for e in emails:
            lines.append(
                f"[{e.category.value}] From: {e.sender_addr}\n"
                f"  Subject: {e.subject or '(no subject)'}\n"
                f"  {e.summary or ''}"
            )
        text = "\n".join(lines)
        if self.notify_callback:
            for uid in self.settings.telegram.allowed_user_ids:
                asyncio.create_task(self.notify_callback(uid, text))

    async def _reminder_job(self) -> None:
        try:
            reminders = self.db.get_pending_reminders()
            for r in reminders:
                text = f"*Reminder:* {r.message}"
                if r.email_id:
                    email_obj = self.db.get_email(r.email_id)
                    if email_obj:
                        text += f"\n\nRelated email:\nFrom: {email_obj.sender_addr}\nSubject: {email_obj.subject}"
                self.db.mark_reminder_sent(r.id)
                if self.notify_callback:
                    for uid in self.settings.telegram.allowed_user_ids:
                        await self.notify_callback(uid, text)
                logger.info("Sent reminder #%d: %s", r.id, r.message)
        except Exception as e:
            logger.error("Reminder check failed: %s", e)
