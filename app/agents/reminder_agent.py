"""Reminder Agent — builds daily digests and overdue alerts.

The scheduler service triggers this agent; the agent composes the
message and hands it to whatever sender callable it is given (the
Telegram bot in production), keeping it transport-agnostic.
"""
from collections.abc import Awaitable, Callable

from app.agents.base import BaseAgent
from app.agents.recommendation_agent import recommendation_agent
from app.agents.task_agent import task_agent
from app.models import TaskStatus

SendFunc = Callable[[str, str], Awaitable[None]]  # (chat_id, text) -> None


class ReminderAgent(BaseAgent):
    name = "reminder"

    def _known_chat_ids(self) -> set[str]:
        return {t.chat_id for t in task_agent.list_tasks(limit=1000) if t.chat_id}

    async def build_daily_digest(self) -> str:
        stats = task_agent.stats()
        overdue = task_agent.overdue_tasks()
        recommendation = await recommendation_agent.recommend(limit=3)

        lines = [
            "☀️ Daily task digest",
            f"Open: {stats.pending + stats.in_progress} | Completed: {stats.completed} | Overdue: {stats.overdue}",
        ]
        if overdue:
            lines.append("\n⚠️ Overdue:")
            lines += [f"  • #{t.id} {t.title}" for t in overdue[:5]]
        lines.append("\n🎯 " + recommendation)
        return "\n".join(lines)

    async def send_daily_digest(self, send: SendFunc) -> int:
        """Send the digest to every chat that owns at least one task."""
        chat_ids = self._known_chat_ids()
        if not chat_ids:
            self.logger.info("Daily digest skipped: no Telegram chats on record.")
            return 0
        digest = await self.build_daily_digest()
        sent = 0
        for chat_id in chat_ids:
            try:
                await send(chat_id, digest)
                sent += 1
            except Exception as exc:
                self.logger.error("Failed to send digest to %s: %s", chat_id, exc)
        self.logger.info("Daily digest delivered to %s chat(s).", sent)
        return sent

    async def send_overdue_alerts(self, send: SendFunc) -> int:
        """Hourly safety net: ping chats that have overdue, non-completed tasks."""
        overdue = [t for t in task_agent.overdue_tasks() if t.chat_id and t.status != TaskStatus.COMPLETED]
        by_chat: dict[str, list[str]] = {}
        for t in overdue:
            by_chat.setdefault(t.chat_id, []).append(f"• #{t.id} {t.title}")
        sent = 0
        for chat_id, items in by_chat.items():
            try:
                await send(chat_id, "⏰ Overdue tasks need attention:\n" + "\n".join(items[:8]))
                sent += 1
            except Exception as exc:
                self.logger.error("Failed overdue alert to %s: %s", chat_id, exc)
        return sent


reminder_agent = ReminderAgent()
