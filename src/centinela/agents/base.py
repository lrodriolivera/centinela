"""Base agent class for Centinela.

All specialized agents inherit from BaseAgent. Provides:
- System prompt management
- Tool use loop (invoke model → execute tools → re-invoke)
- Streaming support
- Conversation history management
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from centinela.core.config import CentinelaConfig, get_config
from centinela.core.models import ModelResolver, ModelResponse, StreamChunk, get_model_resolver
from centinela.tools.registry import ToolRegistry, get_tool_registry, PermissionTier

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = """\
Eres Centinela, un agente IA autónomo con seguridad de grado empresarial.
Ejecutas tareas de manera precisa, segura y eficiente.

Reglas fundamentales:
- Nunca ejecutes acciones destructivas sin confirmación explícita del usuario.
- Nunca accedas a archivos fuera del workspace autorizado.
- Nunca expongas credenciales, tokens o secretos en tus respuestas.
- Si no estás seguro de una acción, pregunta antes de ejecutar.
- Reporta siempre qué herramientas usaste y por qué.
"""


@dataclass
class ConversationMessage:
    """A message in the conversation history."""

    role: str  # "user", "assistant", "system", "tool"
    content: str | list[dict]
    tool_use_id: str | None = None
    name: str | None = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_use_id:
            msg["tool_use_id"] = self.tool_use_id
        if self.name:
            msg["name"] = self.name
        return msg


class BaseAgent:
    """Base class for all Centinela agents.

    Implements the core agent loop:
    1. Send messages + available tools to model
    2. If model returns tool calls, execute them
    3. Feed tool results back to model
    4. Repeat until model returns text (no tool calls)
    """

    name: str = "base"
    description: str = "Base agent"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    max_tool_rounds: int = 10
    permission_tier: PermissionTier = PermissionTier.READ

    def __init__(
        self,
        config: CentinelaConfig | None = None,
        model_resolver: ModelResolver | None = None,
        tool_registry: ToolRegistry | None = None,
        model: str | None = None,
    ):
        self.config = config or get_config()
        self.resolver = model_resolver or get_model_resolver()
        self.tools = tool_registry or get_tool_registry()
        self.model = model
        self.history: list[ConversationMessage] = []
        self._setup()

    def _setup(self) -> None:
        """Override in subclasses to register tools or customize behavior."""
        pass

    def _get_system_messages(self) -> list[dict]:
        """Build system messages for the model."""
        return [{"role": "system", "content": self.system_prompt}]

    def _get_tool_specs(self) -> list[dict] | None:
        """Get tool specifications available to this agent."""
        specs = self.tools.get_bedrock_specs(max_permission=self.permission_tier)
        return specs if specs else None

    def _build_messages(self, user_input: str | None = None) -> list[dict]:
        """Build the full message list for the model."""
        messages = self._get_system_messages()
        for msg in self.history:
            messages.append(msg.to_dict())
        if user_input:
            messages.append({"role": "user", "content": user_input})
        return messages

    async def _execute_tool(self, tool_call: dict) -> str:
        """Execute a single tool call and return the result as string."""
        tool_name = tool_call.get("name", "")
        tool_input = tool_call.get("input", {})
        tool_id = tool_call.get("toolUseId", "")

        tool_def = self.tools.get(tool_name)
        if tool_def is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        # Check if tool requires approval
        if tool_def.requires_approval:
            logger.info("Tool '%s' requires approval — skipping in base agent", tool_name)
            return json.dumps({
                "error": "Tool requires human approval",
                "tool": tool_name,
                "args": tool_input,
            })

        try:
            result = await self.tools.execute(tool_name, tool_input)
            if not isinstance(result, str):
                result = json.dumps(result, default=str)
            return result
        except Exception as e:
            logger.error("Tool '%s' failed: %s", tool_name, e)
            return json.dumps({"error": str(e)})

    def chat(self, user_input: str) -> str:
        """Synchronous single-turn chat with tool use loop."""
        self.history.append(ConversationMessage(role="user", content=user_input))

        messages = self._build_messages()
        tool_specs = self._get_tool_specs()

        for round_num in range(self.max_tool_rounds):
            response = self.resolver.invoke(
                messages=messages,
                model=self.model,
                tools=tool_specs,
            )

            if not response.tool_calls:
                # No tool calls — final answer
                self.history.append(
                    ConversationMessage(role="assistant", content=response.text)
                )
                return response.text

            # Process tool calls
            assistant_content: list[dict] = []
            if response.text:
                assistant_content.append({"text": response.text})
            for tc in response.tool_calls:
                assistant_content.append({"toolUse": tc})

            messages.append({"role": "assistant", "content": assistant_content})

            # Execute tools and collect results
            tool_results: list[dict] = []
            for tc in response.tool_calls:
                import asyncio
                result = asyncio.get_event_loop().run_until_complete(
                    self._execute_tool(tc)
                )
                tool_results.append({
                    "toolResult": {
                        "toolUseId": tc["toolUseId"],
                        "content": [{"text": result}],
                    }
                })

            messages.append({"role": "user", "content": tool_results})

        return "[Centinela] Se alcanzó el límite de rondas de herramientas."

    def stream_chat(self, user_input: str) -> Iterator[str]:
        """Stream a response. Yields text chunks.

        Note: Tool use during streaming falls back to synchronous execution
        for the tool calls, then continues streaming the follow-up.
        """
        self.history.append(ConversationMessage(role="user", content=user_input))

        messages = self._build_messages()
        tool_specs = self._get_tool_specs()

        for round_num in range(self.max_tool_rounds):
            full_text = ""
            tool_calls: list[dict] = []
            stop_reason = ""

            for chunk in self.resolver.stream(
                messages=messages,
                model=self.model,
                tools=tool_specs,
            ):
                if chunk.text:
                    full_text += chunk.text
                    yield chunk.text
                if chunk.tool_use:
                    tool_calls.append(chunk.tool_use)
                if chunk.stop_reason:
                    stop_reason = chunk.stop_reason

            if not tool_calls:
                self.history.append(
                    ConversationMessage(role="assistant", content=full_text)
                )
                return

            # Build assistant message with tool calls
            assistant_content: list[dict] = []
            if full_text:
                assistant_content.append({"text": full_text})
            for tc in tool_calls:
                assistant_content.append({"toolUse": tc})

            messages.append({"role": "assistant", "content": assistant_content})

            # Execute tools
            tool_results: list[dict] = []
            for tc in tool_calls:
                import asyncio

                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop and loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        result = pool.submit(
                            asyncio.run, self._execute_tool(tc)
                        ).result()
                else:
                    result = asyncio.run(self._execute_tool(tc))

                tool_results.append({
                    "toolResult": {
                        "toolUseId": tc["toolUseId"],
                        "content": [{"text": result}],
                    }
                })

            messages.append({"role": "user", "content": tool_results})
            yield "\n"  # Visual separator between tool rounds

        yield "\n[Centinela] Se alcanzó el límite de rondas de herramientas."

    def reset(self) -> None:
        """Clear conversation history."""
        self.history.clear()
