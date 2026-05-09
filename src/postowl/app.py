from __future__ import annotations

import asyncio
import logging

from postowl.agent.rag import RAGEngine
from postowl.bot import PostOwlBot
from postowl.config import Settings
from postowl.listener.builtin import register_builtin_handlers
from postowl.listener.engine import ListenerEngine
from postowl.listener.learner import RuleLearner
from postowl.llm.client import LLMClient
from postowl.memory.contacts import ContactManager
from postowl.memory.index import MemoryIndex
from postowl.scheduler import PostOwlScheduler
from postowl.storage.database import Database
from postowl.storage.vectorstore import VectorStore

logger = logging.getLogger(__name__)


def run(settings: Settings) -> None:
    db = Database(settings.db_path)
    llm = LLMClient(settings.llm)
    vs = VectorStore(settings.chroma_path, settings.embedding)

    memory_index = MemoryIndex(db, llm)
    contact_mgr = ContactManager(db)
    contact_mgr.refresh_from_emails()

    rag = RAGEngine(llm, db, vs, memory_index=memory_index)

    rule_learner = RuleLearner(db)

    bot = PostOwlBot(settings, db, llm, vs, rag)
    tg_app = bot.build_app()

    db.ensure_builtin_listeners()

    async def _broadcast_notification(message: str, priority: str) -> None:
        for uid in settings.telegram.allowed_user_ids:
            await bot.send_notification(uid, message)

    listener_engine = ListenerEngine(db, llm, notify_fn=_broadcast_notification)
    register_builtin_handlers(listener_engine)
    listener_engine.load_listeners()

    def _notify_rule_suggestions(suggestions: list[dict]) -> None:
        loop = asyncio.get_event_loop()
        for uid in settings.telegram.allowed_user_ids:
            for s in suggestions[:1]:
                msg = (
                    f"\U0001f4a1 {s['suggestion']}\n\n"
                    f"回复 /create_rule {s['handler_name']} {s['sender_domain']} 来创建规则"
                )
                asyncio.run_coroutine_threadsafe(bot.send_notification(uid, msg), loop)

    scheduler = PostOwlScheduler(
        settings, db, llm, vs,
        notify_callback=bot.send_notification,
        listener_engine=listener_engine,
        memory_index=memory_index,
        rule_learner=rule_learner,
        notify_suggestions=_notify_rule_suggestions,
    )

    async def post_init(application):
        from telegram import BotCommand
        await application.bot.set_my_commands([
            BotCommand("fetch", "拉取新邮件"),
            BotCommand("today", "今日邮件摘要"),
            BotCommand("week", "本周邮件摘要"),
            BotCommand("ask", "提问关于邮件的问题"),
            BotCommand("search", "搜索邮件"),
            BotCommand("categories", "邮件分类统计"),
            BotCommand("remind", "设置提醒"),
            BotCommand("reminders", "查看待办提醒"),
            BotCommand("accounts", "查看邮箱账户"),
            BotCommand("listeners", "查看邮件规则"),
            BotCommand("listener_toggle", "启停邮件规则"),
            BotCommand("create_rule", "创建自动规则"),
            BotCommand("help", "帮助"),
        ])
        scheduler.start()
        logger.info("PostOwl is running! Press Ctrl+C to stop.")

    async def post_shutdown(application):
        scheduler.stop()
        db.close()
        logger.info("PostOwl stopped.")

    tg_app.post_init = post_init
    tg_app.post_shutdown = post_shutdown

    import time
    max_retries = 0  # 无限重试
    attempt = 0
    while True:
        attempt += 1
        try:
            tg_app.run_polling(drop_pending_updates=True)
            break
        except Exception as e:
            logger.error("Polling crashed (attempt %d): %s", attempt, e)
            if max_retries and attempt >= max_retries:
                raise
            wait = min(30, 5 * attempt)
            logger.info("Restarting in %d seconds...", wait)
            time.sleep(wait)
