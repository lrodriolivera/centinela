"""Tests for Phase 5: Telegram bot, Slack bot, daemon."""

import os
import platform
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ─── Telegram Bot Tests ───


class TestTelegramBot:
    def test_authorized_when_no_restriction(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CENTINELA_TELEGRAM_ALLOWED_CHATS", None)
            from centinela.interfaces.telegram_bot import _is_authorized
            assert _is_authorized(12345)

    def test_authorized_chat_id(self):
        with patch.dict(os.environ, {"CENTINELA_TELEGRAM_ALLOWED_CHATS": "111,222,333"}):
            from centinela.interfaces.telegram_bot import _is_authorized
            assert _is_authorized(222)

    def test_unauthorized_chat_id(self):
        with patch.dict(os.environ, {"CENTINELA_TELEGRAM_ALLOWED_CHATS": "111,222"}):
            from centinela.interfaces.telegram_bot import _is_authorized
            assert not _is_authorized(999)

    def test_create_app_requires_token(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CENTINELA_TELEGRAM_TOKEN", None)
            from centinela.interfaces.telegram_bot import create_telegram_app
            with pytest.raises(ValueError, match="token not configured"):
                create_telegram_app(token="")

    def test_get_allowed_chat_ids_parsing(self):
        with patch.dict(os.environ, {"CENTINELA_TELEGRAM_ALLOWED_CHATS": "100, 200, 300"}):
            from centinela.interfaces.telegram_bot import _get_allowed_chat_ids
            ids = _get_allowed_chat_ids()
            assert ids == {100, 200, 300}


# ─── Slack Bot Tests ───


class TestSlackBot:
    def test_create_app_requires_token(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CENTINELA_SLACK_BOT_TOKEN", None)
            from centinela.interfaces.slack_bot import create_slack_app
            with pytest.raises(ValueError, match="token not configured"):
                create_slack_app(bot_token="")

    def test_run_requires_app_token(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CENTINELA_SLACK_APP_TOKEN", None)
            from centinela.interfaces.slack_bot import run_slack_bot
            with pytest.raises(ValueError, match="app-level token"):
                run_slack_bot(bot_token="xoxb-fake", app_token="")


# ─── Daemon Tests ───


class TestDaemon:
    def test_detect_platform(self):
        from centinela.interfaces.daemon import _detect_platform
        plat = _detect_platform()
        system = platform.system()
        if system == "Linux":
            assert plat == "systemd"
        elif system == "Darwin":
            assert plat == "launchd"

    def test_get_python_path(self):
        from centinela.interfaces.daemon import _get_python_path
        path = _get_python_path()
        assert "python" in path.lower()

    def test_systemd_unit_content(self):
        """Verify systemd unit file template is valid."""
        from centinela.interfaces.daemon import _SYSTEMD_UNIT
        assert "[Unit]" in _SYSTEMD_UNIT
        assert "[Service]" in _SYSTEMD_UNIT
        assert "[Install]" in _SYSTEMD_UNIT
        assert "NoNewPrivileges=true" in _SYSTEMD_UNIT
        assert "ProtectSystem=strict" in _SYSTEMD_UNIT

    def test_launchd_plist_content(self):
        """Verify launchd plist template is valid."""
        from centinela.interfaces.daemon import _LAUNCHD_PLIST
        assert "com.centinela.agent" in _LAUNCHD_PLIST
        assert "<key>KeepAlive</key>" in _LAUNCHD_PLIST
        assert "<key>ThrottleInterval</key>" in _LAUNCHD_PLIST

    def test_systemd_unit_path(self):
        from centinela.interfaces.daemon import _systemd_unit_path
        path = _systemd_unit_path()
        assert path.name == "centinela.service"
        assert ".config/systemd/user" in str(path)

    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
    def test_launchd_plist_path(self):
        from centinela.interfaces.daemon import _launchd_plist_path
        path = _launchd_plist_path()
        assert path.name == "com.centinela.agent.plist"
        assert "LaunchAgents" in str(path)

    def test_systemd_unit_has_security_hardening(self):
        """Verify the systemd unit includes all security directives."""
        from centinela.interfaces.daemon import _SYSTEMD_UNIT
        assert "NoNewPrivileges=true" in _SYSTEMD_UNIT
        assert "ProtectSystem=strict" in _SYSTEMD_UNIT
        assert "ProtectHome=read-only" in _SYSTEMD_UNIT
        assert "PrivateTmp=true" in _SYSTEMD_UNIT
