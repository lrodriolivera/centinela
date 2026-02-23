"""Model resolver for AWS Bedrock with fallback chain and streaming.

Supports:
- Bedrock Converse API with streaming
- Automatic fallback: Opus 4.5 → Sonnet 4.6 → Haiku 4.5
- Progressive cooldown for failed models
- Exponential backoff retry
- Tool use (function calling)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from centinela.core.config import CentinelaConfig, get_config

logger = logging.getLogger(__name__)


@dataclass
class ModelStatus:
    """Tracks the health of a model endpoint."""

    model_id: str
    available: bool = True
    cooldown_until: float = 0.0
    failure_count: int = 0
    last_error: str | None = None

    def is_available(self) -> bool:
        if not self.available:
            return False
        if self.cooldown_until > time.time():
            return False
        return True

    def record_failure(self, error: str, cooldown_config: Any) -> None:
        self.failure_count += 1
        self.last_error = error
        cooldown_secs = min(
            cooldown_config.initial_seconds * (cooldown_config.multiplier ** (self.failure_count - 1)),
            cooldown_config.max_seconds,
        )
        self.cooldown_until = time.time() + cooldown_secs
        logger.warning(
            "Model %s failed (%d): %s. Cooldown %ds",
            self.model_id, self.failure_count, error, cooldown_secs,
        )

    def record_success(self) -> None:
        self.failure_count = 0
        self.last_error = None
        self.cooldown_until = 0.0


@dataclass
class StreamChunk:
    """A chunk of streaming response."""

    text: str = ""
    tool_use: dict | None = None
    stop_reason: str | None = None
    usage: dict | None = None


@dataclass
class ModelResponse:
    """Complete model response."""

    text: str
    model_id: str
    tool_calls: list[dict] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)


class ModelResolver:
    """Manages LLM model selection, fallback, and invocation via Bedrock."""

    def __init__(self, config: CentinelaConfig | None = None):
        self.config = config or get_config()
        self._clients: dict[str, Any] = {}
        self._model_chain = self._build_model_chain()
        self._statuses: dict[str, ModelStatus] = {
            m: ModelStatus(model_id=m) for m in self._model_chain
        }

    def _build_model_chain(self) -> list[str]:
        """Build ordered list of models: primary + fallbacks."""
        chain = [self.config.models.primary]
        for fb in self.config.models.fallbacks:
            if fb not in chain:
                chain.append(fb)
        return chain

    def _get_client(self) -> Any:
        """Get or create the Bedrock runtime client."""
        region = self.config.models.region
        if region not in self._clients:
            boto_config = BotoConfig(
                region_name=region,
                retries={"max_attempts": 0},  # We handle retries ourselves
                read_timeout=120,
                connect_timeout=10,
            )
            session = boto3.Session(profile_name=self.config.models.aws_profile)
            self._clients[region] = session.client(
                "bedrock-runtime", config=boto_config
            )
        return self._clients[region]

    def resolve_model(self, model: str | None = None) -> str:
        """Resolve a model alias or name to a Bedrock model ID."""
        if model is None:
            model = self.config.models.primary

        # Check aliases
        resolved = self.config.models.aliases.get(model, model)
        return resolved

    def _select_available_model(self, preferred: str | None = None) -> str:
        """Select the best available model from the chain."""
        if preferred:
            resolved = self.resolve_model(preferred)
            status = self._statuses.get(resolved)
            if status and status.is_available():
                return resolved

        for model_id in self._model_chain:
            if self._statuses[model_id].is_available():
                return model_id

        # All in cooldown — use primary anyway (best effort)
        logger.warning("All models in cooldown, using primary as fallback")
        return self._model_chain[0]

    def _build_messages(self, messages: list[dict]) -> list[dict]:
        """Convert messages to Bedrock Converse format."""
        bedrock_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            if role == "system":
                continue  # System is passed separately
            content = msg.get("content", "")
            if isinstance(content, str):
                content = [{"text": content}]
            elif isinstance(content, list):
                bedrock_content = []
                for item in content:
                    if isinstance(item, str):
                        bedrock_content.append({"text": item})
                    elif isinstance(item, dict):
                        bedrock_content.append(item)
                content = bedrock_content
            bedrock_messages.append({"role": role, "content": content})
        return bedrock_messages

    def _extract_system(self, messages: list[dict]) -> list[dict] | None:
        """Extract system messages for Bedrock."""
        system_parts = []
        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if isinstance(content, str):
                    system_parts.append({"text": content})
        return system_parts if system_parts else None

    def _build_tool_config(self, tools: list[dict] | None) -> dict | None:
        """Convert tool definitions to Bedrock format."""
        if not tools:
            return None
        bedrock_tools = []
        for tool in tools:
            bedrock_tools.append({
                "toolSpec": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "inputSchema": {
                        "json": tool.get("parameters", {"type": "object", "properties": {}})
                    },
                }
            })
        return {"tools": bedrock_tools}

    def invoke(
        self,
        messages: list[dict],
        model: str | None = None,
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ModelResponse:
        """Invoke model synchronously with automatic fallback."""
        client = self._get_client()
        defaults = self.config.models.defaults
        max_tokens = max_tokens or defaults.max_tokens
        temperature = temperature if temperature is not None else defaults.temperature

        bedrock_messages = self._build_messages(messages)
        system = self._extract_system(messages)
        tool_config = self._build_tool_config(tools)

        last_error: Exception | None = None

        for attempt in range(self.config.models.retry.max_retries + 1):
            model_id = self._select_available_model(model if attempt == 0 else None)
            status = self._statuses[model_id]

            try:
                kwargs: dict[str, Any] = {
                    "modelId": model_id,
                    "messages": bedrock_messages,
                    "inferenceConfig": {
                        "maxTokens": max_tokens,
                        "temperature": temperature,
                    },
                }
                if system:
                    kwargs["system"] = system
                if tool_config:
                    kwargs["toolConfig"] = tool_config

                response = client.converse(**kwargs)
                status.record_success()

                # Parse response
                output = response.get("output", {})
                message = output.get("message", {})
                content_blocks = message.get("content", [])

                text_parts = []
                tool_calls = []
                for block in content_blocks:
                    if "text" in block:
                        text_parts.append(block["text"])
                    elif "toolUse" in block:
                        tool_calls.append(block["toolUse"])

                return ModelResponse(
                    text="\n".join(text_parts),
                    model_id=model_id,
                    tool_calls=tool_calls,
                    stop_reason=response.get("stopReason", ""),
                    usage=response.get("usage", {}),
                )

            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                error_msg = f"{error_code}: {e.response['Error']['Message']}"
                status.record_failure(error_msg, self.config.models.cooldown)
                last_error = e

                if error_code in ("ThrottlingException", "ServiceUnavailableException"):
                    backoff = min(
                        self.config.models.retry.backoff_base ** attempt,
                        self.config.models.retry.backoff_max,
                    )
                    logger.info("Retrying in %.1fs (attempt %d)", backoff, attempt + 1)
                    time.sleep(backoff)
                    continue
                elif error_code == "ValidationException":
                    raise
                else:
                    continue

            except Exception as e:
                status.record_failure(str(e), self.config.models.cooldown)
                last_error = e
                continue

        raise RuntimeError(
            f"All models exhausted after {self.config.models.retry.max_retries + 1} attempts. "
            f"Last error: {last_error}"
        )

    def stream(
        self,
        messages: list[dict],
        model: str | None = None,
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Iterator[StreamChunk]:
        """Stream model response with automatic fallback."""
        client = self._get_client()
        defaults = self.config.models.defaults
        max_tokens = max_tokens or defaults.max_tokens
        temperature = temperature if temperature is not None else defaults.temperature

        bedrock_messages = self._build_messages(messages)
        system = self._extract_system(messages)
        tool_config = self._build_tool_config(tools)

        last_error: Exception | None = None

        for attempt in range(self.config.models.retry.max_retries + 1):
            model_id = self._select_available_model(model if attempt == 0 else None)
            status = self._statuses[model_id]

            try:
                kwargs: dict[str, Any] = {
                    "modelId": model_id,
                    "messages": bedrock_messages,
                    "inferenceConfig": {
                        "maxTokens": max_tokens,
                        "temperature": temperature,
                    },
                }
                if system:
                    kwargs["system"] = system
                if tool_config:
                    kwargs["toolConfig"] = tool_config

                response = client.converse_stream(**kwargs)
                status.record_success()

                current_tool: dict | None = None

                for event in response["stream"]:
                    if "contentBlockStart" in event:
                        start = event["contentBlockStart"].get("start", {})
                        if "toolUse" in start:
                            current_tool = {
                                "toolUseId": start["toolUse"]["toolUseId"],
                                "name": start["toolUse"]["name"],
                                "input_json": "",
                            }

                    elif "contentBlockDelta" in event:
                        delta = event["contentBlockDelta"]["delta"]
                        if "text" in delta:
                            yield StreamChunk(text=delta["text"])
                        elif "toolUse" in delta and current_tool:
                            current_tool["input_json"] += delta["toolUse"].get("input", "")

                    elif "contentBlockStop" in event:
                        if current_tool:
                            try:
                                parsed_input = json.loads(current_tool["input_json"])
                            except json.JSONDecodeError:
                                parsed_input = {}
                            yield StreamChunk(tool_use={
                                "toolUseId": current_tool["toolUseId"],
                                "name": current_tool["name"],
                                "input": parsed_input,
                            })
                            current_tool = None

                    elif "messageStop" in event:
                        yield StreamChunk(
                            stop_reason=event["messageStop"].get("stopReason", "")
                        )

                    elif "metadata" in event:
                        yield StreamChunk(usage=event["metadata"].get("usage"))

                return  # Streaming completed successfully

            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                error_msg = f"{error_code}: {e.response['Error']['Message']}"
                status.record_failure(error_msg, self.config.models.cooldown)
                last_error = e

                if error_code in ("ThrottlingException", "ServiceUnavailableException"):
                    backoff = min(
                        self.config.models.retry.backoff_base ** attempt,
                        self.config.models.retry.backoff_max,
                    )
                    time.sleep(backoff)
                    continue
                elif error_code == "ValidationException":
                    raise
                else:
                    continue

            except Exception as e:
                status.record_failure(str(e), self.config.models.cooldown)
                last_error = e
                continue

        raise RuntimeError(
            f"All models exhausted during streaming. Last error: {last_error}"
        )

    def get_status(self) -> dict[str, dict]:
        """Get status of all models in the chain."""
        result = {}
        for model_id, status in self._statuses.items():
            result[model_id] = {
                "available": status.is_available(),
                "failure_count": status.failure_count,
                "last_error": status.last_error,
                "in_cooldown": status.cooldown_until > time.time(),
                "cooldown_remaining": max(0, int(status.cooldown_until - time.time())),
            }
        return result


# Module-level convenience
_resolver: ModelResolver | None = None


def get_model_resolver() -> ModelResolver:
    """Get or create the global model resolver."""
    global _resolver
    if _resolver is None:
        _resolver = ModelResolver()
    return _resolver


def reset_model_resolver() -> None:
    """Reset the global resolver (useful for testing)."""
    global _resolver
    _resolver = None
