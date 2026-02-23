"""Streaming helpers for SSE and WebSocket responses."""

from __future__ import annotations

import json
from typing import AsyncIterator, Iterator


def sse_encode(data: str, event: str | None = None) -> str:
    """Encode a Server-Sent Events message."""
    lines = []
    if event:
        lines.append(f"event: {event}")
    for line in data.split("\n"):
        lines.append(f"data: {line}")
    lines.append("")  # Trailing newline
    return "\n".join(lines) + "\n"


def sse_stream(chunks: Iterator[str]) -> Iterator[str]:
    """Convert text chunks into SSE stream format."""
    for chunk in chunks:
        yield sse_encode(json.dumps({"text": chunk}), event="chunk")
    yield sse_encode(json.dumps({"done": True}), event="done")


async def async_sse_stream(chunks: Iterator[str]) -> AsyncIterator[str]:
    """Async wrapper for SSE streaming."""
    for chunk in chunks:
        yield sse_encode(json.dumps({"text": chunk}), event="chunk")
    yield sse_encode(json.dumps({"done": True}), event="done")
