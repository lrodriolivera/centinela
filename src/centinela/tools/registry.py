"""Tool registry with permission-aware registration.

Tools are registered with a decorator and include metadata for
the security layer (permission tier, requires_approval flag).
"""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class PermissionTier(str, Enum):
    """Permission levels for tool execution."""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    ADMIN = "admin"


@dataclass
class ToolDefinition:
    """Metadata and callable for a registered tool."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable
    permission: PermissionTier = PermissionTier.READ
    requires_approval: bool = False
    tags: list[str] = field(default_factory=list)

    def to_bedrock_spec(self) -> dict:
        """Convert to Bedrock tool specification format."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    """Central registry for all agent tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str | None = None,
        description: str = "",
        permission: PermissionTier = PermissionTier.READ,
        requires_approval: bool = False,
        tags: list[str] | None = None,
    ) -> Callable:
        """Decorator to register a function as an agent tool.

        Usage:
            @registry.register(
                name="read_file",
                description="Read contents of a file",
                permission=PermissionTier.READ,
            )
            def read_file(path: str) -> str:
                ...
        """

        def decorator(func: Callable) -> Callable:
            tool_name = name or func.__name__
            tool_desc = description or func.__doc__ or ""

            # Build JSON schema from type hints
            params = _extract_parameters(func)

            tool = ToolDefinition(
                name=tool_name,
                description=tool_desc.strip(),
                parameters=params,
                handler=func,
                permission=permission,
                requires_approval=requires_approval,
                tags=tags or [],
            )

            self._tools[tool_name] = tool
            logger.debug("Registered tool: %s (permission=%s)", tool_name, permission.value)
            return func

        return decorator

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(
        self,
        max_permission: PermissionTier | None = None,
        tags: list[str] | None = None,
    ) -> list[ToolDefinition]:
        """List tools filtered by permission level and/or tags."""
        tier_order = [PermissionTier.READ, PermissionTier.WRITE, PermissionTier.EXECUTE, PermissionTier.ADMIN]
        tools = list(self._tools.values())

        if max_permission is not None:
            max_idx = tier_order.index(max_permission)
            tools = [t for t in tools if tier_order.index(t.permission) <= max_idx]

        if tags:
            tag_set = set(tags)
            tools = [t for t in tools if tag_set.intersection(t.tags)]

        return tools

    def get_bedrock_specs(
        self,
        max_permission: PermissionTier | None = None,
    ) -> list[dict]:
        """Get tool specs in Bedrock format for model invocation."""
        return [t.to_bedrock_spec() for t in self.list_tools(max_permission=max_permission)]

    async def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        """Execute a tool by name with given arguments."""
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")

        handler = tool.handler
        if inspect.iscoroutinefunction(handler):
            return await handler(**arguments)
        else:
            return handler(**arguments)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


def _extract_parameters(func: Callable) -> dict[str, Any]:
    """Extract JSON schema from function type hints."""
    sig = inspect.signature(func)
    hints = func.__annotations__ if hasattr(func, "__annotations__") else {}

    properties: dict[str, Any] = {}
    required: list[str] = []

    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue

        hint = hints.get(param_name, str)
        # Handle Optional types
        origin = getattr(hint, "__origin__", None)
        if origin is not None:
            hint = getattr(hint, "__args__", (str,))[0]

        json_type = type_map.get(hint, "string")
        properties[param_name] = {"type": json_type}

        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required

    return schema


# Global registry instance
_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """Get or create the global tool registry."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
