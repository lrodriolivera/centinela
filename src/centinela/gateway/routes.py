"""API routes — REST endpoints and WebSocket.

Endpoints:
  POST /api/chat          Stream chat response (SSE)
  POST /api/chat/sync     Synchronous chat response
  GET  /api/health        Health check (no auth)
  GET  /api/models        Model status
  GET  /api/agents        Agent status
  GET  /api/audit         Audit log entries
  GET  /api/sessions      Memory stats
  POST /api/token         Generate auth token
  POST /api/approve/{id}  Approve pending action
  POST /api/reject/{id}   Reject pending action
  GET  /api/pending       List pending approvals
  WS   /api/ws/chat       WebSocket chat
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from centinela.gateway.middleware import require_auth
from centinela.gateway.streaming import async_sse_stream

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Request/Response models ───


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=50000)
    model: str | None = None
    stream: bool = True


class ChatResponse(BaseModel):
    text: str
    agent: str = "centinela"
    model_id: str = ""


class TokenRequest(BaseModel):
    subject: str = "user"


class TokenResponse(BaseModel):
    token: str
    expires_in_minutes: int


class ApprovalAction(BaseModel):
    decided_by: str = "api_user"


# ─── Health (no auth) ───


@router.get("/health")
async def health():
    return {"status": "ok", "service": "centinela", "version": "0.1.0"}


# ─── Token Generation ───


@router.post("/token", response_model=TokenResponse)
async def create_token(req: TokenRequest):
    """Generate an ephemeral JWT token."""
    from centinela.core.config import get_config
    from centinela.security.auth import get_auth_manager

    auth = get_auth_manager()
    config = get_config()
    token = auth.create_token(subject=req.subject)
    return TokenResponse(
        token=token,
        expires_in_minutes=config.gateway.auth.token_ttl_minutes,
    )


# ─── Chat ───


@router.post("/chat")
async def chat_stream(req: ChatRequest, user: str = Depends(require_auth)):
    """Chat with streaming SSE response."""
    from centinela.core.orchestrator import get_orchestrator

    orch = get_orchestrator()
    chunks = orch.stream_chat(req.message)

    return StreamingResponse(
        async_sse_stream(chunks),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/sync", response_model=ChatResponse)
async def chat_sync(req: ChatRequest, user: str = Depends(require_auth)):
    """Synchronous chat response (no streaming)."""
    from centinela.core.orchestrator import get_orchestrator

    orch = get_orchestrator()
    response = orch.chat(req.message)
    return ChatResponse(text=response)


# ─── WebSocket Chat ───


@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """WebSocket endpoint for real-time chat."""
    await websocket.accept()

    from centinela.core.orchestrator import get_orchestrator
    orch = get_orchestrator()

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                user_message = msg.get("message", data)
            except json.JSONDecodeError:
                user_message = data

            # Stream response chunks
            for chunk in orch.stream_chat(user_message):
                await websocket.send_json({"type": "chunk", "text": chunk})
            await websocket.send_json({"type": "done"})

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error("WebSocket error: %s", e)
        try:
            await websocket.send_json({"type": "error", "text": str(e)})
        except Exception:
            pass


# ─── Models ───


@router.get("/models")
async def get_models(user: str = Depends(require_auth)):
    from centinela.core.models import get_model_resolver
    resolver = get_model_resolver()
    return resolver.get_status()


# ─── Agents ───


@router.get("/agents")
async def get_agents(user: str = Depends(require_auth)):
    from centinela.core.orchestrator import get_orchestrator
    orch = get_orchestrator()
    info = orch.get_status()
    return info["agents"]


# ─── Audit ───


@router.get("/audit")
async def get_audit(
    limit: int = 50,
    user: str = Depends(require_auth),
):
    from centinela.security.audit import get_audit_logger
    audit = get_audit_logger()
    return audit.get_recent(limit=limit)


# ─── Sessions / Memory ───


@router.get("/sessions")
async def get_sessions(user: str = Depends(require_auth)):
    from centinela.core.memory import get_memory_manager
    mem = get_memory_manager()
    return {
        "episodic": mem.episodic.get_stats(),
        "preferences": mem.preferences.get_all(),
    }


# ─── Approval ───


@router.get("/pending")
async def get_pending(user: str = Depends(require_auth)):
    from centinela.security.approval import get_approval_manager
    mgr = get_approval_manager()
    pending = mgr.get_pending()
    return [
        {
            "request_id": r.request_id,
            "agent_id": r.agent_id,
            "tool_name": r.tool_name,
            "command": r.command,
            "reason": r.reason,
            "age_seconds": int(r.age_seconds),
        }
        for r in pending
    ]


@router.post("/approve/{request_id}")
async def approve_action(
    request_id: str,
    action: ApprovalAction,
    user: str = Depends(require_auth),
):
    from centinela.security.approval import get_approval_manager
    mgr = get_approval_manager()
    if mgr.approve(request_id, decided_by=action.decided_by):
        return {"status": "approved", "request_id": request_id}
    raise HTTPException(status_code=404, detail="Request not found")


@router.post("/reject/{request_id}")
async def reject_action(
    request_id: str,
    action: ApprovalAction,
    user: str = Depends(require_auth),
):
    from centinela.security.approval import get_approval_manager
    mgr = get_approval_manager()
    if mgr.reject(request_id, decided_by=action.decided_by):
        return {"status": "rejected", "request_id": request_id}
    raise HTTPException(status_code=404, detail="Request not found")
