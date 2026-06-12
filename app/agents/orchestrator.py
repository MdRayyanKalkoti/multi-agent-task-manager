"""Orchestrator Agent — the single entry point for natural-language requests.

Flow per message:
  1. Memory Agent supplies recent conversation context.
  2. Gemini 2.5 Flash parses the message into a structured intent
     (a regex fallback parser keeps the system usable without an API key).
  3. The intent is routed to the Task / Priority / Recommendation agents.
  4. The reply is composed and both turns are written back to memory.
"""
import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from app.agents.base import BaseAgent
from app.agents.memory_agent import memory_agent
from app.agents.priority_agent import priority_agent
from app.agents.recommendation_agent import recommendation_agent
from app.agents.task_agent import task_agent
from app.models import Task, TaskPriority, TaskStatus, utcnow

_INTENT_SYSTEM = """You convert a user's task-management message into JSON.
Output ONLY a JSON object, no markdown. Schema:
{
  "intent": "create_task" | "update_task" | "complete_task" | "delete_task"
          | "list_tasks" | "search_tasks" | "overdue_tasks" | "recommend"
          | "stats" | "help" | "small_talk",
  "task_id": int | null,
  "title": string | null,
  "description": string | null,
  "priority": "low" | "medium" | "high" | "urgent" | null,
  "status": "pending" | "in_progress" | "completed" | null,
  "due_in_hours": number | null,          // relative deadline if mentioned
  "due_date_iso": string | null,          // absolute deadline if mentioned (ISO 8601)
  "search_query": string | null
}
Use the conversation context to resolve references like "that task".
Current UTC time: {now}."""


