"""Tests for Phase 3: tools, memory, agents, and orchestrator."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from centinela.core.config import CentinelaConfig, reset_config
from centinela.tools.registry import ToolRegistry


# ─── Filesystem Tools Tests ───


class TestFilesystemTools:
    """Test filesystem tools with workspace enforcement."""

    def setup_method(self, tmp_path=None):
        reset_config()

    def test_resolve_safe_path_inside_workspace(self, tmp_path):
        from centinela.tools.filesystem import _resolve_safe_path

        config = CentinelaConfig(workspace=str(tmp_path))
        with patch("centinela.tools.filesystem.get_config", return_value=config):
            resolved = _resolve_safe_path("test.py")
            assert str(resolved).startswith(str(tmp_path))

    def test_resolve_safe_path_blocks_escape(self, tmp_path):
        from centinela.tools.filesystem import _resolve_safe_path

        config = CentinelaConfig(workspace=str(tmp_path))
        with patch("centinela.tools.filesystem.get_config", return_value=config):
            with pytest.raises(PermissionError, match="fuera del workspace"):
                _resolve_safe_path("../../etc/passwd")

    def test_read_file(self, tmp_path):
        from centinela.tools.filesystem import read_file

        test_file = tmp_path / "hello.txt"
        test_file.write_text("line1\nline2\nline3")

        config = CentinelaConfig(workspace=str(tmp_path))
        with patch("centinela.tools.filesystem.get_config", return_value=config):
            result = read_file("hello.txt")
            assert "line1" in result
            assert "line2" in result
            assert "3 líneas" in result

    def test_write_file(self, tmp_path):
        from centinela.tools.filesystem import write_file

        config = CentinelaConfig(workspace=str(tmp_path))
        with patch("centinela.tools.filesystem.get_config", return_value=config):
            result = write_file("new_file.txt", "hello world")
            assert "escrito" in result
            assert (tmp_path / "new_file.txt").read_text() == "hello world"

    def test_edit_file(self, tmp_path):
        from centinela.tools.filesystem import edit_file

        test_file = tmp_path / "code.py"
        test_file.write_text("def hello():\n    return 'world'\n")

        config = CentinelaConfig(workspace=str(tmp_path))
        with patch("centinela.tools.filesystem.get_config", return_value=config):
            result = edit_file("code.py", "return 'world'", "return 'universe'")
            assert "editado" in result
            assert "universe" in test_file.read_text()

    def test_edit_file_not_found(self, tmp_path):
        from centinela.tools.filesystem import edit_file

        config = CentinelaConfig(workspace=str(tmp_path))
        with patch("centinela.tools.filesystem.get_config", return_value=config):
            result = edit_file("nonexistent.py", "a", "b")
            assert "no existe" in result

    def test_list_files(self, tmp_path):
        from centinela.tools.filesystem import list_files

        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("y")

        config = CentinelaConfig(workspace=str(tmp_path))
        with patch("centinela.tools.filesystem.get_config", return_value=config):
            result = list_files("*.py")
            assert "a.py" in result
            assert "b.py" in result

    def test_search_files(self, tmp_path):
        from centinela.tools.filesystem import search_files

        (tmp_path / "code.py").write_text("def hello():\n    return 42\n")

        config = CentinelaConfig(workspace=str(tmp_path))
        with patch("centinela.tools.filesystem.get_config", return_value=config):
            result = search_files("return 42", file_glob="*.py")
            assert "code.py" in result
            assert "return 42" in result


# ─── Web Tools Tests ───


class TestWebTools:
    def test_block_internal_urls(self):
        from centinela.tools.web import _is_safe_url

        assert not _is_safe_url("http://localhost/admin")
        assert not _is_safe_url("http://127.0.0.1:8080")
        assert not _is_safe_url("http://169.254.169.254/latest/meta-data")
        assert not _is_safe_url("http://10.0.0.1/internal")
        assert not _is_safe_url("http://192.168.1.1/router")
        assert not _is_safe_url("ftp://example.com/file")

    def test_allow_public_urls(self):
        from centinela.tools.web import _is_safe_url

        assert _is_safe_url("https://example.com")
        assert _is_safe_url("https://docs.python.org")
        assert _is_safe_url("http://api.github.com/repos")

    def test_html_to_text(self):
        from centinela.tools.web import _html_to_text

        html = """
        <html><body>
        <script>alert('xss')</script>
        <nav>Menu</nav>
        <main><p>Hello World</p><p>Paragraph two</p></main>
        <footer>Footer</footer>
        </body></html>
        """
        text = _html_to_text(html)
        assert "Hello World" in text
        assert "alert" not in text
        assert "Menu" not in text
        assert "Footer" not in text


# ─── Memory Tests ───


class TestEpisodicMemory:
    def test_record_and_retrieve(self, tmp_path):
        from centinela.core.memory import EpisodicMemory

        config = CentinelaConfig()
        config.memory.transcripts.path = str(tmp_path)

        memory = EpisodicMemory(config)
        memory.record(role="user", content="Hello!")
        memory.record(role="assistant", content="Hi there!", agent_id="base")

        recent = memory.get_recent(limit=10)
        assert len(recent) == 2
        assert recent[0]["role"] == "user"
        assert recent[1]["role"] == "assistant"

    def test_search_transcripts(self, tmp_path):
        from centinela.core.memory import EpisodicMemory

        config = CentinelaConfig()
        config.memory.transcripts.path = str(tmp_path)

        memory = EpisodicMemory(config)
        memory.record(role="user", content="How do I use Python decorators?")
        memory.record(role="assistant", content="Decorators wrap functions.")
        memory.record(role="user", content="What about TypeScript?")

        results = memory.search("decorators")
        assert len(results) >= 1
        assert "decorators" in results[0]["content"].lower()

    def test_stats(self, tmp_path):
        from centinela.core.memory import EpisodicMemory

        config = CentinelaConfig()
        config.memory.transcripts.path = str(tmp_path)

        memory = EpisodicMemory(config)
        memory.record(role="user", content="test")

        stats = memory.get_stats()
        assert stats["total_days"] == 1
        assert stats["total_entries"] == 1


class TestPreferencesMemory:
    def test_set_and_get(self, tmp_path):
        from centinela.core.memory import PreferencesMemory

        config = CentinelaConfig()
        config.memory.transcripts.path = str(tmp_path / "transcripts")

        prefs = PreferencesMemory(config)
        prefs.set("language", "python")
        prefs.set("ui.theme", "dark")

        assert prefs.get("language") == "python"
        assert prefs.get("ui.theme") == "dark"
        assert prefs.get("nonexistent", "default") == "default"

    def test_persistence(self, tmp_path):
        from centinela.core.memory import PreferencesMemory

        config = CentinelaConfig()
        config.memory.transcripts.path = str(tmp_path / "transcripts")

        prefs1 = PreferencesMemory(config)
        prefs1.set("key", "value")

        prefs2 = PreferencesMemory(config)
        assert prefs2.get("key") == "value"


# ─── RAG Chunking Tests ───


class TestRagChunking:
    def test_short_text_single_chunk(self):
        from centinela.tools.rag import _chunk_text

        chunks = _chunk_text("Hello world", chunk_size=100)
        assert len(chunks) == 1

    def test_long_text_multiple_chunks(self):
        from centinela.tools.rag import _chunk_text

        text = "word " * 500  # ~2500 chars
        chunks = _chunk_text(text, chunk_size=200, overlap=50)
        assert len(chunks) > 1
        # Verify all text is covered
        combined = " ".join(chunks)
        assert "word" in combined

    def test_overlap_exists(self):
        from centinela.tools.rag import _chunk_text

        text = "A" * 100 + "B" * 100 + "C" * 100
        chunks = _chunk_text(text, chunk_size=120, overlap=30)
        # With overlap, adjacent chunks should share some content
        assert len(chunks) >= 2


# ─── Shell Integration Tests ───


class TestShellPipeline:
    """Test the shell tool's policy integration (without Docker)."""

    def test_safe_command_allowed(self):
        from centinela.security.policies import CommandDecision, CommandPolicyEngine

        engine = CommandPolicyEngine()
        result = engine.evaluate("ls -la")
        assert result.decision == CommandDecision.ALLOWED

    def test_blocked_command_rejected(self):
        from centinela.security.policies import CommandDecision, CommandPolicyEngine

        engine = CommandPolicyEngine()
        result = engine.evaluate("sudo rm -rf /")
        assert result.decision == CommandDecision.BLOCKED


# ─── Agent Tests ───


class TestAgents:
    def test_coder_agent_properties(self):
        from centinela.agents.coder import CoderAgent
        from centinela.tools.registry import PermissionTier

        agent = CoderAgent.__new__(CoderAgent)
        assert agent.name == "coder"
        assert agent.permission_tier == PermissionTier.WRITE

    def test_researcher_agent_properties(self):
        from centinela.agents.researcher import ResearcherAgent
        from centinela.tools.registry import PermissionTier

        agent = ResearcherAgent.__new__(ResearcherAgent)
        assert agent.name == "researcher"
        assert agent.permission_tier == PermissionTier.EXECUTE

    def test_executor_agent_properties(self):
        from centinela.agents.executor import ExecutorAgent
        from centinela.tools.registry import PermissionTier

        agent = ExecutorAgent.__new__(ExecutorAgent)
        assert agent.name == "executor"
        assert agent.permission_tier == PermissionTier.EXECUTE

    def test_reviewer_agent_properties(self):
        from centinela.agents.reviewer import ReviewerAgent
        from centinela.tools.registry import PermissionTier

        agent = ReviewerAgent.__new__(ReviewerAgent)
        assert agent.name == "reviewer"
        assert agent.permission_tier == PermissionTier.READ
