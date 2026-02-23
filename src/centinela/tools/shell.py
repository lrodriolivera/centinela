"""Shell execution tool — integrates sandbox, policies, and approval.

Flow:
1. Command → Policy engine evaluates → ALLOWED / REQUIRES_APPROVAL / BLOCKED
2. If REQUIRES_APPROVAL → Human-in-the-loop prompt
3. If approved → Execute in Docker sandbox (or local fallback)
4. Audit log every execution and decision
"""

from __future__ import annotations

import logging
from typing import Any

from centinela.core.config import get_config
from centinela.security.approval import ApprovalStatus, get_approval_manager
from centinela.security.audit import get_audit_logger
from centinela.security.policies import CommandDecision, get_policy_engine
from centinela.security.sandbox import SandboxResult, get_sandbox
from centinela.tools.registry import PermissionTier, get_tool_registry

logger = logging.getLogger(__name__)

registry = get_tool_registry()


@registry.register(
    name="execute_command",
    description=(
        "Ejecuta un comando shell en un sandbox Docker aislado. "
        "Comandos seguros (ls, grep, git status) se ejecutan sin aprobación. "
        "Comandos peligrosos (rm, curl, pip) requieren aprobación del usuario. "
        "Comandos bloqueados (sudo, rm -rf /) son rechazados siempre."
    ),
    permission=PermissionTier.EXECUTE,
    requires_approval=False,  # Approval is handled internally by the policy engine
    tags=["shell", "execution"],
)
def execute_command(command: str) -> str:
    """Execute a shell command with full security pipeline."""
    config = get_config()
    policy_engine = get_policy_engine()
    approval_mgr = get_approval_manager()
    audit = get_audit_logger()
    sandbox = get_sandbox()

    # 1. Evaluate command against policy
    policy_result = policy_engine.evaluate(command)

    # 2. Handle BLOCKED commands
    if policy_result.decision == CommandDecision.BLOCKED:
        audit.log_security_event(
            agent_id="shell",
            event_type="blocked_command",
            severity="critical",
            details={
                "command": command,
                "reason": policy_result.reason,
                "rule": policy_result.matched_rule,
            },
        )
        return f"BLOQUEADO: {policy_result.reason}"

    # 3. Handle REQUIRES_APPROVAL
    if policy_result.decision == CommandDecision.REQUIRES_APPROVAL:
        status = approval_mgr.request_cli_approval(
            agent_id="shell",
            tool_name="execute_command",
            command=command,
            arguments={"command": command},
            reason=policy_result.reason,
        )

        audit.log_approval_decision(
            agent_id="shell",
            tool_name="execute_command",
            command=command,
            decision=status.value,
            decided_by="cli_user",
        )

        if status != ApprovalStatus.APPROVED:
            return f"Rechazado por el usuario: {command}"

    # 4. Execute in sandbox
    workspace = str(config.workspace_path)
    result: SandboxResult = sandbox.execute(
        command=command,
        workspace_path=workspace,
    )

    # 5. Audit log the execution
    audit.log_tool_execution(
        agent_id="shell",
        tool_name="execute_command",
        arguments={"command": command},
        success=result.success,
        result_preview=result.output[:200] if result.output else "",
        error=result.error,
        execution_time_ms=result.execution_time_ms,
    )

    # 6. Format output
    parts = []
    if result.output:
        parts.append(result.output)
    if result.error:
        parts.append(f"[stderr] {result.error}")
    if not result.success:
        parts.append(f"[exit code: {result.exit_code}]")

    output = "\n".join(parts) if parts else "(sin output)"
    return output
