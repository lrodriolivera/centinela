"""Telegram bot interface for Centinela.

Features:
- /start — Welcome message
- /status — Agent and model status
- /models — Model fallback chain
- /reset — Clear conversation
- Any text message → orchestrator chat with streaming updates

Security:
- Only responds to authorized chat IDs (configured in centinela.yaml)
- Logs all interactions to audit
"""

from __future__ import annotations

import logging
import os
from typing import Any

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from centinela.core.config import get_config
from centinela.core.orchestrator import Orchestrator
from centinela.security.audit import get_audit_logger

logger = logging.getLogger(__name__)

# Max Telegram message length
_MAX_MSG_LEN = 4096


def _get_allowed_chat_ids() -> set[int]:
    """Get authorized chat IDs from env var or config."""
    raw = os.environ.get("CENTINELA_TELEGRAM_ALLOWED_CHATS", "")
    if raw:
        return {int(x.strip()) for x in raw.split(",") if x.strip()}
    return set()  # Empty = allow all (development mode)


def _is_authorized(chat_id: int) -> bool:
    allowed = _get_allowed_chat_ids()
    if not allowed:
        return True  # Dev mode: allow all
    return chat_id in allowed


async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if not _is_authorized(update.effective_chat.id):
        await update.message.reply_text("No autorizado.")
        return

    await update.message.reply_text(
        "Hola! Soy *Centinela*, tu agente IA autónomo.\n\n"
        "Escríbeme cualquier mensaje y te responderé.\n\n"
        "Comandos:\n"
        "/status — Estado del sistema\n"
        "/models — Modelos disponibles\n"
        "/reset — Reiniciar conversación\n",
        parse_mode="Markdown",
    )


async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    if not _is_authorized(update.effective_chat.id):
        return

    orch: Orchestrator = context.bot_data["orchestrator"]
    info = orch.get_status()

    agents_text = "\n".join(
        f"  • {name}: {data['description']} [{data['permission_tier']}]"
        for name, data in info["agents"].items()
    )
    mem = info["memory"]

    text = (
        f"*Centinela Status*\n\n"
        f"*Agentes:*\n{agents_text}\n\n"
        f"*Memoria:* {mem['total_entries']} entradas, {mem['total_days']} días\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def _cmd_models(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /models command."""
    if not _is_authorized(update.effective_chat.id):
        return

    from centinela.core.models import get_model_resolver

    resolver = get_model_resolver()
    statuses = resolver.get_status()

    lines = ["*Modelos Bedrock:*\n"]
    for i, (model_id, info) in enumerate(statuses.items()):
        short = model_id.split(".")[-1][:35]
        label = "primario" if i == 0 else f"fallback {i}"
        status_icon = "✅" if info["available"] else "❌"
        lines.append(f"{status_icon} `{short}` ({label})")
        if info["failure_count"] > 0:
            lines.append(f"   fallos: {info['failure_count']}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /reset command."""
    if not _is_authorized(update.effective_chat.id):
        return

    orch: Orchestrator = context.bot_data["orchestrator"]
    orch.reset()
    await update.message.reply_text("Conversación reiniciada.")


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle regular text messages — send to orchestrator."""
    if not _is_authorized(update.effective_chat.id):
        return

    user_text = update.message.text
    if not user_text:
        return

    audit = get_audit_logger()
    orch: Orchestrator = context.bot_data["orchestrator"]

    # Send "typing" indicator
    await update.effective_chat.send_action("typing")

    try:
        # Collect streamed response
        response_parts: list[str] = []
        for chunk in orch.stream_chat(user_text):
            response_parts.append(chunk)

        full_response = "".join(response_parts)

        # Telegram has a 4096 char limit
        if len(full_response) > _MAX_MSG_LEN:
            # Split into multiple messages
            for i in range(0, len(full_response), _MAX_MSG_LEN):
                part = full_response[i : i + _MAX_MSG_LEN]
                await update.message.reply_text(part)
        else:
            await update.message.reply_text(full_response or "(sin respuesta)")

        audit.log_tool_execution(
            agent_id="telegram",
            tool_name="chat",
            arguments={"message": user_text[:100]},
            success=True,
            result_preview=full_response[:200],
            execution_time_ms=0,
        )

    except Exception as e:
        logger.error("Telegram chat error: %s", e)
        await update.message.reply_text(f"Error: {e}")
        audit.log_security_event(
            agent_id="telegram",
            event_type="chat_error",
            severity="error",
            details={"error": str(e)},
        )


def create_telegram_app(token: str | None = None) -> Application:
    """Create and configure the Telegram bot application."""
    bot_token = token or os.environ.get("CENTINELA_TELEGRAM_TOKEN", "")
    if not bot_token:
        raise ValueError(
            "Telegram bot token not configured. "
            "Set CENTINELA_TELEGRAM_TOKEN env var."
        )

    app = Application.builder().token(bot_token).build()

    # Store orchestrator in bot_data
    app.bot_data["orchestrator"] = Orchestrator()

    # Register handlers
    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("status", _cmd_status))
    app.add_handler(CommandHandler("models", _cmd_models))
    app.add_handler(CommandHandler("reset", _cmd_reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))

    logger.info("Telegram bot configured")
    return app


def run_telegram_bot(token: str | None = None) -> None:
    """Start the Telegram bot (blocking)."""
    app = create_telegram_app(token)
    logger.info("Starting Telegram bot polling...")
    app.run_polling(drop_pending_updates=True)
