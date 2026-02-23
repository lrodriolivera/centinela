"""Centinela configuration system.

Loads configuration from centinela.yaml with environment variable overrides.
Environment variables use the CENTINELA_ prefix (e.g., CENTINELA_MODELS__REGION).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


# --- Sub-models ---


class IdentityConfig(BaseModel):
    name: str = "Centinela"
    version: str = "0.1.0"


class ModelDefaults(BaseModel):
    max_tokens: int = 8192
    temperature: float = 0.7
    top_p: float = 0.95


class RetryConfig(BaseModel):
    max_retries: int = 3
    backoff_base: float = 2.0
    backoff_max: float = 60.0


class CooldownConfig(BaseModel):
    initial_seconds: int = 60
    multiplier: int = 5
    max_seconds: int = 3600


class ModelsConfig(BaseModel):
    provider: str = "bedrock"
    region: str = "us-east-1"
    aws_profile: str = "bedrock"
    primary: str = "us.anthropic.claude-opus-4-5-20251101-v1:0"
    fallbacks: list[str] = Field(default_factory=lambda: [
        "us.anthropic.claude-sonnet-4-6",
        "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    ])
    aliases: dict[str, str] = Field(default_factory=dict)
    defaults: ModelDefaults = Field(default_factory=ModelDefaults)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    cooldown: CooldownConfig = Field(default_factory=CooldownConfig)


class GatewayAuthConfig(BaseModel):
    enabled: bool = True
    token_ttl_minutes: int = 15
    algorithm: str = "HS256"


class GatewayConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ])
    auth: GatewayAuthConfig = Field(default_factory=GatewayAuthConfig)


class SandboxConfig(BaseModel):
    enabled: bool = True
    image: str = "centinela-sandbox:latest"
    network: bool = False
    read_only: bool = True
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    timeout_seconds: int = 300


class PoliciesConfig(BaseModel):
    mode: str = "allowlist"


class AuditConfig(BaseModel):
    enabled: bool = True
    log_dir: str = "~/.centinela/logs"
    redact_secrets: bool = True
    retention_days: int = 30


class SecurityConfig(BaseModel):
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    policies: PoliciesConfig = Field(default_factory=PoliciesConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)


class QdrantConfig(BaseModel):
    path: str = "~/.centinela/memory/qdrant"


class TranscriptsConfig(BaseModel):
    path: str = "~/.centinela/memory/transcripts"


class MemoryConfig(BaseModel):
    backend: str = "qdrant"
    qdrant: QdrantConfig = Field(default_factory=QdrantConfig)
    transcripts: TranscriptsConfig = Field(default_factory=TranscriptsConfig)


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"


# --- Main config ---


class CentinelaConfig(BaseModel):
    """Root configuration for Centinela."""

    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    workspace: str = "~/Documentos/agente_IA_Personal"
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @property
    def workspace_path(self) -> Path:
        return Path(self.workspace).expanduser().resolve()

    @property
    def audit_log_dir(self) -> Path:
        return Path(self.security.audit.log_dir).expanduser().resolve()

    @property
    def qdrant_path(self) -> Path:
        return Path(self.memory.qdrant.path).expanduser().resolve()

    @property
    def transcripts_path(self) -> Path:
        return Path(self.memory.transcripts.path).expanduser().resolve()


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Apply CENTINELA_ environment variable overrides.

    CENTINELA_MODELS__REGION=us-west-2 → data["models"]["region"] = "us-west-2"
    """
    prefix = "CENTINELA_"
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        parts = key[len(prefix):].lower().split("__")
        target = data
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return data


def _find_config_file() -> Path | None:
    """Search for centinela.yaml in standard locations."""
    candidates = [
        Path.cwd() / "config" / "centinela.yaml",
        Path.cwd() / "centinela.yaml",
        Path.home() / ".centinela" / "centinela.yaml",
        Path("/etc/centinela/centinela.yaml"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_config(config_path: str | Path | None = None) -> CentinelaConfig:
    """Load Centinela configuration.

    Priority (highest to lowest):
    1. Environment variables (CENTINELA_ prefix)
    2. Config file specified by config_path or CENTINELA_CONFIG env var
    3. Auto-discovered config file
    4. Default values
    """
    data: dict[str, Any] = {}

    # Resolve config file path
    if config_path is None:
        config_path = os.environ.get("CENTINELA_CONFIG")
    if config_path is None:
        resolved = _find_config_file()
    else:
        resolved = Path(config_path).expanduser().resolve()

    # Load YAML if found
    if resolved and resolved.is_file():
        with open(resolved) as f:
            file_data = yaml.safe_load(f)
            if isinstance(file_data, dict):
                data = file_data

    # Apply env overrides
    data = _apply_env_overrides(data)

    return CentinelaConfig.model_validate(data)


# Singleton-like access
_config: CentinelaConfig | None = None


def get_config() -> CentinelaConfig:
    """Get or create the global configuration instance."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reset_config() -> None:
    """Reset the global config (useful for testing)."""
    global _config
    _config = None
