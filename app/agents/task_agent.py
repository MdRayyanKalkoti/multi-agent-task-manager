"""Task Agent — owns every database operation on tasks (CRUD, search, filters)."""
from datetime import datetime
from typing import Optional

from sqlalchemy import func, or_, select

from app.agents.base import BaseAgent
from app.database import db_session
from app.models import Task, TaskPriority, TaskStatus, utcnow
from app.schemas import StatsOut


class TaskAgent(BaseAgent):
    name = "task"

    # ── Create / update / complete ─────────────────────────────────────
    def create_task(
        self,
        title: str,
        description: str = "",
        priority: TaskPriority = TaskPriority.MEDIUM,
        due_date: Optional[datetime] = None,
        chat_id: Optional[str] = None,
    ) -> Task:
        with db_session() as db:
            task = Task(
                title=title.strip(),
                description=description.strip(),
                priority=priority,
                due_date=due_date,
                chat_id=chat_id,
            )
            db.add(task)
            db.flush()
            db.refresh(task)
            self.logger.info("Created task #%s %r", task.id, task.title)
            return task

    def update_task(self, task_id: int, **fields: object) -> Optional[Task]:
        with db_session() as db:
            task = db.get(Task, task_id)
            if task is None:
                return None
            for key, value in fields.items():
                if value is not None and hasattr(task, key):
                    setattr(task, key, value)
            if fields.get("status") == TaskStatus.COMPLETED:
                task.completed_at = utcnow()
            db.flush()
            db.refresh(task)
            self.logger.info("Updated task #%s (%s)", task.id, ", ".join(k for k, v in fields.items() if v is not None))
            return task

    def complete_task(self, task_id: int) -> Optional[Task]:
        return self.update_task(task_id, status=TaskStatus.COMPLETED)

    def delete_task(self, task_id: int) -> bool:
        with db_session() as db:
            task = db.get(Task, task_id)
            if task is None:
                return False
            db.delete(task)
            self.logger.info("Deleted task #%s", task_id)
            return True

    # ── Read / search / filter ─────────────────────────────────────────
    def get_task(self, task_id: int) -> Optional[Task]:
        with db_session() as db:
            return db.get(Task, task_id)

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        priority: Optional[TaskPriority] = None,
        search: Optional[str] = None,
        overdue_only: bool = False,
        limit: int = 200,
    ) -> list[Task]:
        with db_session() as db:
            stmt = select(Task)
            if status is not None:
                stmt = stmt.where(Task.status == status)
            if priority is not None:
                stmt = stmt.where(Task.priority == priority)
            if search:
                pattern = f"%{search.strip()}%"
                stmt = stmt.where(or_(Task.title.ilike(pattern), Task.description.ilike(pattern)))
            if overdue_only:
                stmt = stmt.where(Task.due_date.is_not(None), Task.due_date < utcnow(), Task.status != TaskStatus.COMPLETED)
            stmt = stmt.order_by(Task.due_date.is_(None), Task.due_date.asc(), Task.created_at.desc()).limit(limit)
            return list(db.scalars(stmt).all())

    def overdue_tasks(self) -> list[Task]:
        return self.list_tasks(overdue_only=True)

    def stats(self) -> StatsOut:
        with db_session() as db:
            total = db.scalar(select(func.count(Task.id))) or 0
            by_status = dict(
                db.execute(select(Task.status, func.count(Task.id)).group_by(Task.status)).all()
            )
            overdue = db.scalar(
                select(func.count(Task.id)).where(
                    Task.due_date.is_not(None),
                    Task.due_date < utcnow(),
                    Task.status != TaskStatus.COMPLETED,
                )
            ) or 0
        return StatsOut(
            total=total,
            pending=by_status.get(TaskStatus.PENDING, 0),
            in_progress=by_status.get(TaskStatus.IN_PROGRESS, 0),
            completed=by_status.get(TaskStatus.COMPLETED, 0),
            overdue=overdue,
        )


task_agent = TaskAgent()
