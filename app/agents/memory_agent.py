"""Memory Agent — short-term conversational memory per chat.

Stores the rolling dialogue in SQLite so the Orchestrator can resolve
references like "mark *that* one done" and so reminders feel contextual.
"""
from sqlalchemy import delete, select

from app.agents.base import BaseAgent
from app.database import db_session
from app.models import MemoryEntry

_MAX_ENTRIES_PER_CHAT = 40  # keep memory bounded
_CONTEXT_WINDOW = 10        # turns handed to the LLM


class MemoryAgent(BaseAgent):
    name = "memory"

    def remember(self, chat_id: str, role: str, content: str) -> None:
        with db_session() as db:
            db.add(MemoryEntry(chat_id=chat_id, role=role, content=content[:2000]))
            # Trim oldest entries beyond the cap
            ids = db.scalars(
                select(MemoryEntry.id)
                .where(MemoryEntry.chat_id == chat_id)
                .order_by(MemoryEntry.created_at.desc(), MemoryEntry.id.desc())
                .offset(_MAX_ENTRIES_PER_CHAT)
            ).all()
            if ids:
                db.execute(delete(MemoryEntry).where(MemoryEntry.id.in_(ids)))

    def recall(self, chat_id: str, limit: int = _CONTEXT_WINDOW) -> list[MemoryEntry]:
        with db_session() as db:
            rows = db.scalars(
                select(MemoryEntry)
                .where(MemoryEntry.chat_id == chat_id)
                .order_by(MemoryEntry.created_at.desc(), MemoryEntry.id.desc())
                .limit(limit)
            ).all()
            return list(reversed(rows))

    def context_text(self, chat_id: str) -> str:
        """Render recent turns as plain text for LLM prompts."""
        lines = [f"{m.role}: {m.content}" for m in self.recall(chat_id)]
        return "\n".join(lines) if lines else "(no prior conversation)"

    def forget(self, chat_id: str) -> int:
        with db_session() as db:
            result = db.execute(delete(MemoryEntry).where(MemoryEntry.chat_id == chat_id))
            self.logger.info("Cleared %s memory rows for chat %s", result.rowcount, chat_id)
            return result.rowcount or 0


memory_agent = MemoryAgent()
