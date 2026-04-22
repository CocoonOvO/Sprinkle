"""User API endpoints - database-backed implementation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from sprinkle.api.auth import get_user_by_id
from sprinkle.api.dependencies import get_current_user
from sprinkle.kernel.auth import UserCredentials
from sprinkle.models.user import User, UserType
from sprinkle.storage.database import SessionLocal

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================

class UserResponse(BaseModel):
    """User response schema."""
    id: str
    username: str
    display_name: str
    user_type: str
    metadata: Dict[str, Any] = {}
    created_at: datetime

    model_config = {"from_attributes": True}


class UpdateUserRequest(BaseModel):
    """Update user request schema."""
    display_name: str | None = Field(None, max_length=100)
    metadata: Dict[str, Any] | None = None


# ============================================================================
# In-Memory Metadata Store (kept for test compatibility)
# ============================================================================

_user_metadata: Dict[str, Dict[str, Any]] = {}


def get_user_metadata_store() -> Dict[str, Dict[str, Any]]:
    """Get user metadata store."""
    return _user_metadata


def clear_user_metadata() -> None:
    """Clear user metadata store."""
    _user_metadata.clear()


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
) -> UserResponse:
    """Get current authenticated user information.
    
    Requires Bearer token authentication.
    """
    # Fetch latest user data from database
    db_user = get_user_by_id(current_user.user_id)
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    
    # Parse extra_data as JSON for metadata
    extra_data = {}
    try:
        import json
        if db_user.extra_data:
            extra_data = json.loads(db_user.extra_data)
    except Exception:
        pass
    
    return UserResponse(
        id=db_user.id,
        username=db_user.username,
        display_name=db_user.display_name,
        user_type="agent" if db_user.user_type == UserType.agent else "human",
        metadata=extra_data,
        created_at=db_user.created_at,
    )


@router.put(
    "/me",
    response_model=UserResponse,
    summary="Update current user",
)
async def update_me(
    request: UpdateUserRequest,
    current_user: UserCredentials = Depends(get_current_user),
) -> UserResponse:
    """Update current user's profile.
    
    - **display_name**: New display name (optional)
    - **metadata**: Additional metadata (optional, merged with existing)
    """
    import json
    
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == current_user.user_id).first()
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
            try:
                if user.extra_data:
                    current_extra = json.loads(user.extra_data)
            except Exception:
                pass
            current_extra.update(request.metadata)
            # Pass dict directly - SQLAlchemy will handle JSON serialization
            user.extra_data = current_extra
        
        user.updated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(user)
        db.expunge(user)
        
        # Parse extra_data for response
        extra_data = {}
        try:
            if user.extra_data:
                extra_data = json.loads(user.extra_data)
        except Exception:
            pass
        
        return UserResponse(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            user_type="agent" if user.user_type == UserType.agent else "human",
            metadata=extra_data,
            created_at=user.created_at,
        )
    finally:
        db.close()
