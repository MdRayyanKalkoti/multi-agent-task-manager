"""Priority Agent — decides how urgent a task is.

Strategy: deterministic keyword/deadline heuristics first (fast, free,
predictable), then an optional Gemini refinement for ambiguous cases.
"""
from datetime import datetime, timedelta
from typing import Optional

from app.agents.base import BaseAgent
from app.models import TaskPriority, utcnow

_URGENT_WORDS = ("urgent", "asap", "immediately", "critical", "emergency", "now")
_HIGH_WORDS = ("important", "deadline", "exam", "interview", "payment", "submit", "due")
_LOW_WORDS = ("someday", "eventually", "maybe", "low priority", "whenever", "optional")


class PriorityAgent(BaseAgent):
    name = "priority"

    def heuristic_priority(self, title: str, description: str, due_date: Optional[datetime]) -> TaskPriority:
        text = f"{title} {description}".lower()
        if any(w in text for w in _URGENT_WORDS):
            return TaskPriority.URGENT
        if due_date is not None:
            delta = due_date - utcnow()
            if delta <= timedelta(hours=24):
                return TaskPriority.URGENT
            if delta <= timedelta(days=3):
                return TaskPriority.HIGH
        if any(w in text for w in _HIGH_WORDS):
            return TaskPriority.HIGH
        if any(w in text for w in _LOW_WORDS):
            return TaskPriority.LOW
        return TaskPriority.MEDIUM

    async def assign_priority(self, title: str, description: str = "", due_date: Optional[datetime] = None) -> TaskPriority:
        """Heuristics first; ask Gemini only when the answer is the bland default."""
        guess = self.heuristic_priority(title, description, due_date)
        if guess != TaskPriority.MEDIUM or not self.llm.available:
            return guess

        data = await self.llm.generate_json(
            prompt=(
                "Classify the priority of this task as exactly one of: "
                '"low", "medium", "high", "urgent".\n'
                f"Title: {title}\nDescription: {description or '(none)'}\n"
                f"Due date: {due_date.isoformat() if due_date else 'none'}\n"
                'Respond with JSON only: {"priority": "..."}'
            ),
            system="You are a precise task-priority classifier. Output JSON only.",
        )
        if data:
            try:
                refined = TaskPriority(str(data.get("priority", "")).lower())
                self.logger.info("Gemini refined priority for %r -> %s", title, refined.value)
                return refined
            except ValueError:
                self.logger.warning("Gemini returned invalid priority: %s", data)
        return guess


priority_agent = PriorityAgent()
