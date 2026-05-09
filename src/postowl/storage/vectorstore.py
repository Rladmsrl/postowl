from __future__ import annotations

import logging
from pathlib import Path

import chromadb
from openai import OpenAI

from postowl.config import EmbeddingConfig
from postowl.models import Email

logger = logging.getLogger(__name__)


class OpenAIEmbeddingFunction(chromadb.EmbeddingFunction[list[str]]):
    def __init__(self, client: OpenAI, model: str):
        self._client = client
        self._model = model

    def __call__(self, input: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(input=input, model=self._model)
        return [item.embedding for item in response.data]


class VectorStore:
    COLLECTION_NAME = "emails"

    def __init__(self, chroma_path: Path, embedding_config: EmbeddingConfig | None = None):
        chroma_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(chroma_path))
        ef: chromadb.EmbeddingFunction | None = None
        if embedding_config and embedding_config.api_key:
            client = OpenAI(base_url=embedding_config.base_url, api_key=embedding_config.api_key)
            ef = OpenAIEmbeddingFunction(client, embedding_config.model)
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )

    def index_email(self, email: Email) -> None:
        if not email.id or not email.body_text:
            return
        doc = self._build_document(email)
        doc_id = str(email.id)
        recipients = ", ".join(email.recipients) if email.recipients else ""
        metadata = {
            "email_id": email.id,
            "account_id": email.account_id,
            "sender": email.sender_addr,
            "recipients": recipients,
            "subject": email.subject or "",
            "category": email.category.value,
            "date": email.date.isoformat() if email.date else "",
        }
        self._collection.upsert(ids=[doc_id], documents=[doc], metadatas=[metadata])

    def index_emails(self, emails: list[Email]) -> None:
        valid = [e for e in emails if e.id and e.body_text]
        if not valid:
            return
        ids = [str(e.id) for e in valid]
        docs = [self._build_document(e) for e in valid]
        metadatas = [
            {
                "email_id": e.id,
                "account_id": e.account_id,
                "sender": e.sender_addr,
                "recipients": ", ".join(e.recipients) if e.recipients else "",
                "subject": e.subject or "",
                "category": e.category.value,
                "date": e.date.isoformat() if e.date else "",
            }
            for e in valid
        ]
        batch_size = 10
        for i in range(0, len(ids), batch_size):
            self._collection.upsert(
                ids=ids[i:i + batch_size],
                documents=docs[i:i + batch_size],
                metadatas=metadatas[i:i + batch_size],
            )

    def query(self, query_text: str, n_results: int = 10) -> list[dict]:
        results = self._collection.query(query_texts=[query_text], n_results=n_results)
        items = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                item = {
                    "email_id": int(doc_id),
                    "document": results["documents"][0][i] if results["documents"] else "",
                    "distance": results["distances"][0][i] if results["distances"] else 0,
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                }
                items.append(item)
        return items

    def count(self) -> int:
        return self._collection.count()

    @staticmethod
    def _build_document(email: Email) -> str:
        parts = []
        if email.subject:
            parts.append(f"Subject: {email.subject}")
        parts.append(f"From: {email.sender_name or ''} <{email.sender_addr}>")
        if email.recipients:
            parts.append(f"To: {', '.join(email.recipients)}")
        if email.date:
            parts.append(f"Date: {email.date.isoformat()}")
        if email.summary:
            parts.append(f"Summary: {email.summary}")
        if email.body_text:
            body = email.body_text[:3000]
            parts.append(f"Body: {body}")
        return "\n".join(parts)
