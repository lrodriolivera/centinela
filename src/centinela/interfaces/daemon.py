"""Daemon mode — run Centinela as a background service.

Supports:
- systemd user service (Ubuntu/Linux)
- launchd agent (macOS)
- Combined gateway + messaging bots in one process

Usage:
    centinela daemon install   — Generate and install service files
    centinela daemon start     — Start the daemon
    centinela daemon stop      — Stop the daemon
    centinela daemon status    — Check daemon status
    centinela daemon uninstall — Remove service files
"""

from __future__ import annotations

import logging
import os
import platform
import signal
import subprocess
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


def _detect_platform() -> str:
    """Detect OS for service management."""
    system = platform.system()
    if system == "Linux":
        return "systemd"
    elif system == "Darwin":
        return "launchd"
    else:
        raise RuntimeError(f"Daemon mode not supported on {system}")


def _get_python_path() -> str:
    """Get the path to the current Python interpreter."""
    return sys.executable


def _get_centinela_path() -> str:
    """Get the path to the centinela entry point."""
    import shutil
    path = shutil.which("centinela")
    if path:
        return path
    return f"{_get_python_path()} -m centinela"


# ─── systemd (Linux) ───


_SYSTEMD_UNIT = """\
[Unit]
Description=Centinela — Agente IA Autónomo
After=network.target docker.service

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=10
Environment=PATH={path}
Environment=HOME={home}
WorkingDirectory={workdir}

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={centinela_home} {workspace}
PrivateTmp=true

[Install]
WantedBy=default.target
"""


def _systemd_unit_path() -> Path:
    """Get the systemd user unit file path."""
    config_dir = Path.home() / ".config" / "systemd" / "user"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "centinela.service"


def _install_systemd() -> str:
    """Generate and install the systemd user service."""
    from centinela.core.config import get_config
    config = get_config()

    unit_content = _SYSTEMD_UNIT.format(
        exec_start=f"{_get_centinela_path()} daemon run",
        path=os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        home=str(Path.home()),
        workdir=str(config.workspace_path),
        centinela_home=str(Path.home() / ".centinela"),
        workspace=str(config.workspace_path),
    )

    unit_path = _systemd_unit_path()
    unit_path.write_text(unit_content)

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "centinela.service"], check=True)

    return str(unit_path)


def _start_systemd() -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "start", "centinela.service"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _stop_systemd() -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "stop", "centinela.service"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _status_systemd() -> dict:
    result = subprocess.run(
        ["systemctl", "--user", "status", "centinela.service"],
        capture_output=True, text=True,
    )
    active = "active (running)" in result.stdout
    return {
        "running": active,
        "platform": "systemd",
        "unit_file": str(_systemd_unit_path()),
        "output": result.stdout[:500],
    }


def _uninstall_systemd() -> None:
    subprocess.run(["systemctl", "--user", "stop", "centinela.service"], capture_output=True)
    subprocess.run(["systemctl", "--user", "disable", "centinela.service"], capture_output=True)
    unit_path = _systemd_unit_path()
    if unit_path.exists():
        unit_path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)


# ─── launchd (macOS) ───


_LAUNCHD_PLIST = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.centinela.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>centinela</string>
        <string>daemon</string>
        <string>run</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>WorkingDirectory</key>
    <string>{workdir}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{path}</string>
        <key>HOME</key>
        <string>{home}</string>
    </dict>
    <key>StandardOutPath</key>
    <string>{log_dir}/centinela.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/centinela.stderr.log</string>
    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
"""


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.centinela.agent.plist"


def _install_launchd() -> str:
    from centinela.core.config import get_config
    config = get_config()

    log_dir = Path.home() / ".centinela" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    plist_content = _LAUNCHD_PLIST.format(
        python=_get_python_path(),
        workdir=str(config.workspace_path),
        path=os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        home=str(Path.home()),
        log_dir=str(log_dir),
    )

    plist_path = _launchd_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_content)

    return str(plist_path)


def _start_launchd() -> bool:
    plist = str(_launchd_plist_path())
    result = subprocess.run(["launchctl", "load", plist], capture_output=True, text=True)
    return result.returncode == 0


def _stop_launchd() -> bool:
    plist = str(_launchd_plist_path())
    result = subprocess.run(["launchctl", "unload", plist], capture_output=True, text=True)
    return result.returncode == 0


def _status_launchd() -> dict:
    result = subprocess.run(
        ["launchctl", "list", "com.centinela.agent"],
        capture_output=True, text=True,
    )
    running = result.returncode == 0 and "Could not find" not in result.stderr
    return {
        "running": running,
        "platform": "launchd",
        "plist_file": str(_launchd_plist_path()),
        "output": result.stdout[:500] if running else result.stderr[:500],
    }


def _uninstall_launchd() -> None:
    _stop_launchd()
    plist = _launchd_plist_path()
    if plist.exists():
        plist.unlink()


# ─── Platform dispatch ───


def install_daemon() -> str:
    plat = _detect_platform()
    if plat == "systemd":
        return _install_systemd()
    return _install_launchd()


def start_daemon() -> bool:
    plat = _detect_platform()
    if plat == "systemd":
        return _start_systemd()
    return _start_launchd()


def stop_daemon() -> bool:
    plat = _detect_platform()
    if plat == "systemd":
        return _stop_systemd()
    return _stop_launchd()


def daemon_status() -> dict:
    plat = _detect_platform()
    if plat == "systemd":
        return _status_systemd()
    return _status_launchd()


def uninstall_daemon() -> None:
    plat = _detect_platform()
    if plat == "systemd":
        _uninstall_systemd()
    else:
        _uninstall_launchd()


# ─── Daemon run (the actual process) ───


def run_daemon() -> None:
    """Run the combined daemon: gateway + optional messaging bots.

    This is the entry point invoked by systemd/launchd.
    """
    from centinela.core.config import get_config
    from centinela.gateway.server import create_app

    config = get_config()
    logger.info("Centinela daemon starting...")

    threads: list[threading.Thread] = []

    # Start Telegram bot if configured
    telegram_token = os.environ.get("CENTINELA_TELEGRAM_TOKEN")
    if telegram_token:
        from centinela.interfaces.telegram_bot import run_telegram_bot

        t = threading.Thread(
            target=run_telegram_bot,
            kwargs={"token": telegram_token},
            daemon=True,
            name="telegram-bot",
        )
        threads.append(t)
        t.start()
        logger.info("Telegram bot started")

    # Start Slack bot if configured
    slack_token = os.environ.get("CENTINELA_SLACK_BOT_TOKEN")
    slack_app_token = os.environ.get("CENTINELA_SLACK_APP_TOKEN")
    if slack_token and slack_app_token:
        from centinela.interfaces.slack_bot import run_slack_bot

        t = threading.Thread(
            target=run_slack_bot,
            kwargs={"bot_token": slack_token, "app_token": slack_app_token},
            daemon=True,
            name="slack-bot",
        )
        threads.append(t)
        t.start()
        logger.info("Slack bot started")

    # Run gateway (blocking, main thread)
    import uvicorn

    app = create_app()
    uvicorn.run(
        app,
        host=config.gateway.host,
        port=config.gateway.port,
        log_level="info",
    )
