from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from postowl.llm.client import LLMClient
from postowl.storage.database import Database
from postowl.storage.vectorstore import VectorStore

if TYPE_CHECKING:
    from postowl.memory.index import MemoryIndex

logger = logging.getLogger(__name__)

RAG_SYSTEM_PROMPT = """You are PostOwl, a smart email assistant. Answer the user's question based on the provided email context.

{memory_index}

Rules:
- Answer based ONLY on the provided email context
- If the context doesn't contain enough information, say so
- Be concise and helpful
- Respond in the same language as the user's question
- Cite specific emails (sender, subject, date) when relevant

You MUST respond in JSON format:
{{
    "answer": "<your answer text>",
    "sources": [
        {{"sender": "<email>", "subject": "<subject>", "date": "<date>"}}
    ],
    "reminder": {{"event": "<what to remind>", "deadline": "<YYYY-MM-DD>"}} or null
}}

"sources" should list the emails you referenced in your answer. If no relevant emails found, return an empty list.
"reminder" should be set ONLY if the answer mentions a specific deadline, due date, expiration, or time-sensitive event. Set to null otherwise.
"""

FILTER_PROMPT = """Given the user's question, select which emails are relevant from the list below.
Return ONLY a JSON object with the relevant email numbers.

Question: {question}

Emails:
{email_list}

Respond in JSON:
{{
    "relevant": [1, 3, 7],
    "reasoning": "<brief explanation of why these are relevant>"
}}

If none are relevant, return {{"relevant": [], "reasoning": "no relevant emails found"}}.
"""

RAG_USER_PROMPT = """Based on the following emails from my inbox, answer my question.

Email Context:
{context}

Question: {question}
"""


def _format_rag_response(data: dict) -> tuple[str, dict | None]:
    answer = data.get("answer", "")
    sources = data.get("sources", [])
    reminder = data.get("reminder")

    parts = [answer]
    if sources:
        parts.extend(["", "\U0001f4ce 来源:"])
        for s in sources:
            sender = s.get("sender", "")
            subject = s.get("subject", "")
            date = s.get("date", "")
            parts.append(f"  - [{date}] {sender}: {subject}")

    return "\n".join(parts), reminder


class RAGEngine:
    def __init__(self, llm: LLMClient, db: Database, vectorstore: VectorStore,
                 memory_index: MemoryIndex | None = None):
        self.llm = llm
        self.db = db
        self.vectorstore = vectorstore
        self.memory_index = memory_index

    def query(self, question: str, n_results: int = 20, working_context: str = "") -> tuple[str, dict | None]:
        results = self.vectorstore.query(question, n_results=n_results)

        if not results:
            return "没有找到相关的邮件内容来回答你的问题。请确保已经拉取并索引了邮件。", None

        # Phase 1: filter with summaries only
        email_list_lines = []
        for i, r in enumerate(results, 1):
            meta = r.get("metadata", {})
            email_list_lines.append(
                f"[{i}] From: {meta.get('sender', '?')} | "
                f"To: {meta.get('recipients', '?')} | "
                f"Subject: {meta.get('subject', '?')} | "
                f"Date: {meta.get('date', '?')} | "
                f"Category: {meta.get('category', '?')}"
            )

        filter_prompt = FILTER_PROMPT.format(
            question=question,
            email_list="\n".join(email_list_lines),
        )

        try:
            filter_result = self.llm.chat_json([
                {"role": "system", "content": "You are an email relevance filter. Always respond in valid JSON."},
                {"role": "user", "content": filter_prompt},
            ])
            relevant_ids = filter_result.get("relevant", [])
        except Exception:
            relevant_ids = list(range(1, min(len(results), 10) + 1))

        if not relevant_ids:
            return "在已索引的邮件中没有找到与问题相关的内容。", None

        # Phase 2: build full context only for relevant emails
        context_parts = []
        for idx in relevant_ids:
            if idx < 1 or idx > len(results):
                continue
            r = results[idx - 1]
            meta = r.get("metadata", {})
            context_parts.append(
                f"[Email {idx}]\n"
                f"From: {meta.get('sender', 'unknown')}\n"
                f"To: {meta.get('recipients', 'unknown')}\n"
                f"Subject: {meta.get('subject', '(no subject)')}\n"
                f"Date: {meta.get('date', 'unknown')}\n"
                f"Category: {meta.get('category', 'unknown')}\n"
                f"Content: {r.get('document', '')[:2000]}\n"
            )

        context = "\n---\n".join(context_parts)
        prompt = RAG_USER_PROMPT.format(context=context, question=question)
        if working_context:
            prompt = f"{working_context}\n\n{prompt}"

        memory_section = ""
        if self.memory_index:
            idx_text = self.memory_index.get_index()
            if idx_text:
                memory_section = f"## User's Email World (Memory Index)\n{idx_text}\n"

        system_prompt = RAG_SYSTEM_PROMPT.format(memory_index=memory_section)

        try:
            data = self.llm.chat_json([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ], max_tokens=4096)
            return _format_rag_response(data)
        except Exception as e:
            logger.error("RAG query failed: %s", e)
            return f"查询失败: {e}", None
