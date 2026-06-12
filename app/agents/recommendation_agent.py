"""Recommendation Agent — suggests what to work on next.

Combines a deterministic scoring model (priority weight + deadline
pressure + staleness) with an optional Gemini-written explanation.
"""
from app.agents.base import BaseAgent
from app.agents.task_agent import task_agent
from app.models import Task, TaskPriority, TaskStatus, utcnow

_PRIORITY_WEIGHT = {
    TaskPriority.URGENT: 100,
    TaskPriority.HIGH: 60,
    TaskPriority.MEDIUM: 30,
    TaskPriority.LOW: 10,
}


class RecommendationAgent(BaseAgent):
    name = "recommendation"

    def _score(self, task: Task) -> float:
        score = float(_PRIORITY_WEIGHT[task.priority])
        now = utcnow()
        if task.due_date is not None:
            hours_left = (task.due_date - now).total_seconds() / 3600
            if hours_left < 0:
                score += 120  # overdue dominates
            elif hours_left < 24:
                score += 80
            elif hours_left < 72:
                score += 40
        age_days = max((now - task.created_at).days, 0)
        score += min(age_days * 2, 20)  # gentle nudge for stale tasks
        return score

    def top_tasks(self, limit: int = 3) -> list[Task]:
        open_tasks = [
            t for t in task_agent.list_tasks()
            if t.status != TaskStatus.COMPLETED
        ]
        return sorted(open_tasks, key=self._score, reverse=True)[:limit]

    async def recommend(self, limit: int = 3) -> str:
        """Human-readable recommendation, LLM-polished when available."""
        top = self.top_tasks(limit)
        if not top:
            return "Your list is clear — nothing to recommend. Add a task to get started."

        bullet_lines = "\n".join(
                f"- #{t.id} {t.title} | priority={t.priority.value} | "
                f"due={t.due_date.strftime('%Y-%m-%d %H:%M') if t.due_date else 'none'} | "
                f"{'OVERDUE' if t.is_overdue else 'on track'}"
            for t in top
        )

        if self.llm.available:
            text = await self.llm.generate(
                prompt=(
                    "Given these candidate tasks (already ranked), write a short, "
                    "motivating recommendation for what to do next. Max 80 words, "
                    "plain text, refer to tasks by their #id.\n" + bullet_lines
                ),
                system="You are a concise, pragmatic productivity coach.",
                temperature=0.6,
            )
            if text:
                return text

        # Deterministic fallback
        lead = top[0]
        why = "it's overdue" if lead.is_overdue else f"it's {lead.priority.value} priority"
        return f"Start with #{lead.id} “{lead.title}” — {why}.\n\nNext up:\n{bullet_lines}"


recommendation_agent = RecommendationAgent()
