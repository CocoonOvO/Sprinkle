"""Agent API Key management endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from sprinkle.kernel.auth import UserCredentials
from sprinkle.api.dependencies import get_current_user, get_db_session
from sprinkle.services.agent_key_service import AgentKeyService

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================

class CreateAgentKeyRequest(BaseModel):
    """Request to create an agent API key."""
    name: str  # e.g., "司康", "布莱妮"
    description: str | None = None


class AgentKeyResponse(BaseModel):
    """Response for agent API key operations."""
    id: str
    name: str
    description: str | None
    created_at: str
    last_used_at: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class AgentKeyCreatedResponse(BaseModel):
    """Response when an API key is created.
    
    This contains the full API key which is ONLY shown ONCE.
    """
    id: str
    name: str
    full_key: str  # The actual key - shown only once!
    message: str


# ============================================================================
# Endpoints
# ============================================================================

@router.post(
    "/agent/keys",
    response_model=AgentKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Agent API Key",
)
async def create_agent_api_key(
    request: CreateAgentKeyRequest,
    current_user: UserCredentials = Depends(get_current_user),
    db=Depends(get_db_session),
) -> AgentKeyCreatedResponse:
    """Create a new API key for an agent user.
    
    **IMPORTANT**: The `full_key` field in the response is shown ONLY ONCE.
    Store it securely - it cannot be retrieved again.
    
    Only agent users can have API keys.
    """
    # Only agents can have API keys
    if not current_user.is_agent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only agent users can have API keys",
        )
    
    # Check if user already has an active key (limit to 1 for now)
    service = AgentKeyService(db)
    existing_keys = await service.list_api_keys(current_user.user_id)
    active_keys = [k for k in existing_keys if k.is_active]
    
    if active_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent already has an active API key. Revoke it first.",
        )
    
    # Create the key
    # We need to get the full user object from the database
    from sqlalchemy import select
    from sprinkle.models import User
    
    result = await db.execute(
        select(User).where(User.id == current_user.user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    
    try:
        full_key, key_id = await service.create_api_key(
            user=user,
            name=request.name,
            description=request.description,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    
    # Get the created key info
    from sprinkle.models import AgentApiKey
    result = await db.execute(
        select(AgentApiKey).where(AgentApiKey.id == key_id)
    )
    api_key = result.scalar_one()
    
    return AgentKeyCreatedResponse(
        id=key_id,
        name=api_key.name,
        full_key=full_key,  # This is shown only once!
        message="Store this key securely. It will not be shown again.",
    )


@router.get(
    "/agent/keys",
    response_model=list[AgentKeyResponse],
    summary="List Agent API Keys",
)
async def list_agent_api_keys(
    current_user: UserCredentials = Depends(get_current_user),
    db=Depends(get_db_session),
) -> list[AgentKeyResponse]:
    """List all API keys for the current agent user.
    
    Note: The actual key secrets are not returned.
    """
    if not current_user.is_agent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only agent users can manage API keys",
        )
    
    service = AgentKeyService(db)
    keys = await service.list_api_keys(current_user.user_id)
    
    return [
        AgentKeyResponse(
            id=k.id,
            name=k.name,
            description=k.description,
            created_at=k.created_at.isoformat() if k.created_at else None,
            last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
            is_active=k.is_active,
        )
        for k in keys
    ]


@router.delete(
    "/agent/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke Agent API Key",
)
async def revoke_agent_api_key(
    key_id: str,
    current_user: UserCredentials = Depends(get_current_user),
    db=Depends(get_db_session),
) -> None:
    """Revoke an API key.
    
    Once revoked, the key can no longer be used for authentication.
    """
    if not current_user.is_agent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only agent users can manage API keys",
        )
    
    service = AgentKeyService(db)
    success = await service.revoke_api_key(key_id, current_user.user_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found or not owned by you",
        )
