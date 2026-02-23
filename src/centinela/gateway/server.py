"""Centinela Gateway — FastAPI application.

Serves:
- REST API for chat, agents, audit, approval
- WebSocket for real-time streaming chat
- Static files for the Web UI (when built)

Security:
- Binds to localhost only (127.0.0.1) by default
- JWT auth on all API endpoints
- Rate limiting per IP
- CORS with strict origin whitelist
- Security headers (nosniff, DENY frame, XSS protection)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from centinela.core.config import get_config
from centinela.gateway.middleware import (
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    setup_cors,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    logger.info("Centinela Gateway starting...")
    config = get_config()
    logger.info(
        "Listening on %s:%d", config.gateway.host, config.gateway.port
    )
    yield
    logger.info("Centinela Gateway shutting down.")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    config = get_config()

    app = FastAPI(
        title="Centinela API",
        description="Agente IA autónomo con seguridad de grado empresarial",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if not config.gateway.auth.enabled else None,
        redoc_url=None,
    )

    # Middleware (order matters — outermost first)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware)
    setup_cors(app)

    # Routes
    from centinela.gateway.routes import router
    app.include_router(router, prefix="/api")

    # Serve Web UI static files if built
    web_dist = Path(__file__).parent.parent / "web" / "dist"
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="web")
        logger.info("Web UI served from %s", web_dist)

    return app


def run_server(host: str | None = None, port: int | None = None) -> None:
    """Run the gateway server with uvicorn."""
    import uvicorn

    config = get_config()
    host = host or config.gateway.host
    port = port or config.gateway.port

    uvicorn.run(
        create_app(),
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )
