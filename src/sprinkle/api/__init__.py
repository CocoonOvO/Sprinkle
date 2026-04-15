"""API module - REST API layer."""

from __future__ import annotations

from fastapi import APIRouter

from sprinkle.api.auth import router as auth_router
from sprinkle.api.users import router as users_router
from sprinkle.api.conversations import router as conversations_router
from sprinkle.api.messages import conversation_messages_router, message_ops_router
from sprinkle.api.members import router as members_router
from sprinkle.api.files import router as files_router
from sprinkle.api.events import router as events_router

__version__ = "0.1.0"

# API v1 router
api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(users_router, prefix="/users", tags=["users"])
api_router.include_router(conversations_router, prefix="/conversations", tags=["conversations"])
api_router.include_router(conversation_messages_router, prefix="/conversations", tags=["messages"])
api_router.include_router(message_ops_router, prefix="/messages", tags=["messages"])
api_router.include_router(members_router, prefix="/conversations", tags=["members"])
api_router.include_router(files_router, prefix="/files", tags=["files"])
api_router.include_router(events_router, tags=["events"])

# WebSocket router (separate, mounted directly on app)
from sprinkle.api.websocket import router as websocket_router

__all__ = [
    "api_router",
    "websocket_router",
    "auth_router",
    "users_router",
    "conversations_router",
    "conversation_messages_router",
    "message_ops_router",
    "members_router",
    "files_router",
    "events_router",
]
