"""Command policy engine — allowlist/denylist for shell commands.

Enforces what commands the agent can run:
- ALLOWED: execute without asking (safe commands)
- REQUIRES_APPROVAL: ask user before executing
- BLOCKED: never execute, always reject
"""

from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class CommandDecision(str, Enum):
    ALLOWED = "allowed"
    REQUIRES_APPROVAL = "requires_approval"
    BLOCKED = "blocked"


@dataclass
class PolicyResult:
    """Result of a policy evaluation."""

    decision: CommandDecision
    reason: str
    matched_rule: str | None = None


# Default safe commands that never need approval
DEFAULT_SAFE_COMMANDS = frozenset({
    # Read-only filesystem
    "ls", "cat", "head", "tail", "less", "file", "stat", "wc",
    "grep", "awk", "sed", "cut", "sort", "uniq", "diff", "find",
    "which", "whereis", "type", "realpath", "basename", "dirname",
    # System info (read-only)
    "uname", "whoami", "id", "env", "printenv", "date", "uptime",
    "df", "du", "free",
    # Development (read operations)
    "git status", "git log", "git diff", "git branch", "git show",
    "python3 --version", "node --version", "npm --version",
    # Data processing
    "jq", "yq", "base64", "md5sum", "sha256sum",
    "echo", "printf", "true", "false", "test",
})

# Commands that require human approval
DEFAULT_APPROVAL_COMMANDS = frozenset({
    "rm", "rmdir", "mv", "cp",
    "curl", "wget", "httpie",
    "git push", "git remote", "git checkout", "git reset", "git rebase",
    "pip", "pip3", "npm install", "npm uninstall",
    "docker run", "docker exec", "docker-compose up",
    "make", "cmake",
    "python3", "python", "node",
    "chmod", "chown",
})

# Commands that are ALWAYS blocked
DEFAULT_BLOCKED_COMMANDS = frozenset({
    "sudo", "su",
    "chmod 777", "chmod -R 777",
    "dd", "mkfs", "fdisk", "parted",
    "shutdown", "reboot", "halt", "poweroff",
    "iptables", "ip6tables", "nft",
    "mount", "umount",
    "kill -9", "killall",
    "passwd", "useradd", "userdel", "usermod",
    "> /dev/sda", "rm -rf /", "rm -rf /*",
})

# Dangerous patterns (regex)
DEFAULT_BLOCKED_PATTERNS = [
    r"rm\s+-[rR]f?\s+/\s*$",           # rm -rf /
    r"rm\s+-[rR]f?\s+/\*",              # rm -rf /*
    r">\s*/dev/[sh]d",                   # Write to disk devices
    r"mkfs\.",                           # Format filesystems
    r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;:", # Fork bomb
    r"curl.*\|\s*bash",                  # Pipe curl to bash
    r"wget.*\|\s*bash",                  # Pipe wget to bash
    r"eval\s+.*\$\(",                    # eval with command substitution
    r"/etc/shadow",                      # Shadow file access
    r"/etc/passwd.*>>",                  # Passwd file modification
    r"\.ssh/",                           # SSH directory access
    r"\.aws/",                           # AWS credentials access
    r"\.gnupg/",                         # GPG keyring access
    r"\.centinela/.*\.yaml",             # Config file modification
]


class CommandPolicyEngine:
    """Evaluates shell commands against security policies."""

    def __init__(
        self,
        safe_commands: frozenset[str] | None = None,
        approval_commands: frozenset[str] | None = None,
        blocked_commands: frozenset[str] | None = None,
        blocked_patterns: list[str] | None = None,
    ):
        self.safe_commands = safe_commands or DEFAULT_SAFE_COMMANDS
        self.approval_commands = approval_commands or DEFAULT_APPROVAL_COMMANDS
        self.blocked_commands = blocked_commands or DEFAULT_BLOCKED_COMMANDS
        self.blocked_patterns = [
            re.compile(p) for p in (blocked_patterns or DEFAULT_BLOCKED_PATTERNS)
        ]

    def evaluate(self, command: str) -> PolicyResult:
        """Evaluate a command and return the policy decision."""
        command = command.strip()
        if not command:
            return PolicyResult(
                decision=CommandDecision.BLOCKED,
                reason="Comando vacío",
            )

        # 1. Check blocked patterns first (highest priority)
        for pattern in self.blocked_patterns:
            if pattern.search(command):
                return PolicyResult(
                    decision=CommandDecision.BLOCKED,
                    reason=f"Patrón peligroso detectado: {pattern.pattern}",
                    matched_rule=f"pattern:{pattern.pattern}",
                )

        # 2. Check exact blocked commands
        for blocked in self.blocked_commands:
            if command == blocked or command.startswith(blocked + " "):
                return PolicyResult(
                    decision=CommandDecision.BLOCKED,
                    reason=f"Comando bloqueado: {blocked}",
                    matched_rule=f"blocked:{blocked}",
                )

        # 3. Extract the base command (first word or first two words)
        try:
            parts = shlex.split(command)
        except ValueError:
            parts = command.split()

        if not parts:
            return PolicyResult(
                decision=CommandDecision.BLOCKED,
                reason="No se pudo parsear el comando",
            )

        base_cmd = parts[0]
        two_word_cmd = f"{parts[0]} {parts[1]}" if len(parts) > 1 else ""

        # 4. Check safe commands (exact match on base or two-word)
        if base_cmd in self.safe_commands or two_word_cmd in self.safe_commands:
            matched = two_word_cmd if two_word_cmd in self.safe_commands else base_cmd
            return PolicyResult(
                decision=CommandDecision.ALLOWED,
                reason=f"Comando seguro: {matched}",
                matched_rule=f"safe:{matched}",
            )

        # 5. Check approval commands
        if base_cmd in self.approval_commands or two_word_cmd in self.approval_commands:
            matched = two_word_cmd if two_word_cmd in self.approval_commands else base_cmd
            return PolicyResult(
                decision=CommandDecision.REQUIRES_APPROVAL,
                reason=f"Requiere aprobación: {matched}",
                matched_rule=f"approval:{matched}",
            )

        # 6. Default: require approval for unknown commands
        return PolicyResult(
            decision=CommandDecision.REQUIRES_APPROVAL,
            reason=f"Comando desconocido: {base_cmd}",
            matched_rule="default:unknown",
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> CommandPolicyEngine:
        """Load policies from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f) or {}

        policies = data.get("policies", {})
        safe = frozenset(policies.get("safe_commands", DEFAULT_SAFE_COMMANDS))
        approval = frozenset(policies.get("approval_commands", DEFAULT_APPROVAL_COMMANDS))
        blocked = frozenset(policies.get("blocked_commands", DEFAULT_BLOCKED_COMMANDS))
        patterns = policies.get("blocked_patterns", DEFAULT_BLOCKED_PATTERNS)

        return cls(
            safe_commands=safe,
            approval_commands=approval,
            blocked_commands=blocked,
            blocked_patterns=patterns,
        )


# Global instance
_engine: CommandPolicyEngine | None = None


def get_policy_engine() -> CommandPolicyEngine:
    global _engine
    if _engine is None:
        _engine = CommandPolicyEngine()
    return _engine
