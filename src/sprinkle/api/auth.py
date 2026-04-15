"""Authentication API endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from sprinkle.kernel.auth import AuthService, UserCredentials
from sprinkle.api.dependencies import get_auth_service

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================

class RegisterRequest(BaseModel):
    """Register request schema."""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)
    display_name: str | None = Field(None, max_length=100)
    is_agent: bool = False


class RegisterResponse(BaseModel):
    """Register response schema."""
    id: str
    username: str
    display_name: str
    user_type: str
    created_at: datetime

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    """Login request schema."""
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    """Token response schema."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 1800  # 30 minutes


class RefreshRequest(BaseModel):
    """Refresh token request schema."""
    refresh_token: str


class UserResponse(BaseModel):
    """User response schema."""
    id: str
    username: str
    display_name: str
    user_type: str
    metadata: Dict[str, Any] = {}
    created_at: datetime

    model_config = {"from_attributes": True}


# ============================================================================
# In-Memory User Store
# ============================================================================

# Store for registered users: user_id -> UserCredentials
_registered_users: Dict[str, UserCredentials] = {}


def get_registered_users() -> Dict[str, UserCredentials]:
    """Get the registered users store."""
    return _registered_users


def clear_registered_users() -> None:
    """Clear all registered users (for testing)."""
    _registered_users.clear()


# ============================================================================
# API Endpoints
# ============================================================================

@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register(
    request: RegisterRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> RegisterResponse:
    """Register a new user account.
    
    - **username**: Unique username (3-50 characters)
    - **password**: Password (6-100 characters)
    - **display_name**: Optional display name
    - **is_agent**: Whether this is an agent user
    """
    # Check if username already exists
    existing = await auth_service.get_user_by_username(request.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )
    
    # Generate user ID
    user_id = str(uuid4())
    
    # Register user in auth service
    user = await auth_service.register_user(
        username=request.username,
        password=request.password,
        user_id=user_id,
        is_agent=request.is_agent,
    )
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register user",
        )
    
    # Store in registered users
    _registered_users[user_id] = user
    
    return RegisterResponse(
        id=user.user_id,
        username=user.username,
        display_name=request.display_name or user.username,
        user_type="agent" if user.is_agent else "human",
        created_at=datetime.now(timezone.utc),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login",
)
async def login(
    request: LoginRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Authenticate with username and password.
    
    Returns access token and refresh token.
    """
    user = await auth_service.authenticate(request.username, request.password)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create tokens
    tokens = auth_service.create_tokens(user.user_id)
    
    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type="bearer",
        expires_in=1800,  # 30 minutes
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
)
async def refresh_token(
    request: RefreshRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Refresh access token using refresh token.
    
    - **refresh_token**: Valid refresh token from login
    """
    tokens = auth_service.refresh_access_token(request.refresh_token)
    
    if not tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type="bearer",
        expires_in=1800,
    )
