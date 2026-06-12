"""Telegram integration (python-telegram-bot v21, async, long polling).

Runs inside FastAPI's lifespan as a background polling task so a single
Render web service hosts the API, the dashboard, the bot and the scheduler.
"""
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.agents import orchestrator
from app.config import get_settings
from app.logger import get_logger

logger = get_logger("services.telegram")


class TelegramService:
    """Owns the bot Application lifecycle and exposes a send() for agents."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self.app: Optional[Application] = None

    @property
    def enabled(self) -> bool:
        return bool(self._settings.telegram_bot_token)

    # ── Lifecycle ───────────────────────────────────────────────────────
    async def start(self) -> None:
        if not self.enabled:
            logger.warning("TELEGRAM_BOT_TOKEN not set — bot disabled.")
            return
        self.app = (
            ApplicationBuilder()
            .token(self._settings.telegram_bot_token)
            .build()
        )
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("help", self._cmd_help))
        self.app.add_handler(CommandHandler("tasks", self._cmd_tasks))
        self.app.add_handler(CommandHandler("overdue", self._cmd_overdue))
        self.app.add_handler(CommandHandler("recommend", self._cmd_recommend))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        self.app.add_error_handler(self._on_error)

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot polling started.")

    async def stop(self) -> None:
        if self.app is None:
            return
        try:
            if self.app.updater and self.app.updater.running:
                await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            logger.info("Telegram bot stopped.")
        except Exception as exc:
            logger.error("Error stopping Telegram bot: %s", exc)

    # ── Outbound (used by the Reminder Agent) ───────────────────────────
    async def send(self, chat_id: str, text: str) -> None:
        if self.app is None:
            raise RuntimeError("Telegram bot is not running.")
        await self.app.bot.send_message(chat_id=chat_id, text=text)

    # ── Guards ──────────────────────────────────────────────────────────
    def _authorised(self, update: Update) -> bool:
        allowed = self._settings.telegram_allowed_chat_id
        if not allowed:
            return True
        return update.effective_chat is not None and str(update.effective_chat.id) == str(allowed)

    # ── Handlers ────────────────────────────────────────────────────────
    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorised(update) or update.message is None:
            return
        await update.message.reply_text(
            "👋 Hi! I'm your AI task manager.\n"
            "Talk to me naturally — “add task pay rent tomorrow 6pm”, "
            "“what's overdue?”, “done #2”.\nUse /help for more."
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorised(update) or update.message is None:
            return
        reply = await orchestrator.handle_message(str(update.effective_chat.id), "help")
        await update.message.reply_text(reply)

    async def _cmd_tasks(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._route(update, "show my tasks")

    async def _cmd_overdue(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._route(update, "show overdue tasks")

    async def _cmd_recommend(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._route(update, "what should I do next?")

    async def _on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.message is None or not update.message.text:
            return
        await self._route(update, update.message.text)

    async def _route(self, update: Update, text: str) -> None:
        if not self._authorised(update) or update.message is None or update.effective_chat is None:
            return
        await update.effective_chat.send_action("typing")
        reply = await orchestrator.handle_message(str(update.effective_chat.id), text)
        await update.message.reply_text(reply)

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Telegram handler error: %s", context.error)


telegram_service = TelegramService()