class OrchestratorAgent(BaseAgent):
    name = "orchestrator"

    # ── Public API ──────────────────────────────────────────────────────
    async def handle_message(self, chat_id: str, message: str) -> str:
        message = message.strip()
        if not message:
            return "Say something like: add task buy milk tomorrow 5pm"

        memory_agent.remember(chat_id, "user", message)
        intent = await self._parse_intent(chat_id, message)
        self.logger.info("chat=%s intent=%s", chat_id, intent.get("intent"))

        try:
            reply = await self._dispatch(chat_id, intent, message)
        except Exception as exc:  # never let one bad turn kill the bot
            self.logger.exception("Dispatch failed: %s", exc)
            reply = "Something went wrong handling that. Try rephrasing, or use /help."

        memory_agent.remember(chat_id, "assistant", reply)
        return reply

    # ── Intent parsing ──────────────────────────────────────────────────
    async def _parse_intent(self, chat_id: str, message: str) -> dict[str, Any]:
        if self.llm.available:
            context = memory_agent.context_text(chat_id)
            data = await self.llm.generate_json(
                prompt=f"Conversation so far:\n{context}\n\nNew user message:\n{message}",
                system=_INTENT_SYSTEM.replace("{now}", utcnow().isoformat()),
            )
            if data and data.get("intent"):
                return data
            self.logger.warning("LLM intent parse failed; using fallback parser.")
        return self._fallback_parse(message)

    def _fallback_parse(self, message: str) -> dict[str, Any]:
        """Deterministic keyword parser used when Gemini is unavailable."""
        text = message.lower().strip()
        id_match = re.search(r"#?(\d+)", text)
        task_id = int(id_match.group(1)) if id_match else None

        def base(intent: str, **extra: Any) -> dict[str, Any]:
            return {"intent": intent, "task_id": task_id, "title": None, "description": None,
                    "priority": None, "status": None, "due_in_hours": None,
                    "due_date_iso": None, "search_query": None, **extra}

        if any(w in text for w in ("recommend", "what should i do", "what next", "suggest")):
            return base("recommend")
        if "overdue" in text:
            return base("overdue_tasks")
        if any(w in text for w in ("stats", "summary", "progress")):
            return base("stats")
        if text.startswith(("search", "find")):
            return base("search_tasks", search_query=re.sub(r"^(search|find)( for)?\s*", "", text))
        if any(w in text for w in ("list", "show", "view", "my tasks", "all tasks")):
            return base("list_tasks")
        if any(w in text for w in ("done", "complete", "finished", "mark")) and task_id:
            return base("complete_task")
        if text.startswith(("delete", "remove")) and task_id:
            return base("delete_task")
        if text.startswith(("add", "create", "new task", "remind me to")):
            title = re.sub(r"^(add( a)?( task)?|create( a)?( task)?|new task|remind me to)\s*:?\s*", "", message, flags=re.I)
            return base("create_task", title=title or message)
        if any(w in text for w in ("help", "how do", "what can you")):
            return base("help")
        return base("small_talk")

    # ── Routing ─────────────────────────────────────────────────────────
    async def _dispatch(self, chat_id: str, intent: dict[str, Any], raw: str) -> str:
        kind = str(intent.get("intent", "small_talk"))
        handler = {
            "create_task": self._create,
            "update_task": self._update,
            "complete_task": self._complete,
            "delete_task": self._delete,
            "list_tasks": self._list,
            "search_tasks": self._search,
            "overdue_tasks": self._overdue,
            "recommend": self._recommend,
            "stats": self._stats,
            "help": self._help,
        }.get(kind)
        if handler is None:
            return await self._small_talk(chat_id, raw)
        return await handler(chat_id, intent)

    # ── Helpers ─────────────────────────────────────────────────────────
    @staticmethod
    def _resolve_due(intent: dict[str, Any]) -> Optional[datetime]:
        iso = intent.get("due_date_iso")
        if iso:
            try:
                parsed = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
                return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
            except ValueError:
                pass
        hours = intent.get("due_in_hours")
        if isinstance(hours, (int, float)) and hours > 0:
            return utcnow() + timedelta(hours=float(hours))
        return None

    @staticmethod
    def _fmt(task: Task) -> str:
        flag = {"urgent": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}[task.priority.value]
        due = f" · due {task.due_date.strftime('%d %b %H:%M')}" if task.due_date else ""
        overdue = " ⚠️OVERDUE" if task.is_overdue else ""
        status = "✅" if task.status == TaskStatus.COMPLETED else ("▶️" if task.status == TaskStatus.IN_PROGRESS else "⬜")
        return f"{status} #{task.id} {flag} {task.title}{due}{overdue}"

    # ── Intent handlers ─────────────────────────────────────────────────
    async def _create(self, chat_id: str, intent: dict[str, Any]) -> str:
        title = (intent.get("title") or "").strip()
        if not title:
            return "What should the task be called? e.g. add task: submit report by Friday"
        description = (intent.get("description") or "").strip()
        due_date = self._resolve_due(intent)

        if intent.get("priority"):
            try:
                priority = TaskPriority(str(intent["priority"]).lower())
            except ValueError:
                priority = await priority_agent.assign_priority(title, description, due_date)
        else:
            priority = await priority_agent.assign_priority(title, description, due_date)

        task = task_agent.create_task(title, description, priority, due_date, chat_id=chat_id)
        return f"Added:\n{self._fmt(task)}\nPriority set to {priority.value} by the Priority Agent."

    async def _update(self, chat_id: str, intent: dict[str, Any]) -> str:
        task_id = intent.get("task_id")
        if not task_id:
            return "Which task? Give me its number, e.g. move #3 to high priority."
        fields: dict[str, Any] = {}
        if intent.get("title"):
            fields["title"] = str(intent["title"]).strip()
        if intent.get("description"):
            fields["description"] = str(intent["description"]).strip()
        if intent.get("priority"):
            try:
                fields["priority"] = TaskPriority(str(intent["priority"]).lower())
            except ValueError:
                pass
        if intent.get("status"):
            try:
                fields["status"] = TaskStatus(str(intent["status"]).lower())
            except ValueError:
                pass
        due = self._resolve_due(intent)
        if due:
            fields["due_date"] = due
        if not fields:
            return "Tell me what to change — title, priority, status or due date."
        task = task_agent.update_task(int(task_id), **fields)
        return f"Updated:\n{self._fmt(task)}" if task else f"No task #{task_id} found."

    async def _complete(self, chat_id: str, intent: dict[str, Any]) -> str:
        task_id = intent.get("task_id")
        if not task_id:
            return "Which task is done? e.g. done #2"
        task = task_agent.complete_task(int(task_id))
        return f"Nice! Marked complete:\n{self._fmt(task)}" if task else f"No task #{task_id} found."

    async def _delete(self, chat_id: str, intent: dict[str, Any]) -> str:
        task_id = intent.get("task_id")
        if not task_id:
            return "Which task should I delete? e.g. delete #4"
        return f"Deleted task #{task_id}." if task_agent.delete_task(int(task_id)) else f"No task #{task_id} found."

    async def _list(self, chat_id: str, intent: dict[str, Any]) -> str:
        status = None
        if intent.get("status"):
            try:
                status = TaskStatus(str(intent["status"]).lower())
            except ValueError:
                pass
        tasks = task_agent.list_tasks(status=status, limit=15)
        if not tasks:
            return "No tasks yet. Try: add task plan the week"
        return "Your tasks:\n" + "\n".join(self._fmt(t) for t in tasks)

    async def _search(self, chat_id: str, intent: dict[str, Any]) -> str:
        query = (intent.get("search_query") or intent.get("title") or "").strip()
        if not query:
            return "What should I search for?"
        tasks = task_agent.list_tasks(search=query, limit=10)
        if not tasks:
            return f"Nothing matched “{query}”."
        return f"Matches for “{query}”:\n" + "\n".join(self._fmt(t) for t in tasks)

    async def _overdue(self, chat_id: str, intent: dict[str, Any]) -> str:
        tasks = task_agent.overdue_tasks()
        if not tasks:
            return "Nothing is overdue. 🎉"
        return "Overdue tasks:\n" + "\n".join(self._fmt(t) for t in tasks)

    async def _recommend(self, chat_id: str, intent: dict[str, Any]) -> str:
        return await recommendation_agent.recommend()

    async def _stats(self, chat_id: str, intent: dict[str, Any]) -> str:
        s = task_agent.stats()
        return (
            f"📊 Stats\nTotal: {s.total}\nPending: {s.pending}\n"
            f"In progress: {s.in_progress}\nCompleted: {s.completed}\nOverdue: {s.overdue}"
        )

    async def _help(self, chat_id: str, intent: dict[str, Any]) -> str:
        return (
            "I manage your tasks in plain language. Try:\n"
            "• add task submit report by Friday 5pm\n"
            "• show my tasks / search report\n"
            "• done #2 · delete #3 · move #1 to high priority\n"
            "• what's overdue? · what should I do next?\n"
            "• stats"
        )

    async def _small_talk(self, chat_id: str, raw: str) -> str:
        if self.llm.available:
            text = await self.llm.generate(
                prompt=f"Context:\n{memory_agent.context_text(chat_id)}\n\nUser: {raw}",
                system=(
                    "You are a friendly task-manager assistant. Reply in under 50 words "
                    "and gently steer toward task management."
                ),
                temperature=0.7,
            )
            if text:
                return text
        return "I'm your task assistant — try /help to see what I can do."


orchestrator = OrchestratorAgent()
