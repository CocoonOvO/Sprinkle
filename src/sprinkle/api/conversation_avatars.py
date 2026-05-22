"""Conversation Avatar API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session
from uuid import uuid4
from pathlib import Path

from sprinkle.api.conversations import _check_db_admin_access
from sprinkle.api.dependencies import get_current_user
from sprinkle.kernel.auth import UserCredentials
from sprinkle.models import Conversation
from sprinkle.storage.database import SessionLocal
from sqlalchemy import select

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================

class ConversationAvatarResponse(BaseModel):
    """Conversation avatar response schema."""
    conversation_id: str
    avatar_url: str

    model_config = {"from_attributes": True}


class UpdateConversationAvatarByUrlRequest(BaseModel):
    """Update conversation avatar by URL request schema."""
    avatar_url: str


# ============================================================================
# Helper Functions
# ============================================================================

def _get_conversation_or_404(db: Session, conversation_id: str) -> Conversation:
    """Get conversation by ID or raise 404."""
    conv = db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    ).scalar_one_or_none()
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return conv


# ============================================================================
# API Endpoints
# ============================================================================

@router.get(
    "/conversations/{conversation_id}/avatar",
    response_model=ConversationAvatarResponse,
    summary="Get conversation avatar",
)
async def get_conversation_avatar(
    conversation_id: str,
    # No auth required - avatar URL is public, clients use it directly
) -> ConversationAvatarResponse:
    """Get avatar URL for a conversation.
    
    This endpoint is public - no authentication required.
    The returned avatar_url can be used directly by clients to fetch the image.
    """
    db = SessionLocal()
    try:
        conv = _get_conversation_or_404(db, conversation_id)
        return ConversationAvatarResponse(
            conversation_id=conv.id,
            avatar_url=conv.avatar_url or "",
        )
    finally:
        db.close()


@router.put(
    "/conversations/{conversation_id}/avatar",
    response_model=ConversationAvatarResponse,
    summary="Update conversation avatar (by URL)",
)
async def update_conversation_avatar_by_url(
    conversation_id: str,
    request: UpdateConversationAvatarByUrlRequest,
    current_user: UserCredentials = Depends(get_current_user),
) -> ConversationAvatarResponse:
    """Update a conversation's avatar using a URL.
    
    Requires admin or owner permission.
    """
    db = SessionLocal()
    try:
        _check_db_admin_access(conversation_id, current_user.user_id, db)
        conv = _get_conversation_or_404(db, conversation_id)
        conv.avatar_url = request.avatar_url
        db.commit()
        return ConversationAvatarResponse(
            conversation_id=conv.id,
            avatar_url=conv.avatar_url,
        )
    finally:
        db.close()


@router.post(
    "/conversations/{conversation_id}/avatar/upload",
    response_model=ConversationAvatarResponse,
    summary="Upload and set conversation avatar",
)
async def upload_conversation_avatar(
    conversation_id: str,
    file: UploadFile = File(...),
    current_user: UserCredentials = Depends(get_current_user),
) -> ConversationAvatarResponse:
    """Upload an image file and set it as the conversation's avatar.
    
    Requires admin or owner permission.
    
    - **conversation_id**: Target conversation UUID
    - **file**: Image file (png, jpg, jpeg, gif, webp; max 5MB)
    """
    # Validate file type
    allowed_types = {"image/png", "image/jpeg", "image/gif", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {file.content_type}. Allowed: {', '.join(sorted(allowed_types))}",
        )

    # Read file content
    content = await file.read()
    file_size = len(content)

    # Max 5MB
    max_size = 5 * 1024 * 1024
    if file_size > max_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large: {file_size} bytes. Max size: {max_size} bytes (5MB).",
        )

    # Upload via existing file storage
    file_id = str(uuid4())
    file_ext = Path(file.filename or "avatar.png").suffix if file.filename else ".png"
    stored_filename = f"{file_id}{file_ext}"

    from sprinkle.api.files import STORAGE_DIR
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    file_path = STORAGE_DIR / stored_filename

    with open(file_path, "wb") as f:
        f.write(content)

    from sprinkle.api.files import guess_mime_type
    mime_type = guess_mime_type(file.filename or stored_filename)

    # Save file record and update conversation avatar
    db = SessionLocal()
    try:
        _check_db_admin_access(conversation_id, current_user.user_id, db)
        conv = _get_conversation_or_404(db, conversation_id)

        from sprinkle.models.file import File as FileModel
        file_record = FileModel(
            id=file_id,
            uploader_id=current_user.user_id,
            file_name=file.filename or stored_filename,
            file_path=str(file_path),
            file_size=file_size,
            mime_type=mime_type,
            conversation_id=conversation_id,
        )
        db.add(file_record)

        avatar_url = f"/api/v1/files/{file_id}"
        conv.avatar_url = avatar_url
        db.commit()

        return ConversationAvatarResponse(
            conversation_id=conv.id,
            avatar_url=avatar_url,
        )
    finally:
        db.close()