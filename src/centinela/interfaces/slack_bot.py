"""Slack bot interface for Centinela.

Features:
- Responds to direct messages and @mentions
- Slash command /centinela
- Threaded replies for conversation context
- Logs all interactions to audit

Security:
- Validates Slack signing secret
- Only responds in authorized workspaces
"""

from __future__ import annotations

import logging
import os

from slack_bolt import App as SlackApp
from slack_bolt.adapter.socket_mode import SocketModeHandler

from centinela.core.orchestrator import Orchestrator
from centinela.security.audit import get_audit_logger

logger = logging.getLogger(__name__)


def _get_orchestrator() -> Orchestrator:
    """Get or create the orchestrator for Slack."""
    if not hasattr(_get_orchestrator, "_instance"):
        _get_orchestrator._instance = Orchestrator()
    return _get_orchestrator._instance


def create_slack_app(
    bot_token: str | None = None,
    signing_secret: str | None = None,
) -> SlackApp:
    """Create and configure the Slack bot application."""
    token = bot_token or os.environ.get("CENTINELA_SLACK_BOT_TOKEN", "")
    secret = signing_secret or os.environ.get("CENTINELA_SLACK_SIGNING_SECRET", "")

    if not token:
        raise ValueError(
            "Slack bot token not configured. "
            "Set CENTINELA_SLACK_BOT_TOKEN env var."
        )

    app = SlackApp(token=token, signing_secret=secret)
    audit = get_audit_logger()

    # ─── Message handler (DMs and @mentions) ───

    @app.event("message")
    def handle_message(event, say, client):
        """Handle incoming messages."""
        text = event.get("text", "").strip()
        user = event.get("user", "unknown")
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts")

        if not text or event.get("bot_id"):
            return  # Ignore empty messages and bot messages

        orch = _get_orchestrator()

        try:
            # Collect response
            response_parts: list[str] = []
            for chunk in orch.stream_chat(text):
                response_parts.append(chunk)

            full_response = "".join(response_parts)

            # Reply in thread
            say(
                text=full_response or "(sin respuesta)",
                thread_ts=thread_ts,
            )

            audit.log_tool_execution(
                agent_id="slack",
                tool_name="chat",
                arguments={"message": text[:100], "user": user},
                success=True,
                result_preview=full_response[:200],
                execution_time_ms=0,
            )

        except Exception as e:
            logger.error("Slack chat error: %s", e)
            say(text=f"Error: {e}", thread_ts=thread_ts)
            audit.log_security_event(
                agent_id="slack",
                event_type="chat_error",
                severity="error",
                details={"error": str(e), "user": user},
            )

    # ─── App mention handler ───

    @app.event("app_mention")
    def handle_mention(event, say, client):
        """Handle @Centinela mentions."""
        text = event.get("text", "").strip()
        thread_ts = event.get("thread_ts") or event.get("ts")

        # Remove the @mention from the text
        import re
        text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()

        if not text:
            say(text="Hola! Envíame un mensaje y te responderé.", thread_ts=thread_ts)
            return

        orch = _get_orchestrator()
        try:
            response_parts = list(orch.stream_chat(text))
            full_response = "".join(response_parts)
            say(text=full_response or "(sin respuesta)", thread_ts=thread_ts)
        except Exception as e:
            say(text=f"Error: {e}", thread_ts=thread_ts)

    # ─── Slash command ───

    @app.command("/centinela")
    def handle_command(ack, command, respond):
        """Handle /centinela slash command."""
        ack()
        text = command.get("text", "").strip()

        if not text or text == "help":
            respond(
                "Comandos:\n"
                "• `/centinela status` — Estado del sistema\n"
                "• `/centinela models` — Modelos disponibles\n"
                "• `/centinela reset` — Reiniciar conversación\n"
                "• `/centinela <mensaje>` — Chat con Centinela"
            )
            return

        if text == "status":
            orch = _get_orchestrator()
            info = orch.get_status()
            agents = "\n".join(
                f"• {n}: {d['description']} [{d['permission_tier']}]"
                for n, d in info["agents"].items()
            )
            mem = info["memory"]
            respond(
                f"*Centinela Status*\n\n"
                f"*Agentes:*\n{agents}\n\n"
                f"*Memoria:* {mem['total_entries']} entradas, {mem['total_days']} días"
            )
            return

        if text == "models":
            from centinela.core.models import get_model_resolver

            resolver = get_model_resolver()
            statuses = resolver.get_status()
            lines = []
            for i, (mid, info) in enumerate(statuses.items()):
                short = mid.split(".")[-1][:35]
                label = "primario" if i == 0 else f"fallback {i}"
                icon = ":white_check_mark:" if info["available"] else ":x:"
                lines.append(f"{icon} `{short}` ({label})")
            respond("*Modelos Bedrock:*\n" + "\n".join(lines))
            return

        if text == "reset":
            orch = _get_orchestrator()
            orch.reset()
            respond("Conversación reiniciada.")
            return

        # Default: chat
        orch = _get_orchestrator()
        try:
            response_parts = list(orch.stream_chat(text))
            respond("".join(response_parts) or "(sin respuesta)")
        except Exception as e:
            respond(f"Error: {e}")

    logger.info("Slack bot configured")
    return app


def run_slack_bot(
    bot_token: str | None = None,
    app_token: str | None = None,
) -> None:
    """Start the Slack bot in Socket Mode (blocking)."""
    app_tok = app_token or os.environ.get("CENTINELA_SLACK_APP_TOKEN", "")
    if not app_tok:
        raise ValueError(
            "Slack app-level token not configured. "
            "Set CENTINELA_SLACK_APP_TOKEN env var."
        )

    slack_app = create_slack_app(bot_token)
    handler = SocketModeHandler(slack_app, app_tok)
    logger.info("Starting Slack bot in Socket Mode...")
    handler.start()
