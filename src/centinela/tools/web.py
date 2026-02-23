"""Web tools — fetch URLs and extract content.

Uses httpx for async HTTP and BeautifulSoup for HTML parsing.
Network access is controlled by configuration.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from centinela.tools.registry import PermissionTier, get_tool_registry

logger = logging.getLogger(__name__)

registry = get_tool_registry()

_DEFAULT_HEADERS = {
    "User-Agent": "Centinela/0.1 (AI Agent; +https://github.com/lrodriolivera/centinela)",
}

# Block internal/private ranges
_BLOCKED_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "169.254.169.254",  # AWS metadata endpoint
    "metadata.google.internal",
}


def _is_safe_url(url: str) -> bool:
    """Validate URL to prevent SSRF attacks."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    hostname = parsed.hostname or ""
    if hostname in _BLOCKED_HOSTS:
        return False

    # Block private IP ranges
    if hostname.startswith(("10.", "192.168.", "172.")):
        return False

    return True


def _html_to_text(html: str, max_length: int = 15000) -> str:
    """Convert HTML to clean text."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove scripts, styles, nav, footer
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    result = "\n".join(lines)

    if len(result) > max_length:
        result = result[:max_length] + "\n\n[...contenido truncado]"
    return result


@registry.register(
    name="web_fetch",
    description=(
        "Descarga y extrae el texto de una URL. "
        "Convierte HTML a texto limpio. Útil para investigar documentación, "
        "artículos y páginas web. No accede a URLs internas o privadas."
    ),
    permission=PermissionTier.EXECUTE,
    requires_approval=True,
    tags=["web", "research"],
)
def web_fetch(url: str, max_length: int = 15000) -> str:
    """Fetch a URL and return its text content."""
    if not _is_safe_url(url):
        return f"Error: URL bloqueada por política de seguridad: {url}"

    try:
        with httpx.Client(
            headers=_DEFAULT_HEADERS,
            timeout=30.0,
            follow_redirects=True,
            max_redirects=5,
        ) as client:
            response = client.get(url)
            response.raise_for_status()

        content_type = response.headers.get("content-type", "")

        if "html" in content_type:
            text = _html_to_text(response.text, max_length=max_length)
        elif "json" in content_type:
            text = response.text[:max_length]
        elif "text" in content_type:
            text = response.text[:max_length]
        else:
            return f"Tipo de contenido no soportado: {content_type}"

        return f"# Contenido de {url}\n\n{text}"

    except httpx.TimeoutException:
        return f"Error: Timeout al acceder a {url}"
    except httpx.HTTPStatusError as e:
        return f"Error HTTP {e.response.status_code}: {url}"
    except Exception as e:
        return f"Error al acceder a {url}: {e}"


@registry.register(
    name="web_search_extract",
    description=(
        "Extrae información específica de una URL basándose en una pregunta. "
        "Descarga la página, extrae el texto y retorna las secciones más relevantes."
    ),
    permission=PermissionTier.EXECUTE,
    requires_approval=True,
    tags=["web", "research"],
)
def web_search_extract(url: str, query: str) -> str:
    """Fetch URL and extract sections relevant to a query."""
    content = web_fetch(url)
    if content.startswith("Error"):
        return content

    # Simple relevance extraction: find paragraphs containing query terms
    query_terms = [t.lower() for t in query.split() if len(t) > 2]
    lines = content.split("\n")
    relevant: list[str] = []

    for line in lines:
        lower_line = line.lower()
        if any(term in lower_line for term in query_terms):
            relevant.append(line)

    if not relevant:
        return f"No se encontró información relevante para '{query}' en {url}"

    return f"# Extracto de {url} (query: '{query}')\n\n" + "\n".join(relevant[:50])
