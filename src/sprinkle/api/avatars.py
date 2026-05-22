"""Avatar API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sprinkle.api.auth import get_user_by_id
from sprinkle.api.dependencies import get_current_user
from sprinkle.kernel.auth import UserCredentials
from sprinkle.models.user import User
from sprinkle.storage.database import SessionLocal

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================

class AvatarResponse(BaseModel):
    """Avatar response schema."""
    user_id: str
    avatar_url: str

    model_config = {"from_attributes": True}


class UpdateAvatarByUrlRequest(BaseModel):
    """Update avatar by URL request schema."""
    avatar_url: str = Field(..., max_length=500, description="Avatar image URL")


# ============================================================================
# Helper Functions
# ============================================================================

def get_user_or_404(db: Session, user_id: str) -> User:
    """Get user by ID or raise 404."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


# ============================================================================
# API Endpoints
# ============================================================================

@router.get(
    "/{user_id}",
    response_model=AvatarResponse,
    summary="Get user avatar",
)
async def get_avatar(
    user_id: str,
    current_user: UserCredentials = Depends(get_current_user),
) -> AvatarResponse:
    """Get avatar URL for a specific user.
    
    Requires authentication.
    
    - **user_id**: Target user UUID
    """
    db = SessionLocal()
    try:
        user = get_user_or_404(db, user_id)
        return AvatarResponse(user_id=user.id, avatar_url=user.avatar_url or "")
    finally:
        db.close()


@router.put(
    "/me",
    response_model=AvatarResponse,
    summary="Update current user avatar (by URL)",
)
async def update_avatar_by_url(
    request: UpdateAvatarByUrlRequest,
    current_user: UserCredentials = Depends(get_current_user),
) -> AvatarResponse:
    """Update current user's avatar using a URL.
    
    - **avatar_url**: URL of the avatar image (max 500 characters)
    """
    db = SessionLocal()
    try:
        user = get_user_or_404(db, current_user.user_id)
        user.avatar_url = request.avatar_url
        db.commit()
        return AvatarResponse(user_id=user.id, avatar_url=user.avatar_url)
    finally:
        db.close()


@router.post(
    "/me/upload",
    response_model=AvatarResponse,
    summary="Upload and set user avatar",
)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: UserCredentials = Depends(get_current_user),
) -> AvatarResponse:
    """Upload an image file and set it as the current user's avatar.
    
    The file is stored using the existing file upload mechanism and the
    resulting file download URL is set as the user's avatar URL.
    
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
    
    # Upload via the existing files endpoint logic
    from uuid import uuid4
    from pathlib import Path
    
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
    
    # Save file record to database
    db = SessionLocal()
    try:
        from sprinkle.models.file import File as FileModel
        file_record = FileModel(
            id=file_id,
            uploader_id=current_user.user_id,
            file_name=file.filename or stored_filename,
            file_path=str(file_path),
            file_size=file_size,
            mime_type=mime_type,
        )
        db.add(file_record)
        
        # Update user avatar URL to the file download endpoint
        avatar_url = f"/api/v1/files/{file_id}"
        user = get_user_or_404(db, current_user.user_id)
        user.avatar_url = avatar_url
        db.commit()
        
        return AvatarResponse(user_id=user.id, avatar_url=avatar_url)
    finally:
        db.close()