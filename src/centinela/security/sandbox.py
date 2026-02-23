"""Docker sandbox executor for secure command execution.

Runs commands in isolated Docker containers with:
- Network disabled (no egress)
- Read-only root filesystem
- All Linux capabilities dropped
- No privilege escalation
- Memory and CPU limits
- Execution timeout
- Automatic container cleanup
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import docker
from docker.errors import ContainerError, DockerException, ImageNotFound

from centinela.core.config import CentinelaConfig, get_config

logger = logging.getLogger(__name__)

_SANDBOX_LABEL = "centinela-sandbox"


@dataclass
class SandboxResult:
    """Result of a sandboxed command execution."""

    success: bool
    output: str
    error: str | None = None
    exit_code: int = 0
    execution_time_ms: int = 0
    container_id: str | None = None
    model_id: str | None = None  # which sandbox image was used


class SandboxExecutor:
    """Execute commands in isolated Docker containers."""

    def __init__(self, config: CentinelaConfig | None = None):
        self.config = config or get_config()
        self._client: docker.DockerClient | None = None
        self._sandbox_config = self.config.security.sandbox

    @property
    def client(self) -> docker.DockerClient:
        if self._client is None:
            try:
                self._client = docker.from_env()
                self._client.ping()
            except DockerException as e:
                raise RuntimeError(
                    f"Docker no disponible: {e}. "
                    "Instala Docker o deshabilita el sandbox en centinela.yaml"
                ) from e
        return self._client

    @property
    def enabled(self) -> bool:
        return self._sandbox_config.enabled

    def ensure_image(self) -> bool:
        """Check if sandbox image exists, return True if available."""
        try:
            self.client.images.get(self._sandbox_config.image)
            return True
        except ImageNotFound:
            logger.warning(
                "Sandbox image '%s' not found. "
                "Run: scripts/build-sandbox.sh",
                self._sandbox_config.image,
            )
            return False

    def execute(
        self,
        command: str | list[str],
        workspace_path: str | None = None,
        timeout: int | None = None,
        environment: dict[str, str] | None = None,
    ) -> SandboxResult:
        """Execute a command in a sandboxed Docker container.

        Args:
            command: Shell command string or list of args
            workspace_path: Host directory to mount as /workspace (rw)
            timeout: Override timeout in seconds
            environment: Extra env vars for the container (NO secrets)
        """
        if not self.enabled:
            return self._execute_local(command)

        timeout = timeout or self._sandbox_config.timeout_seconds
        start_time = time.time()

        # Build command
        if isinstance(command, str):
            cmd = ["bash", "-c", command]
        else:
            cmd = command

        # Container configuration — maximum isolation
        container_kwargs: dict[str, Any] = {
            "image": self._sandbox_config.image,
            "command": cmd,
            "detach": True,
            "stdout": True,
            "stderr": True,
            # --- SECURITY ---
            "network_disabled": not self._sandbox_config.network,
            "read_only": self._sandbox_config.read_only,
            "security_opt": [
                "no-new-privileges=true",
            ],
            "cap_drop": ["ALL"],  # Drop ALL Linux capabilities
            "cap_add": [],  # Don't add any back
            "mem_limit": self._sandbox_config.memory_limit,
            "memswap_limit": self._sandbox_config.memory_limit,  # No swap
            "cpu_period": 100000,
            "cpu_quota": int(self._sandbox_config.cpu_limit * 100000),
            "pids_limit": 256,  # Limit number of processes
            # Temp space for operations
            "tmpfs": {"/tmp": "size=100m,mode=1777,noexec"},
            # Label for cleanup
            "labels": {_SANDBOX_LABEL: "true"},
            # Environment
            "environment": environment or {},
        }

        # Mount workspace if provided
        if workspace_path:
            container_kwargs["volumes"] = {
                workspace_path: {"bind": "/workspace", "mode": "rw"}
            }
            container_kwargs["working_dir"] = "/workspace"

        container = None
        try:
            container = self.client.containers.create(**container_kwargs)
            container.start()

            # Wait with timeout
            result = container.wait(timeout=timeout)
            exit_code = result.get("StatusCode", -1)

            # Collect output
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

            elapsed_ms = int((time.time() - start_time) * 1000)

            return SandboxResult(
                success=exit_code == 0,
                output=stdout.strip(),
                error=stderr.strip() if stderr.strip() else None,
                exit_code=exit_code,
                execution_time_ms=elapsed_ms,
                container_id=container.short_id,
            )

        except docker.errors.APIError as e:
            # Timeout or other Docker API error
            elapsed_ms = int((time.time() - start_time) * 1000)
            if container:
                try:
                    container.kill()
                except Exception:
                    pass

            error_msg = str(e)
            if "timed out" in error_msg.lower() or elapsed_ms >= timeout * 1000:
                error_msg = f"Timeout: ejecución excedió {timeout}s"

            return SandboxResult(
                success=False,
                output="",
                error=error_msg,
                exit_code=-1,
                execution_time_ms=elapsed_ms,
                container_id=container.short_id if container else None,
            )

        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.error("Sandbox execution failed: %s", e)
            return SandboxResult(
                success=False,
                output="",
                error=str(e),
                exit_code=-1,
                execution_time_ms=elapsed_ms,
            )

        finally:
            if container:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    def _execute_local(self, command: str | list[str]) -> SandboxResult:
        """Fallback: execute locally when sandbox is disabled (development only)."""
        import subprocess

        start_time = time.time()
        if isinstance(command, list):
            command = " ".join(command)

        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self._sandbox_config.timeout_seconds,
                cwd=str(self.config.workspace_path),
            )
            elapsed_ms = int((time.time() - start_time) * 1000)

            return SandboxResult(
                success=proc.returncode == 0,
                output=proc.stdout.strip(),
                error=proc.stderr.strip() if proc.stderr.strip() else None,
                exit_code=proc.returncode,
                execution_time_ms=elapsed_ms,
            )

        except subprocess.TimeoutExpired:
            elapsed_ms = int((time.time() - start_time) * 1000)
            return SandboxResult(
                success=False,
                output="",
                error=f"Timeout: ejecución excedió {self._sandbox_config.timeout_seconds}s",
                exit_code=-1,
                execution_time_ms=elapsed_ms,
            )

    def cleanup_stale_containers(self, max_age_hours: int = 4) -> int:
        """Remove old sandbox containers that weren't cleaned up."""
        removed = 0
        try:
            containers = self.client.containers.list(
                all=True,
                filters={"label": _SANDBOX_LABEL},
            )
            cutoff = time.time() - (max_age_hours * 3600)

            for container in containers:
                created = container.attrs.get("Created", "")
                # Docker timestamps are ISO format
                try:
                    from datetime import datetime

                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if dt.timestamp() < cutoff:
                        container.remove(force=True)
                        removed += 1
                except (ValueError, TypeError):
                    pass

        except DockerException as e:
            logger.warning("Failed to cleanup containers: %s", e)

        if removed:
            logger.info("Cleaned up %d stale sandbox containers", removed)
        return removed

    def get_stats(self) -> dict:
        """Get sandbox statistics."""
        try:
            running = self.client.containers.list(
                filters={"label": _SANDBOX_LABEL}
            )
            all_containers = self.client.containers.list(
                all=True, filters={"label": _SANDBOX_LABEL}
            )
            image_exists = self.ensure_image()

            return {
                "enabled": self.enabled,
                "image": self._sandbox_config.image,
                "image_available": image_exists,
                "running_containers": len(running),
                "total_containers": len(all_containers),
                "network_disabled": not self._sandbox_config.network,
                "read_only": self._sandbox_config.read_only,
                "memory_limit": self._sandbox_config.memory_limit,
                "cpu_limit": self._sandbox_config.cpu_limit,
            }
        except DockerException:
            return {
                "enabled": self.enabled,
                "docker_available": False,
            }


# Global instance
_sandbox: SandboxExecutor | None = None


def get_sandbox() -> SandboxExecutor:
    global _sandbox
    if _sandbox is None:
        _sandbox = SandboxExecutor()
    return _sandbox
