"""Tests for the tool registry."""

import pytest

from centinela.tools.registry import PermissionTier, ToolDefinition, ToolRegistry


class TestToolRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()

        @registry.register(name="test_tool", description="A test tool")
        def test_tool(message: str) -> str:
            return f"echo: {message}"

        tool = registry.get("test_tool")
        assert tool is not None
        assert tool.name == "test_tool"
        assert tool.description == "A test tool"

    def test_register_with_permission(self):
        registry = ToolRegistry()

        @registry.register(
            name="danger",
            description="Dangerous",
            permission=PermissionTier.ADMIN,
            requires_approval=True,
        )
        def danger() -> str:
            return "boom"

        tool = registry.get("danger")
        assert tool.permission == PermissionTier.ADMIN
        assert tool.requires_approval is True

    def test_list_by_permission(self):
        registry = ToolRegistry()

        @registry.register(name="reader", permission=PermissionTier.READ)
        def reader() -> str:
            return ""

        @registry.register(name="writer", permission=PermissionTier.WRITE)
        def writer() -> str:
            return ""

        @registry.register(name="execer", permission=PermissionTier.EXECUTE)
        def execer() -> str:
            return ""

        read_only = registry.list_tools(max_permission=PermissionTier.READ)
        assert len(read_only) == 1
        assert read_only[0].name == "reader"

        up_to_write = registry.list_tools(max_permission=PermissionTier.WRITE)
        assert len(up_to_write) == 2

    def test_bedrock_spec(self):
        registry = ToolRegistry()

        @registry.register(name="greet", description="Say hello")
        def greet(name: str) -> str:
            return f"Hello {name}"

        specs = registry.get_bedrock_specs()
        assert len(specs) == 1
        assert specs[0]["name"] == "greet"
        assert "parameters" in specs[0]

    @pytest.mark.asyncio
    async def test_execute(self):
        registry = ToolRegistry()

        @registry.register(name="add")
        def add(a: int, b: int) -> int:
            return a + b

        result = await registry.execute("add", {"a": 2, "b": 3})
        assert result == 5

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        registry = ToolRegistry()
        with pytest.raises(ValueError, match="Unknown tool"):
            await registry.execute("nonexistent", {})

    def test_contains(self):
        registry = ToolRegistry()

        @registry.register(name="exists")
        def exists() -> str:
            return ""

        assert "exists" in registry
        assert "nope" not in registry

    def test_len(self):
        registry = ToolRegistry()
        assert len(registry) == 0

        @registry.register(name="one")
        def one() -> str:
            return ""

        assert len(registry) == 1

    def test_parameter_extraction(self):
        registry = ToolRegistry()

        @registry.register(name="complex")
        def complex_func(path: str, count: int, verbose: bool = False) -> str:
            return ""

        tool = registry.get("complex")
        params = tool.parameters
        assert params["properties"]["path"]["type"] == "string"
        assert params["properties"]["count"]["type"] == "integer"
        assert params["properties"]["verbose"]["type"] == "boolean"
        assert "path" in params["required"]
        assert "count" in params["required"]
        assert "verbose" not in params.get("required", [])
