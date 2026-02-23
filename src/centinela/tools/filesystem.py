"""Filesystem tools — read, write, edit, glob, grep.

All operations are restricted to the configured workspace directory.
Any attempt to access files outside the workspace is blocked.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from pathlib import Path

from centinela.core.config import get_config
from centinela.tools.registry import PermissionTier, get_tool_registry

logger = logging.getLogger(__name__)

registry = get_tool_registry()


def _resolve_safe_path(path: str) -> Path:
    """Resolve a path and ensure it's inside the workspace."""
    config = get_config()
    workspace = config.workspace_path

    resolved = (workspace / path).resolve()
    if not str(resolved).startswith(str(workspace)):
        raise PermissionError(
            f"Acceso denegado: '{path}' está fuera del workspace ({workspace})"
        )
    return resolved


@registry.register(
    name="read_file",
    description="Lee el contenido de un archivo dentro del workspace. Retorna el texto del archivo.",
    permission=PermissionTier.READ,
)
def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
    """Read file contents with optional line range."""
    resolved = _resolve_safe_path(path)
    if not resolved.is_file():
        return f"Error: '{path}' no existe o no es un archivo."

    lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    selected = lines[offset : offset + limit]

    numbered = [f"{i + offset + 1:>5}\t{line}" for i, line in enumerate(selected)]
    header = f"# {path} ({total} líneas, mostrando {offset + 1}-{offset + len(selected)})\n"
    return header + "\n".join(numbered)


@registry.register(
    name="write_file",
    description="Escribe contenido en un archivo dentro del workspace. Crea el archivo si no existe.",
    permission=PermissionTier.WRITE,
)
def write_file(path: str, content: str) -> str:
    """Write content to a file (creates parent dirs if needed)."""
    resolved = _resolve_safe_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return f"Archivo escrito: {path} ({len(content)} bytes)"


@registry.register(
    name="edit_file",
    description="Reemplaza una cadena exacta por otra en un archivo. old_string debe ser único en el archivo.",
    permission=PermissionTier.WRITE,
)
def edit_file(path: str, old_string: str, new_string: str) -> str:
    """Replace exact string in a file."""
    resolved = _resolve_safe_path(path)
    if not resolved.is_file():
        return f"Error: '{path}' no existe."

    content = resolved.read_text(encoding="utf-8")
    count = content.count(old_string)

    if count == 0:
        return f"Error: old_string no encontrado en '{path}'."
    if count > 1:
        return f"Error: old_string aparece {count} veces. Debe ser único."

    new_content = content.replace(old_string, new_string, 1)
    resolved.write_text(new_content, encoding="utf-8")
    return f"Archivo editado: {path} (1 reemplazo)"


@registry.register(
    name="list_files",
    description="Lista archivos en un directorio del workspace usando un patrón glob. Ejemplo: '**/*.py'",
    permission=PermissionTier.READ,
)
def list_files(pattern: str = "*", path: str = ".") -> str:
    """List files matching a glob pattern within the workspace."""
    config = get_config()
    workspace = config.workspace_path
    base = _resolve_safe_path(path)

    if not base.is_dir():
        return f"Error: '{path}' no es un directorio."

    matches = sorted(base.glob(pattern))
    # Limit results to prevent flooding
    max_results = 200
    results = []
    for m in matches[:max_results]:
        try:
            rel = m.relative_to(workspace)
        except ValueError:
            continue
        suffix = "/" if m.is_dir() else ""
        results.append(f"  {rel}{suffix}")

    header = f"# {len(results)} archivos (patrón: '{pattern}' en '{path}')"
    if len(matches) > max_results:
        header += f" [truncado, {len(matches)} total]"
    return header + "\n" + "\n".join(results) if results else f"Sin resultados para '{pattern}'"


@registry.register(
    name="search_files",
    description="Busca un patrón regex en archivos del workspace. Retorna líneas coincidentes con contexto.",
    permission=PermissionTier.READ,
)
def search_files(
    pattern: str,
    path: str = ".",
    file_glob: str = "*",
    max_results: int = 50,
) -> str:
    """Search for a regex pattern in files."""
    base = _resolve_safe_path(path)
    if not base.is_dir():
        return f"Error: '{path}' no es un directorio."

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"Error en regex: {e}"

    results: list[str] = []
    files_searched = 0

    for file_path in sorted(base.rglob(file_glob)):
        if not file_path.is_file():
            continue
        # Skip binary files and large files
        if file_path.stat().st_size > 1_000_000:
            continue

        files_searched += 1
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                config = get_config()
                try:
                    rel = file_path.relative_to(config.workspace_path)
                except ValueError:
                    rel = file_path
                results.append(f"  {rel}:{i}: {line.strip()[:150]}")
                if len(results) >= max_results:
                    break
        if len(results) >= max_results:
            break

    header = f"# {len(results)} coincidencias en {files_searched} archivos"
    if len(results) >= max_results:
        header += " [truncado]"
    return header + "\n" + "\n".join(results) if results else f"Sin coincidencias para '{pattern}'"
