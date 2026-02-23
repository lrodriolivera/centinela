"""Persistent memory system — semantic, episodic, and preferences.

Three layers:
1. Semantic: Qdrant vector search (via RAG tools) for indexed knowledge
2. Episodic: JSONL transcripts of all conversations
3. Preferences: YAML file with user preferences and learned patterns
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from centinela.core.config import CentinelaConfig, get_config

logger = logging.getLogger(__name__)


@dataclass
class TranscriptEntry:
    """A single entry in the conversation transcript."""

    timestamp: str
    role: str  # user, assistant, system, tool
    content: str
    agent_id: str = "base"
    model_id: str = ""
    tool_name: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


class EpisodicMemory:
    """Stores conversation transcripts as JSONL files.

    One file per day: transcripts/2026-02-23.jsonl
    """

    def __init__(self, config: CentinelaConfig | None = None):
        self.config = config or get_config()
        self.transcripts_dir = self.config.transcripts_path
        self.transcripts_dir.mkdir(parents=True, exist_ok=True)

    def _today_file(self) -> Path:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.transcripts_dir / f"{date_str}.jsonl"

    def record(
        self,
        role: str,
        content: str,
        agent_id: str = "base",
        model_id: str = "",
        tool_name: str = "",
        metadata: dict | None = None,
    ) -> None:
        """Record a conversation entry."""
        entry = TranscriptEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            role=role,
            content=content[:5000],  # Limit stored content
            agent_id=agent_id,
            model_id=model_id,
            tool_name=tool_name,
            metadata=metadata or {},
        )

        with open(self._today_file(), "a") as f:
            f.write(entry.to_json() + "\n")

    def get_recent(self, limit: int = 20) -> list[dict]:
        """Get recent transcript entries across all days."""
        entries: list[dict] = []

        # Read files in reverse date order
        files = sorted(self.transcripts_dir.glob("*.jsonl"), reverse=True)
        for fpath in files:
            with open(fpath) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            if len(entries) >= limit:
                break

        # Return most recent entries
        return entries[-limit:]

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Simple text search across transcripts."""
        query_lower = query.lower()
        results: list[dict] = []

        for fpath in sorted(self.transcripts_dir.glob("*.jsonl"), reverse=True):
            with open(fpath) as f:
                for line in f:
                    if query_lower in line.lower():
                        try:
                            results.append(json.loads(line.strip()))
                        except json.JSONDecodeError:
                            continue
                        if len(results) >= limit:
                            return results
        return results

    def get_stats(self) -> dict:
        """Get transcript statistics."""
        files = list(self.transcripts_dir.glob("*.jsonl"))
        total_entries = 0
        total_bytes = 0
        for fpath in files:
            total_bytes += fpath.stat().st_size
            with open(fpath) as f:
                total_entries += sum(1 for _ in f)

        return {
            "total_days": len(files),
            "total_entries": total_entries,
            "total_size_mb": round(total_bytes / 1024 / 1024, 2),
            "path": str(self.transcripts_dir),
        }


class PreferencesMemory:
    """Stores user preferences as a YAML file.

    Learns patterns like preferred language, coding style,
    frequently used tools, etc.
    """

    def __init__(self, config: CentinelaConfig | None = None):
        self.config = config or get_config()
        self._path = self.config.transcripts_path.parent / "preferences.yaml"
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self._path.is_file():
            with open(self._path) as f:
                return yaml.safe_load(f) or {}
        return {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            yaml.dump(self._data, f, default_flow_style=False, allow_unicode=True)

    def get(self, key: str, default=None):
        """Get a preference value."""
        keys = key.split(".")
        data = self._data
        for k in keys:
            if isinstance(data, dict):
                data = data.get(k)
            else:
                return default
            if data is None:
                return default
        return data

    def set(self, key: str, value) -> None:
        """Set a preference value (supports dot notation: 'ui.theme')."""
        keys = key.split(".")
        data = self._data
        for k in keys[:-1]:
            data = data.setdefault(k, {})
        data[keys[-1]] = value
        self._save()

    def get_all(self) -> dict:
        return self._data.copy()


class MemoryManager:
    """Unified interface to all memory systems."""

    def __init__(self, config: CentinelaConfig | None = None):
        self.config = config or get_config()
        self.episodic = EpisodicMemory(self.config)
        self.preferences = PreferencesMemory(self.config)

    def record_interaction(
        self,
        role: str,
        content: str,
        agent_id: str = "base",
        model_id: str = "",
        tool_name: str = "",
    ) -> None:
        """Record a conversation interaction."""
        self.episodic.record(
            role=role,
            content=content,
            agent_id=agent_id,
            model_id=model_id,
            tool_name=tool_name,
        )

    def get_context(self, limit: int = 10) -> str:
        """Get recent context for injecting into system prompt."""
        recent = self.episodic.get_recent(limit=limit)
        if not recent:
            return ""

        parts = ["# Contexto reciente (memoria episódica)"]
        for entry in recent:
            role = entry.get("role", "?")
            content = entry.get("content", "")[:200]
            parts.append(f"[{role}] {content}")

        return "\n".join(parts)


# Global instance
_memory: MemoryManager | None = None


def get_memory_manager() -> MemoryManager:
    global _memory
    if _memory is None:
        _memory = MemoryManager()
    return _memory
