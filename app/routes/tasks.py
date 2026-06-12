"""REST API for tasks — backs the dashboard and any external client."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.agents.orchestrator import orchestrator
from app.agents.priority_agent import priority_agent
from app.agents.recommendation_agent import recommendation_agent
from app.agents.task_agent import task_agent
from app.database import get_db
from app.logger import get_logger
from app.models import TaskPriority, TaskStatus
from app.schemas import ChatRequest, ChatResponse, StatsOut, TaskCreate, TaskOut, TaskUpdate

logger = get_logger("routes.tasks")
router = APIRouter(prefix="/api", tags=["tasks"])


def _to_out(task) -> TaskOut:
    out = TaskOut.model_validate(task)
    out.is_overdue = task.is_overdue
    return out


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    status: Optional[TaskStatus] = Query(None),
    priority: Optional[TaskPriority] = Query(None),
    search: Optional[str] = Query(None, max_length=200),
    overdue: bool = Query(False),
    db: Session = Depends(get_db),
) -> list[TaskOut]:
    tasks = task_agent.list_tasks(status=status, priority=priority, search=search, overdue_only=overdue)
    return [_to_out(t) for t in tasks]


@router.post("/tasks", response_model=TaskOut, status_code=201)
async def create_task(payload: TaskCreate) -> TaskOut:
    priority = payload.priority or await priority_agent.assign_priority(
        payload.title, payload.description, payload.due_date
    )
    task = task_agent.create_task(
        title=payload.title,
        description=payload.description,
        priority=priority,
        due_date=payload.due_date,
    )
    return _to_out(task)


@router.get("/tasks/stats", response_model=StatsOut)
def stats() -> StatsOut:
    return task_agent.stats()


@router.get("/tasks/overdue", response_model=list[TaskOut])
def overdue() -> list[TaskOut]:
    return [_to_out(t) for t in task_agent.overdue_tasks()]


@router.get("/tasks/recommendations")
async def recommendations() -> dict[str, str]:
    return {"recommendation": await recommendation_agent.recommend()}


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: int) -> TaskOut:
    task = task_agent.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return _to_out(task)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: int, payload: TaskUpdate) -> TaskOut:
    task = task_agent.update_task(task_id, **payload.model_dump(exclude_unset=True))
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return _to_out(task)


@router.post("/tasks/{task_id}/complete", response_model=TaskOut)
def complete_task(task_id: int) -> TaskOut:
    task = task_agent.complete_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return _to_out(task)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int) -> None:
    if not task_agent.delete_task(task_id):
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    """Talk to the Orchestrator from the web (same brain as the Telegram bot)."""
    reply = await orchestrator.handle_message(payload.chat_id, payload.message)
    return ChatResponse(reply=reply)
