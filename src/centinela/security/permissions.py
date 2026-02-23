"""Permission system with tiered access control.

Four tiers enforce least-privilege for agents and tools:
  READ    — View files, query data, inspect state
  WRITE   — Create/modify files within workspace
  EXECUTE — Run shell commands, invoke external services
  ADMIN   — System operations, config changes, credential access
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from centinela.tools.registry import PermissionTier

logger = logging.getLogger(__name__)


@dataclass
class AgentPermissions:
    """Permissions granted to a specific agent."""

    agent_id: str
    tiers: set[PermissionTier] = field(default_factory=lambda: {PermissionTier.READ})
    allowed_tools: set[str] = field(default_factory=set)  # Empty = all within tier
    denied_tools: set[str] = field(default_factory=set)

    def has_tier(self, tier: PermissionTier) -> bool:
        return tier in self.tiers

    def can_use_tool(self, tool_name: str, required_tier: PermissionTier) -> bool:
        """Check if agent can use a specific tool."""
        if not self.has_tier(required_tier):
            return False
        if tool_name in self.denied_tools:
            return False
        if self.allowed_tools and tool_name not in self.allowed_tools:
            return False
        return True


# Default permission profiles
PROFILES: dict[str, set[PermissionTier]] = {
    "readonly": {PermissionTier.READ},
    "coding": {PermissionTier.READ, PermissionTier.WRITE, PermissionTier.EXECUTE},
    "full": {PermissionTier.READ, PermissionTier.WRITE, PermissionTier.EXECUTE, PermissionTier.ADMIN},
}


class PermissionManager:
    """Manages agent permissions and access control decisions."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentPermissions] = {}

    def register_agent(
        self,
        agent_id: str,
        profile: str = "readonly",
        extra_tiers: set[PermissionTier] | None = None,
        allowed_tools: set[str] | None = None,
        denied_tools: set[str] | None = None,
    ) -> AgentPermissions:
        """Register an agent with a permission profile."""
        tiers = PROFILES.get(profile, PROFILES["readonly"]).copy()
        if extra_tiers:
            tiers.update(extra_tiers)

        perms = AgentPermissions(
            agent_id=agent_id,
            tiers=tiers,
            allowed_tools=allowed_tools or set(),
            denied_tools=denied_tools or set(),
        )
        self._agents[agent_id] = perms
        logger.debug("Agent '%s' registered with profile '%s': %s", agent_id, profile, tiers)
        return perms

    def check(self, agent_id: str, tool_name: str, required_tier: PermissionTier) -> bool:
        """Check if an agent has permission to use a tool.

        Returns False (deny) if agent is not registered.
        """
        perms = self._agents.get(agent_id)
        if perms is None:
            logger.warning("Permission check for unregistered agent '%s'", agent_id)
            return False
        return perms.can_use_tool(tool_name, required_tier)

    def get_agent(self, agent_id: str) -> AgentPermissions | None:
        return self._agents.get(agent_id)

    def grant_tier(self, agent_id: str, tier: PermissionTier) -> None:
        perms = self._agents.get(agent_id)
        if perms:
            perms.tiers.add(tier)
            logger.info("Granted %s to agent '%s'", tier.value, agent_id)

    def revoke_tier(self, agent_id: str, tier: PermissionTier) -> None:
        perms = self._agents.get(agent_id)
        if perms:
            perms.tiers.discard(tier)
            logger.info("Revoked %s from agent '%s'", tier.value, agent_id)

    def deny_tool(self, agent_id: str, tool_name: str) -> None:
        perms = self._agents.get(agent_id)
        if perms:
            perms.denied_tools.add(tool_name)


# Global instance
_manager: PermissionManager | None = None


def get_permission_manager() -> PermissionManager:
    global _manager
    if _manager is None:
        _manager = PermissionManager()
    return _manager
