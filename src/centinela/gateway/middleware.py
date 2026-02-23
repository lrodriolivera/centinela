"""Gateway middleware — auth, CORS, rate limiting.

Security layers applied to every request:
1. CORS: strict origin whitelist (no wildcard)
2. Rate limiting: sliding window per IP (in-memory)
3. Auth: JWT token validation bound to client context
4. No dynamic URL parameters accepted (prevents SSRF/token theft)
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Callable

from fastapi import HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from centinela.core.config import get_config
from centinela.security.auth import get_auth_manager

logger = logging.getLogger(__name__)


# ─── Rate Limiter (in-memory sliding window) ───


class RateLimiter:
    """Sliding window rate limiter per client IP."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_ip: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds

        # Clean old entries
        entries = self._requests[client_ip]
        self._requests[client_ip] = [t for t in entries if t > cutoff]

        if len(self._requests[client_ip]) >= self.max_requests:
            return False

        self._requests[client_ip].append(now)
        return True

    def remaining(self, client_ip: str) -> int:
        now = time.time()
        cutoff = now - self.window_seconds
        entries = [t for t in self._requests[client_ip] if t > cutoff]
        return max(0, self.max_requests - len(entries))


_rate_limiter = RateLimiter()


# ─── Rate Limit Middleware ───


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        client_ip = request.client.host if request.client else "unknown"

        if not _rate_limiter.is_allowed(client_ip):
            logger.warning("Rate limit exceeded for %s", client_ip)
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Try again later.",
            )

        response = await call_next(request)
        remaining = _rate_limiter.remaining(client_ip)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Limit"] = str(_rate_limiter.max_requests)
        return response


# ─── Auth Dependency ───


async def require_auth(request: Request) -> str:
    """FastAPI dependency: validate JWT token and return subject.

    Token MUST be in Authorization header (never URL params).
    """
    config = get_config()
    if not config.gateway.auth.enabled:
        return "anonymous"

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]
    auth = get_auth_manager()

    client_ip = request.client.host if request.client else "127.0.0.1"
    user_agent = request.headers.get("User-Agent", "unknown")

    payload = auth.validate_token(token, client_ip=client_ip, user_agent=user_agent)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return payload.sub


# ─── Security Headers Middleware ───


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
        return response


def setup_cors(app) -> None:
    """Configure strict CORS — no wildcards."""
    config = get_config()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.gateway.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )
