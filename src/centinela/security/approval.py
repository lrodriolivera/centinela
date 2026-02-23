"""Human-in-the-loop approval workflow.

Before executing sensitive actions, the agent pauses and asks
the user for explicit confirmation. Supports CLI prompts and
async approval via API (for web/messaging interfaces).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


@dataclass
class ApprovalRequest:
    """A pending approval request."""

    request_id: str
    agent_id: str
    tool_name: str
    command: str
    arguments: dict[str, Any]
    reason: str
    created_at: float = field(default_factory=time.time)
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_at: float | None = None
    decided_by: str | None = None

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at


class ApprovalManager:
    """Manages approval requests for sensitive agent actions."""

    def __init__(self, timeout_seconds: int = 300):
        self.timeout_seconds = timeout_seconds
        self._pending: dict[str, ApprovalRequest] = {}
        self._history: list[ApprovalRequest] = []
        self._cli_prompt: Callable | None = None

    def create_request(
        self,
        agent_id: str,
        tool_name: str,
        command: str,
        arguments: dict[str, Any],
        reason: str,
    ) -> ApprovalRequest:
        """Create a new approval request."""
        request = ApprovalRequest(
            request_id=str(uuid.uuid4())[:8],
            agent_id=agent_id,
            tool_name=tool_name,
            command=command,
            arguments=arguments,
            reason=reason,
        )
        self._pending[request.request_id] = request
        logger.info(
            "Approval request %s: agent=%s tool=%s cmd='%s'",
            request.request_id, agent_id, tool_name, command[:80],
        )
        return request

    def approve(self, request_id: str, decided_by: str = "user") -> bool:
        """Approve a pending request."""
        request = self._pending.pop(request_id, None)
        if request is None:
            return False
        request.status = ApprovalStatus.APPROVED
        request.decided_at = time.time()
        request.decided_by = decided_by
        self._history.append(request)
        logger.info("Request %s APPROVED by %s", request_id, decided_by)
        return True

    def reject(self, request_id: str, decided_by: str = "user") -> bool:
        """Reject a pending request."""
        request = self._pending.pop(request_id, None)
        if request is None:
            return False
        request.status = ApprovalStatus.REJECTED
        request.decided_at = time.time()
        request.decided_by = decided_by
        self._history.append(request)
        logger.info("Request %s REJECTED by %s", request_id, decided_by)
        return True

    def request_cli_approval(
        self,
        agent_id: str,
        tool_name: str,
        command: str,
        arguments: dict[str, Any],
        reason: str,
    ) -> ApprovalStatus:
        """Request approval via CLI prompt (blocking).

        Displays the action details and waits for user input.
        """
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text

        console = Console()

        request = self.create_request(agent_id, tool_name, command, arguments, reason)

        # Display the request
        detail = Text()
        detail.append("Herramienta: ", style="bold")
        detail.append(f"{tool_name}\n")
        detail.append("Comando: ", style="bold")
        detail.append(f"{command}\n", style="yellow")
        detail.append("Razón: ", style="bold")
        detail.append(f"{reason}\n")
        if arguments:
            detail.append("Argumentos: ", style="bold")
            detail.append(f"{arguments}\n", style="dim")

        console.print()
        console.print(Panel(
            detail,
            title="[bold yellow]Aprobación Requerida[/]",
            border_style="yellow",
        ))

        try:
            response = console.input(
                "[bold yellow]¿Aprobar? [/]([green]s[/]/[red]n[/]): "
            ).strip().lower()
        except (KeyboardInterrupt, EOFError):
            response = "n"

        if response in ("s", "si", "sí", "y", "yes"):
            self.approve(request.request_id, decided_by="cli_user")
            console.print("[green]Aprobado.[/]")
            return ApprovalStatus.APPROVED
        else:
            self.reject(request.request_id, decided_by="cli_user")
            console.print("[red]Rechazado.[/]")
            return ApprovalStatus.REJECTED

    def get_pending(self) -> list[ApprovalRequest]:
        """Get all pending requests (for API/Web UI)."""
        # Clean expired
        expired = [
            rid for rid, req in self._pending.items()
            if req.age_seconds > self.timeout_seconds
        ]
        for rid in expired:
            req = self._pending.pop(rid)
            req.status = ApprovalStatus.TIMEOUT
            req.decided_at = time.time()
            self._history.append(req)

        return list(self._pending.values())

    def get_history(self, limit: int = 50) -> list[ApprovalRequest]:
        """Get recent approval history."""
        return self._history[-limit:]


# Global instance
_manager: ApprovalManager | None = None


def get_approval_manager() -> ApprovalManager:
    global _manager
    if _manager is None:
        _manager = ApprovalManager()
    return _manager
