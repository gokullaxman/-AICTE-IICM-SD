import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, Request, HTTPException, status

from app.core.security import get_current_user
from app.schemas.payloads import ChatMessageRequest, ChatMessageResponse
from app.services.gemini_chat_service import gemini_chat_service

logger = logging.getLogger("aicte_iicm.chat_endpoint")

router = APIRouter()


@router.post("/message", response_model=ChatMessageResponse)
async def send_chat_message(
    payload: ChatMessageRequest,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Secure conversational AI endpoint for the AICTE IICM-SD assistant.
    Requires authenticated officer session (JWT Bearer token).
    Enforces per-officer rate limiting, request validation, and safe error conversion.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"

    # Forward to Gemini chat service
    response = await gemini_chat_service.generate_reply(
        message=payload.message,
        history=payload.history,
        user=current_user,
        client_ip=client_ip,
        conversation_id=payload.conversation_id
    )

    return response

