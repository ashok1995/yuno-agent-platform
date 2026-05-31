import asyncio
import logging
import threading

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest, InvalidToken, NetworkError, TimedOut
from sqlalchemy import func

from app.config import settings
from app.database import (
    SessionLocal,
    MessageLogModel,
    USER_SENDER_TELEGRAM,
    USER_SENDER_WEB,
    INTERNAL_MESSAGE_SENDERS,
)
from app.graph import execute_workflow_pipeline
from app.router_service import OrchestrationRouterService, TELEGRAM_FRIENDLY_FALLBACK

logger = logging.getLogger("yuno.telegram")

USER_SENDERS = frozenset({USER_SENDER_TELEGRAM, USER_SENDER_WEB})


def _latest_agent_reply(db, workflow_id: str, *, after_id: int = 0) -> str | None:
    """Prefer specialist/user-facing agent text over orchestrator routing lines."""
    rows = (
        db.query(MessageLogModel)
        .filter(
            MessageLogModel.workflow_id == workflow_id,
            MessageLogModel.id > after_id,
            MessageLogModel.sender.notin_(USER_SENDERS),
        )
        .order_by(MessageLogModel.id.desc())
        .all()
    )
    for row in rows:
        if row.sender in INTERNAL_MESSAGE_SENDERS:
            continue
        text = (row.content or "").strip()
        if text and not text.startswith("[Ollama HTTP"):
            return text
    for row in rows:
        text = (row.content or "").strip()
        if text:
            return text
    return None


async def _safe_edit_message(status_msg, text: str) -> None:
    """Edit Telegram status bubble; fall back to new message if edit fails."""
    if len(text) > 4000:
        text = text[:4000] + "\n\n_[truncated]_"
    try:
        await status_msg.edit_text(text)
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        logger.warning(f"Telegram edit failed, sending new message: {e}")
        await status_msg.reply_text(text)


async def _process_telegram_message(
    db,
    workflow_id: str,
    user_text: str,
    last_id_before: int,
) -> str:
    from app.agent_runtime import build_conversation_context

    routing_context = build_conversation_context(db, workflow_id, user_text)
    resolved = await OrchestrationRouterService.resolve_topology(
        user_prompt=routing_context,
        requested_template="dynamic_router_intent",
        current_prompt=user_text,
    )
    logger.info(
        "Telegram message routed — workflow=%s template=%s prompt=%r",
        workflow_id,
        resolved,
        user_text[:120],
    )
    await execute_workflow_pipeline(resolved, workflow_id, user_text, channel="telegram")

    db.expire_all()
    ai_response = _latest_agent_reply(db, workflow_id, after_id=last_id_before)
    if not ai_response:
        if OrchestrationRouterService._is_small_talk(user_text):
            return TELEGRAM_FRIENDLY_FALLBACK
        return (
            "⚠️ I couldn't generate a reply. "
            "Please check Ollama is running (`ollama serve`) and try again."
        )
    return ai_response


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to Yuno Agent Platform\n\n"
        "Send any message — same multi-agent pipeline as the web dashboard.\n\n"
        "Try:\n"
        "• Plan a 3-day trip to Dharamshala\n"
        "• 1000 invested at 5% annual return for 5 years\n"
        "• Audit: x = eval(input())\n"
        "• Hi — I'll say hello back!"
    )


async def handle_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = (update.message.text or "").strip()
    if not user_text:
        return

    chat_id = update.effective_chat.id
    workflow_id = f"tg_{chat_id}"
    logger.info(
        "Telegram inbound — chat_id=%s workflow=%s text=%r",
        chat_id,
        workflow_id,
        user_text[:120],
    )

    status_msg = await update.message.reply_text(
        "🧠 Routing through multi-agent workflow…",
    )

    db = SessionLocal()
    try:
        last_id_before = (
            db.query(func.max(MessageLogModel.id))
            .filter(MessageLogModel.workflow_id == workflow_id)
            .scalar()
        ) or 0

        ai_response = await asyncio.wait_for(
            _process_telegram_message(db, workflow_id, user_text, last_id_before),
            timeout=settings.TELEGRAM_REPLY_TIMEOUT_SEC,
        )
        await _safe_edit_message(status_msg, ai_response)
        logger.info(
            "Telegram reply sent — workflow=%s chars=%d",
            workflow_id,
            len(ai_response),
        )

    except asyncio.TimeoutError:
        logger.warning(f"Telegram reply timed out for workflow {workflow_id}")
        await _safe_edit_message(
            status_msg,
            "⏱️ This is taking longer than expected. "
            "Ollama may be busy — please try again in a moment.",
        )
    except Exception as e:
        logger.error(f"Telegram handler error: {e}", exc_info=True)
        await _safe_edit_message(status_msg, f"❌ Runtime error: {e}")
    finally:
        db.close()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        logger.debug(f"Telegram transient network error: {err}")
    else:
        logger.error(f"Telegram bot error: {err}", exc_info=err)


def start_telegram_channel_polling():
    """Start Telegram bot in a daemon thread when TELEGRAM_BOT_TOKEN is set in .env."""
    token = (settings.TELEGRAM_BOT_TOKEN or "").strip()
    if not token or token == "YOUR_BOT_TOKEN_HERE":
        logger.warning("Telegram token not set — channel polling deactivated.")
        print("⚠️  Telegram: set TELEGRAM_BOT_TOKEN in backend/.env to enable.")
        return

    parts = token.split(":")
    if len(parts) != 2 or not parts[0].isdigit() or len(parts[1]) < 10:
        logger.warning("Telegram token format invalid — channel polling deactivated.")
        return

    def run_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            application = Application.builder().token(token).build()
            application.add_handler(CommandHandler("start", start_command))
            application.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_message)
            )
            application.add_error_handler(error_handler)

            logger.info("Telegram channel polling started.")
            print("🚀 Telegram channel live — messages use full workflow pipeline.")

            application.run_polling(
                close_loop=False,
                allowed_updates=["message"],
                stop_signals=None,
                bootstrap_retries=3,
            )
        except InvalidToken:
            logger.error("Invalid Telegram bot token.")
            print("❌ Telegram: invalid token — check TELEGRAM_BOT_TOKEN in .env")
        except Exception as e:
            logger.error(f"Telegram bot crashed: {e}")

    thread = threading.Thread(target=run_bot, daemon=True, name="telegram-polling")
    thread.start()
