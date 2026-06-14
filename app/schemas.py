"""Pydantic request/response schemas."""
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import TaskPriority, TaskStatus


def _to_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Normalise any incoming datetime to naive UTC.

    The dashboard sends timezone-aware ISO strings (e.g. '...Z' or '+05:30'),
    while the bot/heuristics produce naive UTC. The database stores naive UTC,
    so we convert aware values to UTC and drop the tzinfo to keep every code
    path consistent (and avoid 'naive vs aware' subtraction errors).
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    priority: Optional[TaskPriority] = None  # None -> Priority Agent decides
    due_date: Optional[datetime] = None

    @field_validator("due_date")
    @classmethod
    def _normalise_due(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _to_naive_utc(v)


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    priority: Optional[TaskPriority] = None
    due_date: Optional[datetime] = None

    @field_validator("due_date")
    @classmethod
    def _normalise_due(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _to_naive_utc(v)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    due_date: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]
    is_overdue: bool = False


class StatsOut(BaseModel):
    total: int
    pending: int
    in_progress: int
    completed: int
    overdue: int


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    chat_id: str = "web"


class ChatResponse(BaseModel):
    reply: str
    