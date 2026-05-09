from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from postowl.agent.classifier import classify_email
from postowl.agent.summarizer import summarize_email
from postowl.email.client import EmailClient
from postowl.llm.client import LLMClient
from postowl.models import Email, EmailAccount, EmailCategory
from postowl.storage.database import Database
from postowl.storage.vectorstore import VectorStore

if TYPE_CHECKING:
    from postowl.listener.engine import ListenerEngine
    from postowl.listener.learner import RuleLearner
    from postowl.memory.index import MemoryIndex

logger = logging.getLogger(__name__)


def process_email(
    email: Email,
    llm: LLMClient,
    db: Database,
    vs: VectorStore,
    *,
    listener_engine: ListenerEngine | None = None,
    on_progress: Callable[[Email, str], None] | None = None,
) -> Email:
    """Process a single email: classify -> update DB -> summarize -> update DB -> index vectorstore -> listeners."""
    cr = classify_email(llm, email)
    if cr.category == EmailCategory.UNKNOWN:
        logger.warning(
            "Email %s classified as UNKNOWN after processing (possible retry exhaustion)",
            email.message_id,
        )
    db.update_email_classification(email.id, cr.category, cr.priority)  # type: ignore[arg-type]
    email.category = cr.category
    email.priority = cr.priority

    sr = summarize_email(llm, email)
    db.update_email_summary(email.id, sr.summary)  # type: ignore[arg-type]
    email.summary = sr.summary

    vs.index_email(email)

    if listener_engine:
        listener_engine.check_event("email_received", email)

    if on_progress:
        on_progress(email, "done")

    return email


def process_emails_batch(
    emails: list[Email],
    llm: LLMClient,
    db: Database,
    vs: VectorStore,
    *,
    max_workers: int = 4,
    listener_engine: ListenerEngine | None = None,
    on_progress: Callable[[Email, str], None] | None = None,
) -> list[Email]:
    """Process multiple emails with parallel LLM calls. Each email is persisted as soon as its LLM calls finish."""
    if not emails:
        return []

    import threading
    _db_lock = threading.Lock()
    processed: list[Email] = []

    def _process_one(email: Email) -> Email | None:
        try:
            cr = classify_email(llm, email)
            sr = summarize_email(llm, email)
        except Exception:
            logger.warning("LLM failed for email %s", email.message_id, exc_info=True)
            return None

        if cr.category == EmailCategory.UNKNOWN:
            logger.warning(
                "Email %s classified as UNKNOWN after processing (possible retry exhaustion)",
                email.message_id,
            )

        with _db_lock:
            db.update_email_classification(email.id, cr.category, cr.priority)  # type: ignore[arg-type]
            email.category = cr.category
            email.priority = cr.priority
            db.update_email_summary(email.id, sr.summary)  # type: ignore[arg-type]
            email.summary = sr.summary
            vs.index_email(email)
            if listener_engine:
                listener_engine.check_event("email_received", email)

        if on_progress:
            on_progress(email, "done")
        return email

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_process_one, e): e for e in emails}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    processed.append(result)
            except Exception:
                email = futures[future]
                logger.warning("Failed to process email %s", email.message_id, exc_info=True)

    return processed


def fetch_and_process(
    account: EmailAccount,
    llm: LLMClient,
    db: Database,
    vs: VectorStore,
    *,
    max_workers: int = 4,
    limit: int | None = None,
    listener_engine: ListenerEngine | None = None,
    memory_index: MemoryIndex | None = None,
    rule_learner: RuleLearner | None = None,
    notify_suggestions: Callable[[list[dict]], None] | None = None,
    on_progress: Callable[[Email, str], None] | None = None,
    on_error: Callable[[EmailAccount, Exception], None] | None = None,
) -> list[Email]:
    """Fetch new emails from an account and process them in parallel."""
    try:
        with EmailClient(account) as client:
            fetch_kwargs: dict = {"since_uid": account.last_uid}
            if limit is not None:
                fetch_kwargs["limit"] = limit
            emails = client.fetch_new_emails(**fetch_kwargs)
            if not emails:
                return []

            max_uid = account.last_uid
            to_process: list[Email] = []

            for email_obj in emails:
                eid = db.save_email(email_obj)
                if eid:
                    email_obj.id = eid
                    to_process.append(email_obj)
                max_uid = max(max_uid, email_obj.uid)

            processed = process_emails_batch(
                to_process, llm, db, vs,
                max_workers=max_workers, listener_engine=listener_engine,
                on_progress=on_progress,
            )

            db.update_last_uid(account.id, max_uid)  # type: ignore[arg-type]

            if memory_index and processed:
                try:
                    memory_index.refresh()
                except Exception as e:
                    logger.warning("Failed to refresh memory index: %s", e)

            if rule_learner and processed:
                try:
                    for email in processed:
                        action = "ignore" if email.category.value in ("newsletter", "notification", "promotion") else "received"
                        rule_learner.log_action(0, action, email)
                    suggestions = rule_learner.detect_patterns(0)
                    if suggestions and notify_suggestions:
                        notify_suggestions(suggestions)
                except Exception as e:
                    logger.warning("Rule learning failed: %s", e)

            return processed
    except Exception as exc:
        if on_error:
            on_error(account, exc)
            return []
        raise
