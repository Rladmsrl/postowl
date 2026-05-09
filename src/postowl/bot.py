from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from functools import wraps

from dateutil import parser as dateparser
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from postowl.agent.rag import RAGEngine
from postowl.memory.working import WorkingMemory
from postowl.agent.summarizer import summarize_emails
from postowl.config import Settings
from postowl.llm.client import LLMClient
from postowl.models import ListenerConfig, Reminder
from postowl.pipeline import fetch_and_process
from postowl.storage.database import Database
from postowl.storage.vectorstore import VectorStore

logger = logging.getLogger(__name__)


class PostOwlBot:
    def __init__(self, settings: Settings, db: Database, llm: LLMClient,
                 vs: VectorStore, rag: RAGEngine):
        self.settings = settings
        self.db = db
        self.llm = llm
        self.vs = vs
        self.rag = rag
        self._app: Application | None = None
        self._working_memory: dict[int, WorkingMemory] = {}

    def _auth(self, handler):
        @wraps(handler)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user_id = update.effective_user.id if update.effective_user else None
            allowed = self.settings.telegram.allowed_user_ids
            if allowed and user_id not in allowed:
                await update.message.reply_text("Unauthorized.")
                return
            return await handler(update, context)
        return wrapper

    def _get_working_memory(self, user_id: int) -> WorkingMemory:
        wm = self._working_memory.get(user_id)
        if wm is None or wm.is_expired():
            wm = WorkingMemory()
            self._working_memory[user_id] = wm
        return wm

    def build_app(self) -> Application:
        self._app = Application.builder().token(self.settings.telegram.bot_token).build()
        handlers = [
            ("start", self._cmd_start),
            ("fetch", self._cmd_fetch),
            ("today", self._cmd_today),
            ("week", self._cmd_week),
            ("categories", self._cmd_categories),
            ("search", self._cmd_search),
            ("ask", self._cmd_ask),
            ("remind", self._cmd_remind),
            ("reminders", self._cmd_reminders),
            ("accounts", self._cmd_accounts),
            ("listeners", self._cmd_listeners),
            ("listener_toggle", self._cmd_listener_toggle),
            ("create_rule", self._cmd_create_rule),
            ("help", self._cmd_help),
        ]
        for name, handler in handlers:
            self._app.add_handler(CommandHandler(name, self._auth(handler)))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._auth(self._handle_message)))
        self._app.add_handler(CallbackQueryHandler(self._handle_reminder_callback, pattern=r"^remind:"))
        return self._app

    async def send_notification(self, chat_id: int, text: str) -> None:
        if self._app:
            try:
                await self._app.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
            except Exception:
                await self._app.bot.send_message(chat_id=chat_id, text=text)

    @staticmethod
    async def _reply(message, text: str) -> None:
        try:
            await message.reply_text(text, parse_mode="Markdown")
        except Exception:
            await message.reply_text(text)

    @staticmethod
    async def _reply_with_keyboard(message, text: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
        try:
            await message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            await message.reply_text(text, reply_markup=keyboard)

    @staticmethod
    async def _react(message, emoji: str = "✉️") -> None:
        try:
            await message.set_reaction([ReactionTypeEmoji(emoji=emoji)])
        except Exception:
            pass

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        await update.message.reply_text(
            f"Hi {user.first_name}! I'm PostOwl, your email assistant.\n\n"
            f"Your user ID: `{user.id}`\n\n"
            "Commands:\n"
            "/fetch - Fetch new emails\n"
            "/today - Today's summary\n"
            "/week - This week's summary\n"
            "/categories - Category stats\n"
            "/search <keyword> - Search emails\n"
            "/ask <question> - Ask about emails\n"
            "/remind <time> <message> - Set reminder\n"
            "/reminders - View reminders\n"
            "/accounts - View accounts\n"
            "/listeners - View email listeners\n"
            "/listener\\_toggle <id> - Toggle listener\n"
            "/help - Show help",
            parse_mode="Markdown",
        )

    async def _cmd_fetch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("Fetching emails...")
        result = await asyncio.to_thread(self._do_fetch)
        await self._reply(update.message, result)

    def _do_fetch(self) -> str:
        accounts = self.db.get_accounts()
        if not accounts:
            return "No email accounts configured."

        total = 0
        messages: list[str] = []
        for account in accounts:
            try:
                new_emails = fetch_and_process(
                    account, self.llm, self.db, self.vs,
                    max_workers=self.settings.scheduler.max_workers,
                    memory_index=self.rag.memory_index,
                )
                if not new_emails:
                    messages.append(f"*{account.name}*: No new emails")
                else:
                    total += len(new_emails)
                    messages.append(f"*{account.name}*: {len(new_emails)} new")
            except Exception as e:
                messages.append(f"*{account.name}*: Error - {e}")

        return f"Fetched {total} new emails.\n" + "\n".join(messages)

    async def _cmd_today(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("Generating today's summary...")
        since = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        result = await asyncio.to_thread(self._do_summary, since)
        await self._reply(update.message, result)

    async def _cmd_week(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("Generating weekly summary...")
        since = datetime.now() - timedelta(days=7)
        result = await asyncio.to_thread(self._do_summary, since)
        await self._reply(update.message, result)

    def _do_summary(self, since: datetime) -> str:
        emails = self.db.get_emails(since=since)
        if not emails:
            return "No emails found for this period."
        return summarize_emails(self.llm, emails)

    async def _cmd_categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        stats = self.db.get_email_stats()
        if not stats:
            await update.message.reply_text("No emails in database.")
            return
        lines = ["*Email Categories:*\n"]
        for cat, count in sorted(stats.items(), key=lambda x: -x[1]):
            lines.append(f"  {cat}: {count}")
        await self._reply(update.message, "\n".join(lines))

    async def _cmd_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = " ".join(context.args) if context.args else ""
        if not query:
            await update.message.reply_text("Usage: /search <keyword>")
            return
        results = self.db.search_emails(query, limit=10)
        if not results:
            await update.message.reply_text("No emails found.")
            return
        lines = [f"*Search: '{query}'*\n"]
        for e in results:
            date_str = e.date.strftime("%m-%d") if e.date else "?"
            lines.append(f"[{date_str}] {e.sender_addr}\n  {e.subject or '(no subject)'}")
        await self._reply(update.message, "\n".join(lines))

    async def _cmd_ask(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        question = " ".join(context.args) if context.args else ""
        if not question:
            await update.message.reply_text("Usage: /ask <question>")
            return
        await self._react(update.message, "👀")
        user_id = update.effective_user.id
        wm = self._get_working_memory(user_id)
        answer, reminder = await asyncio.to_thread(
            self.rag.query, question, working_context=wm.get_context_str(),
        )
        wm.add_exchange(question, answer)
        keyboard = self._build_reminder_keyboard(reminder) if reminder else None
        await self._reply_with_keyboard(update.message, answer, keyboard)

    async def _cmd_remind(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "Usage: /remind <time> <message>\n"
                "Example: /remind 2024-12-25T09:00 Check holiday emails"
            )
            return
        time_str = context.args[0]
        message = " ".join(context.args[1:])
        try:
            remind_at = dateparser.parse(time_str)
            if not remind_at:
                await update.message.reply_text("Could not parse time.")
                return
        except Exception:
            await update.message.reply_text("Invalid time format.")
            return

        r = Reminder(remind_at=remind_at, message=message)
        rid = self.db.add_reminder(r)
        await update.message.reply_text(
            f"Reminder #{rid} set for {remind_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"Message: {message}"
        )

    async def _cmd_reminders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        items = self.db.get_all_reminders(include_sent=False)
        if not items:
            await update.message.reply_text("No pending reminders.")
            return
        lines = ["*Pending Reminders:*\n"]
        for r in items:
            lines.append(f"#{r.id} [{r.remind_at.strftime('%m-%d %H:%M')}] {r.message}")
        await self._reply(update.message, "\n".join(lines))

    async def _cmd_accounts(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        accounts = self.db.get_accounts()
        if not accounts:
            await update.message.reply_text("No accounts configured. Use CLI to add.")
            return
        lines = ["*Email Accounts:*\n"]
        for a in accounts:
            lines.append(f"#{a.id} *{a.name}* - {a.email} ({a.imap_server})")
        await self._reply(update.message, "\n".join(lines))

    async def _cmd_listeners(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        listeners = self.db.get_listeners()
        if not listeners:
            await update.message.reply_text("No listeners configured.")
            return
        lines = ["*Email Listeners:*\n"]
        for listener in listeners:
            status = "ON" if listener.enabled else "OFF"
            lines.append(f"#{listener.id} [{status}] *{listener.name}*\n  {listener.description}")
        lines.append("\nToggle: /listener\\_toggle <id>")
        await self._reply(update.message, "\n".join(lines))

    async def _cmd_listener_toggle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.message.reply_text("Usage: /listener_toggle <id>")
            return
        try:
            lid = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Invalid listener ID.")
            return
        new_state = self.db.toggle_listener(lid)
        if new_state is None:
            await update.message.reply_text(f"Listener #{lid} not found.")
            return
        status = "enabled" if new_state else "disabled"
        await update.message.reply_text(f"Listener #{lid} {status}.")

    async def _cmd_create_rule(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args or len(context.args) < 2:
            await update.message.reply_text("Usage: /create_rule <handler_name> <sender_domain>")
            return
        handler_name = context.args[0]
        sender_domain = context.args[1]

        listener = ListenerConfig(
            name=f"Auto: {handler_name} for {sender_domain}",
            description=f"Auto-generated rule for {sender_domain}",
            handler_name=handler_name,
            conditions={"sender_domain": sender_domain},
        )
        lid = self.db.add_listener(listener)
        await self._reply(update.message, f"规则已创建 (ID: {lid})：{handler_name} for {sender_domain}")

    @staticmethod
    def _build_reminder_keyboard(reminder: dict) -> InlineKeyboardMarkup | None:
        event = reminder.get("event", "")
        deadline = reminder.get("deadline", "")
        if not event or not deadline:
            return None
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⏰ 提前1天提醒", callback_data=f"remind:1:{deadline}:{event}"),
                InlineKeyboardButton("⏰ 提前3天提醒", callback_data=f"remind:3:{deadline}:{event}"),
            ]
        ])

    async def _handle_reminder_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()

        parts = query.data.split(":", 3)
        if len(parts) < 4:
            await query.edit_message_reply_markup(reply_markup=None)
            return

        days_before = int(parts[1])
        deadline_str = parts[2]
        event = parts[3]

        try:
            deadline = datetime.fromisoformat(deadline_str)
            remind_at = (deadline - timedelta(days=days_before)).replace(hour=10, minute=30, second=0, microsecond=0)
            if remind_at < datetime.now():
                remind_at = datetime.now() + timedelta(minutes=5)

            reminder = Reminder(
                remind_at=remind_at,
                message=f"{event}（截止: {deadline_str}）",
            )
            rid = self.db.add_reminder(reminder)
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(
                f"✅ 提醒已设置 (#{rid})\n"
                f"将在 {remind_at.strftime('%Y-%m-%d %H:%M')} 提醒你：{event}"
            )
        except Exception as e:
            await query.message.reply_text(f"设置提醒失败: {e}")

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._cmd_start(update, context)

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = update.message.text or ""
        await self._react(update.message, "👀")
        user_id = update.effective_user.id
        wm = self._get_working_memory(user_id)
        answer, reminder = await asyncio.to_thread(
            self.rag.query, text, working_context=wm.get_context_str(),
        )
        wm.add_exchange(text, answer)
        keyboard = self._build_reminder_keyboard(reminder) if reminder else None
        await self._reply_with_keyboard(update.message, answer, keyboard)
