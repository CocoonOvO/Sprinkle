"""User API endpoints - database-backed implementation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sprinkle.api.auth import get_user_by_id
from sprinkle.api.dependencies import get_current_user, get_db_session
from sprinkle.kernel.auth import UserCredentials
from sprinkle.models.user import User, UserType
from sprinkle.models.file import File

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================

class AvatarInfo(BaseModel):
    """Avatar info schema."""
    file_id: str
    file_name: str
    mime_type: str
    file_size: int
    url: str  # 完整的访问URL


class UserResponse(BaseModel):
    """User response schema."""
    id: str
    username: str
    display_name: str
    user_type: str  # "human" | "agent"
    avatar: AvatarInfo | None = None  # 头像信息，而非 avatar_id
    metadata: Dict[str, Any] = {}  # 统一用 metadata，不再用 extra_data
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class UpdateUserRequest(BaseModel):
    """Update user request schema."""
    display_name: str | None = Field(None, max_length=100)
    metadata: Dict[str, Any] | None = None  # 统一用 metadata


class SetAvatarRequest(BaseModel):
    """Set avatar request schema."""
    file_id: str


class RemoveAvatarResponse(BaseModel):
    """Remove avatar response schema."""
    message: str


class UserListResponse(BaseModel):
    """User list response schema."""
    items: list[UserResponse]
    total: int


# ============================================================================
# Helper Functions
# ============================================================================

def _build_avatar_info(file_record: File, base_url: str = "") -> AvatarInfo:
    """Build avatar info from File record."""
    return AvatarInfo(
        file_id=file_record.id,
        file_name=file_record.file_name,
        mime_type=file_record.mime_type or "application/octet-stream",
        file_size=file_record.file_size,
        url=f"{base_url}/api/v1/files/{file_record.id}" if base_url else f"/api/v1/files/{file_record.id}",
    )


async def _user_to_response(
    db: AsyncSession,
    user: User,
    base_url: str = "",
) -> UserResponse:
    """Convert User model to UserResponse with avatar info."""
    # Parse extra_data as metadata
    extra_data = {}
    if user.extra_data:
        if isinstance(user.extra_data, dict):
            extra_data = user.extra_data
        else:
            try:
                import json
                extra_data = json.loads(user.extra_data)
            except Exception:
                extra_data = {}
    
    # Load avatar file info if exists
    avatar_info = None
    if user.avatar_id:
        result = await db.execute(select(File).where(File.id == user.avatar_id))
        avatar_file = result.scalar_one_or_none()
        if avatar_file:
            avatar_info = _build_avatar_info(avatar_file, base_url)
    
    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        user_type="agent" if user.user_type == UserType.agent else "human",
        avatar=avatar_info,
        metadata=extra_data,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


# ============================================================================
# API Endpoints
# ============================================================================

@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user",
)
async def get_me(
    current_user: UserCredentials = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    """Get current authenticated user information.
    
    Requires Bearer token authentication.
    """
    db_user = get_user_by_id(current_user.user_id)
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    
    return await _user_to_response(db, db_user)


@router.put(
    "/me",
    response_model=UserResponse,
    summary="Update current user",
)
async def update_me(
    request: UpdateUserRequest,
    current_user: UserCredentials = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    """Update current user's profile.
    
    - **display_name**: New display name (optional)
    - **metadata**: Additional metadata (optional, merged with existing)
    """
    result = await db.execute(select(User).where(User.id == current_user.user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    
    # Update display_name if provided
    if request.display_name is not None:
        user.display_name = request.display_name
    
    # Merge metadata if provided
    if request.metadata is not None:
        current_extra = {}
        if user.extra_data:
            if isinstance(user.extra_data, dict):
                current_extra = user.extra_data
            else:
                try:
                    import json
                    current_extra = json.loads(user.extra_data)
                except Exception:
                    pass
        current_extra.update(request.metadata)
        user.extra_data = current_extra
    
    user.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(user)
    
    return await _user_to_response(db, user)


@router.post(
    "/me/avatar",
    response_model=UserResponse,
    summary="Set avatar",
)
async def set_avatar(
    request: SetAvatarRequest,
    current_user: UserCredentials = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    """Set user avatar.
    
    - **file_id**: ID of an uploaded image file to use as avatar
    """
    # Verify file exists
    result = await db.execute(select(File).where(File.id == request.file_id))
    file_record = result.scalar_one_or_none()
    if file_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    
    # Verify file is an image
    if not file_record.mime_type or not file_record.mime_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Avatar must be an image file",
        )
    
    # Update user's avatar_id
    result = await db.execute(select(User).where(User.id == current_user.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    
    user.avatar_id = request.file_id
    user.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(user)
    
    return await _user_to_response(db, user)


@router.delete(
    "/me/avatar",
    response_model=RemoveAvatarResponse,
    summary="Remove avatar",
)
async def remove_avatar(
    current_user: UserCredentials = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> RemoveAvatarResponse:
    """Remove user's avatar (set to no avatar)."""
    result = await db.execute(select(User).where(User.id == current_user.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    
    user.avatar_id = None
    user.updated_at = datetime.utcnow()
    await db.commit()
    
    return RemoveAvatarResponse(message="Avatar removed successfully")


@router.get(
    "",
    response_model=UserListResponse,
    summary="List all users",
)
async def list_users(
    db: AsyncSession = Depends(get_db_session),
    limit: int = 100,
    offset: int = 0,
) -> UserListResponse:
    """List all users with pagination.
    
    - **limit**: Maximum number of users to return (default 100, max 500)
    - **offset**: Number of users to skip (default 0)
    """
    if limit > 500:
        limit = 500
    if limit < 1:
        limit = 1

    # Count total
    count_result = await db.execute(select(User))
    total = len(count_result.all())

    # Fetch users with pagination
    result = await db.execute(
        select(User).order_by(User.created_at.desc()).offset(offset).limit(limit)
    )
    users = result.scalars().all()

    items = [await _user_to_response(db, user) for user in users]

    return UserListResponse(items=items, total=total)


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get user by ID",
)
async def get_user(
    user_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    """Get user information by user ID.
    
    - **user_id**: User UUID
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    
    return await _user_to_response(db, user)


@router.get(
    "/{user_id}/avatar",
    summary="Get user avatar",
)
async def get_user_avatar(
    user_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    """Get user's avatar image.
    
    - **user_id**: User UUID
    """
    # Get user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    
    if not user.avatar_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User has no avatar",
        )
    
    # Get avatar file
    result = await db.execute(select(File).where(File.id == user.avatar_id))
    file_record = result.scalar_one_or_none()
    if file_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Avatar file not found",
        )
    
    # Read file content
    import pathlib
    file_path = pathlib.Path(file_record.file_path)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Avatar file not found on server",
        )
    
    with open(file_path, "rb") as f:
        content = f.read()
    
    return StreamingResponse(
        iter([content]),
        media_type=file_record.mime_type,
        headers={
            "Content-Disposition": f'inline; filename="{file_record.file_name}"',
            "Content-Length": str(file_record.file_size),
            "Cache-Control": "public, max-age=86400",  # 缓存1天
        },
    )
