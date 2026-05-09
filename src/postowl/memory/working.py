from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class WorkingMemory:
    """Per-user short-term conversational context."""

    topic: str = ""
    exchanges: list[dict] = field(default_factory=list)
    last_active: float = field(default_factory=time.time)
    max_exchanges: int = 5
    ttl_seconds: int = 1800  # 30 minutes

    def add_exchange(self, question: str, answer: str) -> None:
        summary = answer[:200] if len(answer) > 200 else answer
        self.exchanges.append({"question": question, "answer_summary": summary})
        if len(self.exchanges) > self.max_exchanges:
            self.exchanges.pop(0)
        self.last_active = time.time()

    def is_expired(self) -> bool:
        return time.time() - self.last_active > self.ttl_seconds

    def get_context_str(self) -> str:
        if not self.exchanges:
            return ""
        lines = ["## Recent conversation context:"]
        for ex in self.exchanges:
            lines.append(f"Q: {ex['question']}")
            lines.append(f"A: {ex['answer_summary']}")
        return "\n".join(lines)

    def clear(self) -> None:
        self.topic = ""
        self.exchanges.clear()
        self.last_active = time.time()
