"""Tests for the model resolver and configuration."""

from unittest.mock import MagicMock, patch

import pytest

from centinela.core.config import CentinelaConfig, load_config, reset_config
from centinela.core.models import ModelResolver, ModelResponse, ModelStatus, StreamChunk


class TestModelStatus:
    def test_initially_available(self):
        status = ModelStatus(model_id="test-model")
        assert status.is_available()
        assert status.failure_count == 0

    def test_unavailable_after_failure(self):
        from centinela.core.config import CooldownConfig

        cooldown = CooldownConfig(initial_seconds=60, multiplier=5, max_seconds=3600)
        status = ModelStatus(model_id="test-model")
        status.record_failure("test error", cooldown)

        assert not status.is_available()
        assert status.failure_count == 1
        assert status.last_error == "test error"

    def test_available_after_success(self):
        from centinela.core.config import CooldownConfig

        cooldown = CooldownConfig(initial_seconds=1, multiplier=1, max_seconds=10)
        status = ModelStatus(model_id="test-model")
        status.record_failure("err", cooldown)
        status.record_success()

        assert status.is_available()
        assert status.failure_count == 0
        assert status.last_error is None


class TestConfig:
    def setup_method(self):
        reset_config()

    def test_default_config(self):
        config = CentinelaConfig()
        assert config.identity.name == "Centinela"
        assert config.models.provider == "bedrock"
        assert config.models.region == "us-east-1"
        assert len(config.models.fallbacks) == 2

    def test_model_chain(self):
        config = CentinelaConfig()
        assert "opus" in config.models.primary
        assert "sonnet" in config.models.fallbacks[0]
        assert "haiku" in config.models.fallbacks[1]

    def test_security_defaults(self):
        config = CentinelaConfig()
        assert config.security.sandbox.enabled is True
        assert config.security.sandbox.network is False
        assert config.security.sandbox.read_only is True
        assert config.security.policies.mode == "allowlist"
        assert config.security.audit.enabled is True
        assert config.security.audit.redact_secrets is True

    def test_env_override(self):
        import os

        os.environ["CENTINELA_MODELS__REGION"] = "eu-west-1"
        try:
            config = load_config()
            assert config.models.region == "eu-west-1"
        finally:
            del os.environ["CENTINELA_MODELS__REGION"]
            reset_config()


class TestModelResolver:
    def test_build_model_chain(self):
        config = CentinelaConfig()
        resolver = ModelResolver(config)
        chain = resolver._model_chain
        assert len(chain) == 3
        assert "opus" in chain[0]
        assert "sonnet" in chain[1]
        assert "haiku" in chain[2]

    def test_resolve_alias(self):
        config = CentinelaConfig()
        config.models.aliases = {
            "opus": "us.anthropic.claude-opus-4-5-20251101-v1:0",
        }
        resolver = ModelResolver(config)
        resolved = resolver.resolve_model("opus")
        assert "opus" in resolved

    def test_get_status(self):
        config = CentinelaConfig()
        resolver = ModelResolver(config)
        status = resolver.get_status()
        assert len(status) == 3
        for model_id, info in status.items():
            assert info["available"] is True
            assert info["failure_count"] == 0

    def test_select_available_skips_cooled_down(self):
        config = CentinelaConfig()
        resolver = ModelResolver(config)

        # Put primary in cooldown
        primary = resolver._model_chain[0]
        resolver._statuses[primary].record_failure(
            "test", config.models.cooldown
        )

        selected = resolver._select_available_model()
        assert selected != primary
        assert "sonnet" in selected


class TestStreamChunk:
    def test_text_chunk(self):
        chunk = StreamChunk(text="hello")
        assert chunk.text == "hello"
        assert chunk.tool_use is None

    def test_tool_chunk(self):
        chunk = StreamChunk(tool_use={"name": "test", "input": {}})
        assert chunk.tool_use is not None
        assert chunk.tool_use["name"] == "test"
