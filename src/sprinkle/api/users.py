"""User API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from sprinkle.kernel.auth import AuthService, UserCredentials
from sprinkle.api.dependencies import get_current_user, get_auth_service

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
# In-Memory User Metadata Store
# ============================================================================

# Store for user metadata: user_id -> {"display_name": str, "metadata": dict, "updated_at": datetime}
_user_metadata: Dict[str, Dict[str, Any]] = {}


def get_user_metadata_store() -> Dict[str, Dict[str, Any]]:
    """Get user metadata store."""
    return _user_metadata


def clear_user_metadata() -> None:
    """Clear user metadata (for testing)."""
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
    # Get user metadata if exists
    meta = _user_metadata.get(current_user.user_id, {})
    display_name = meta.get("display_name", current_user.username)
    
    return UserResponse(
        id=current_user.user_id,
        username=current_user.username,
        display_name=display_name,
        user_type="agent" if current_user.is_agent else "human",
        metadata=meta.get("metadata", {}),
        created_at=datetime.now(timezone.utc),  # Placeholder
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
    # Get or create metadata entry
    if current_user.user_id not in _user_metadata:
        _user_metadata[current_user.user_id] = {
            "display_name": current_user.username,
            "metadata": {},
        }
    
    meta = _user_metadata[current_user.user_id]
    
    # Update fields if provided
    if request.display_name is not None:
        meta["display_name"] = request.display_name
    
    if request.metadata is not None:
        # Merge metadata
        meta["metadata"].update(request.metadata)
    
    meta["updated_at"] = datetime.now(timezone.utc)
    
    return UserResponse(
        id=current_user.user_id,
        username=current_user.username,
        display_name=meta["display_name"],
        user_type="agent" if current_user.is_agent else "human",
        metadata=meta["metadata"],
        created_at=datetime.now(timezone.utc),  # Placeholder
    )
