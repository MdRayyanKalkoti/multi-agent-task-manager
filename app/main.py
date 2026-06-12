"""FastAPI application entry point.

One process hosts:  REST API · HTML dashboard · Telegram bot (polling)
· APScheduler reminder jobs. Suitable for a single Render web service.
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import init_db
from app.logger import get_logger, setup_logging
from app.routes import dashboard, tasks
from app.services.scheduler import start_scheduler, stop_scheduler
from app.services.telegram_bot import telegram_service

from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

setup_logging()
logger = get_logger("main")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)
    init_db()
    bot_task = asyncio.create_task(_start_bot_safely())
    start_scheduler()
    yield
    stop_scheduler()
    await telegram_service.stop()
    bot_task.cancel()
    logger.info("Shutdown complete.")


async def _start_bot_safely() -> None:
    try:
        await telegram_service.start()
    except Exception as exc:
        logger.error("Telegram bot failed to start (API still serves): %s", exc)


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Multi-agent AI task management: FastAPI + Telegram + Gemini 2.5 Flash.",
    lifespan=lifespan,
)

app.include_router(tasks.router)
app.include_router(dashboard.router)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(_STATIC_DIR / "favicon.ico")

@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """Used by Render's health check."""
    return {"status": "ok", "environment": settings.environment}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
