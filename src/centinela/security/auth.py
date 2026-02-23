"""Authentication with ephemeral JWT tokens bound to context.

Tokens are:
- Short-lived (configurable TTL, default 15 min)
- Bound to client IP + user-agent (prevents token theft)
- Signed with HS256 using a per-instance secret
- Never accepted from URL parameters (prevents CVE-2026-25253-style attacks)
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass

import jwt

from centinela.core.config import get_config

logger = logging.getLogger(__name__)


@dataclass
class TokenPayload:
    """Decoded token contents."""

    sub: str  # Subject (user or agent ID)
    context_hash: str  # Hash of IP + user-agent
    exp: float  # Expiration timestamp
    iat: float  # Issued-at timestamp
    jti: str  # Unique token ID


class AuthManager:
    """Manages JWT token creation and validation."""

    def __init__(self, secret_key: str | None = None):
        config = get_config()
        self._secret = secret_key or secrets.token_hex(32)
        self._algorithm = config.gateway.auth.algorithm
        self._ttl_minutes = config.gateway.auth.token_ttl_minutes
        self._revoked: set[str] = set()

    @staticmethod
    def _compute_context_hash(client_ip: str, user_agent: str) -> str:
        """Create a hash binding the token to the client's context."""
        data = f"{client_ip}:{user_agent}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def create_token(
        self,
        subject: str,
        client_ip: str = "127.0.0.1",
        user_agent: str = "centinela-cli",
    ) -> str:
        """Create a new ephemeral JWT token.

        The token is bound to the client's IP and user-agent to prevent
        theft via SSRF or XSS (mitigates CVE-2026-25253 pattern).
        """
        now = time.time()
        context_hash = self._compute_context_hash(client_ip, user_agent)
        jti = secrets.token_hex(8)

        payload = {
            "sub": subject,
            "ctx": context_hash,
            "exp": now + (self._ttl_minutes * 60),
            "iat": now,
            "jti": jti,
        }

        token = jwt.encode(payload, self._secret, algorithm=self._algorithm)
        logger.debug(
            "Token created for '%s' (ttl=%dm, ctx=%s)",
            subject, self._ttl_minutes, context_hash,
        )
        return token

    def validate_token(
        self,
        token: str,
        client_ip: str = "127.0.0.1",
        user_agent: str = "centinela-cli",
    ) -> TokenPayload | None:
        """Validate a JWT token.

        Returns TokenPayload on success, None on failure.
        Validates: signature, expiration, context binding, revocation.
        """
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                options={"require": ["sub", "ctx", "exp", "iat", "jti"]},
            )
        except jwt.ExpiredSignatureError:
            logger.warning("Token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning("Invalid token: %s", e)
            return None

        # Check revocation
        jti = payload.get("jti", "")
        if jti in self._revoked:
            logger.warning("Token %s has been revoked", jti)
            return None

        # Verify context binding (IP + user-agent)
        expected_ctx = self._compute_context_hash(client_ip, user_agent)
        if payload.get("ctx") != expected_ctx:
            logger.warning(
                "Token context mismatch: expected=%s, got=%s",
                expected_ctx, payload.get("ctx"),
            )
            return None

        return TokenPayload(
            sub=payload["sub"],
            context_hash=payload["ctx"],
            exp=payload["exp"],
            iat=payload["iat"],
            jti=jti,
        )

    def revoke_token(self, jti: str) -> None:
        """Revoke a token by its ID."""
        self._revoked.add(jti)
        logger.info("Token %s revoked", jti)

    @property
    def secret_key(self) -> str:
        """Access the secret key (for FastAPI middleware setup)."""
        return self._secret


# Global instance
_auth: AuthManager | None = None


def get_auth_manager() -> AuthManager:
    global _auth
    if _auth is None:
        _auth = AuthManager()
    return _auth


def reset_auth_manager() -> None:
    global _auth
    _auth = None
