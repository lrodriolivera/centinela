"""Structured audit logging with automatic secret redaction.

Every tool execution, approval decision, and security event is logged
as structured JSON for compliance, debugging, and incident response.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from centinela.core.config import get_config

# Secret patterns to redact
_SECRET_PATTERNS = [
    re.compile(r"(password|passwd|pwd)\s*[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"(token|api_key|apikey|secret|credential)\s*[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"(AKIA|ASIA)[A-Z0-9]{16,}", re.IGNORECASE),  # AWS access keys
    re.compile(r"(sk-[a-zA-Z0-9]{20,})", re.IGNORECASE),  # API keys
    re.compile(r"(ghp_[a-zA-Z0-9]{36})", re.IGNORECASE),  # GitHub tokens
    re.compile(r"(Bearer\s+[a-zA-Z0-9._-]+)", re.IGNORECASE),  # Bearer tokens
    re.compile(r"-----BEGIN [A-Z]+ KEY-----", re.IGNORECASE),  # PEM keys
]


def redact_secrets(text: str) -> str:
    """Redact known secret patterns from text."""
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("***REDACTED***", result)
    return result


def hash_arguments(args: dict[str, Any]) -> str:
    """Create a SHA-256 hash of arguments for audit trail without exposing values."""
    serialized = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


@dataclass
class AuditEntry:
    """A single audit log entry."""

    timestamp: str
    event_type: str
    agent_id: str
    details: dict[str, Any] = field(default_factory=dict)
    severity: str = "info"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, ensure_ascii=False)


class AuditLogger:
    """Writes structured audit logs with secret redaction."""

    def __init__(self, log_dir: str | Path | None = None, redact: bool = True):
        config = get_config()
        self.log_dir = Path(log_dir or config.security.audit.log_dir).expanduser().resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.redact = redact if redact is not None else config.security.audit.redact_secrets
        self._log_file = self.log_dir / "audit.jsonl"
        self._logger = structlog.get_logger("centinela.audit")

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _write(self, entry: AuditEntry) -> None:
        """Append entry to JSONL audit log."""
        line = entry.to_json()
        if self.redact:
            line = redact_secrets(line)

        with open(self._log_file, "a") as f:
            f.write(line + "\n")

        # Also log via structlog for console/file logging
        self._logger.info(
            entry.event_type,
            agent_id=entry.agent_id,
            severity=entry.severity,
            **entry.details,
        )

    def log_tool_execution(
        self,
        agent_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        success: bool,
        result_preview: str = "",
        error: str | None = None,
        execution_time_ms: int = 0,
    ) -> None:
        """Log a tool execution."""
        entry = AuditEntry(
            timestamp=self._now(),
            event_type="tool_execution",
            agent_id=agent_id,
            severity="info" if success else "warning",
            details={
                "tool_name": tool_name,
                "arguments_hash": hash_arguments(arguments),
                "success": success,
                "result_preview": result_preview[:200] if result_preview else "",
                "error": error,
                "execution_time_ms": execution_time_ms,
            },
        )
        self._write(entry)

    def log_approval_decision(
        self,
        agent_id: str,
        tool_name: str,
        command: str,
        decision: str,
        decided_by: str,
    ) -> None:
        """Log an approval decision."""
        entry = AuditEntry(
            timestamp=self._now(),
            event_type="approval_decision",
            agent_id=agent_id,
            severity="info" if decision == "approved" else "warning",
            details={
                "tool_name": tool_name,
                "command": redact_secrets(command) if self.redact else command,
                "decision": decision,
                "decided_by": decided_by,
            },
        )
        self._write(entry)

    def log_security_event(
        self,
        agent_id: str,
        event_type: str,
        severity: str = "warning",
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log a security-relevant event (blocked command, auth failure, etc.)."""
        entry = AuditEntry(
            timestamp=self._now(),
            event_type=f"security:{event_type}",
            agent_id=agent_id,
            severity=severity,
            details=details or {},
        )
        self._write(entry)

    def log_model_invocation(
        self,
        agent_id: str,
        model_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
        fallback_used: bool = False,
    ) -> None:
        """Log an LLM model invocation."""
        entry = AuditEntry(
            timestamp=self._now(),
            event_type="model_invocation",
            agent_id=agent_id,
            details={
                "model_id": model_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
                "fallback_used": fallback_used,
            },
        )
        self._write(entry)

    def get_recent(self, limit: int = 50) -> list[dict]:
        """Read recent audit entries from the log file."""
        if not self._log_file.exists():
            return []

        entries = []
        with open(self._log_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        return entries[-limit:]


# Global instance
_audit: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    global _audit
    if _audit is None:
        _audit = AuditLogger()
    return _audit
