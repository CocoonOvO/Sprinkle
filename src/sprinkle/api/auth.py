"""Authentication API endpoints - database-backed implementation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from sprinkle.config import get_settings
from sprinkle.kernel.auth import AuthService, TokenData
from sprinkle.models.user import User, UserType
from sprinkle.storage.database import SessionLocal


# Import get_auth_service from dependencies to support FastAPI dependency injection
# This allows tests to override the auth service via app.dependency_overrides
from sprinkle.api.dependencies import get_auth_service


# ============================================================================
# Helpers
# ============================================================================

def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def create_tokens(user_id: str, username: str, auth_service=None) -> Dict[str, Any]:
    """Create access and refresh tokens using AuthService.
    
    Args:
        user_id: User identifier
        username: Username for additional claim
        auth_service: AuthService instance (uses global if not provided)
    """
    if auth_service is None:
        auth_service = get_auth_service()
    access_token_expires = timedelta(minutes=30)
    refresh_token_expires = timedelta(days=7)

    tokens = auth_service.create_tokens(
        user_id=user_id,
        access_expires_delta=access_token_expires,
        refresh_expires_delta=refresh_token_expires,
        additional_claims={"username": username},
    )

    return {
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "token_type": "bearer",
        "expires_in": 1800,  # 30 minutes
    }


# ============================================================================
# Pydantic Models
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
    user_id: str  # Added to support permission checks in tests


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
# Router
# ============================================================================

router = APIRouter()


# ============================================================================
# Database helpers
# ============================================================================

def get_user_by_username(username: str) -> Optional[User]:
    """Get user from database by username."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user:
            db.expunge(user)
        return user
    finally:
        db.close()


def get_user_by_id(user_id: str) -> Optional[User]:
    """Get user from database by ID."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            db.expunge(user)
        return user
    finally:
        db.close()


def create_user(
    username: str,
    password: str,
    display_name: str,
    is_agent: bool = False,
) -> User:
    """Create a new user in the database."""
    db = SessionLocal()
    try:
        user = User(
            id=str(uuid4()),
            username=username,
            password_hash=hash_password(password),
            display_name=display_name,
            user_type=UserType.agent if is_agent else UserType.human,
            extra_data="{}",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


# ============================================================================
# API Endpoints
# ============================================================================

@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register(request: RegisterRequest) -> RegisterResponse:
    """Register a new user account.

    - **username**: Unique username (3-50 characters)
    - **password**: Password (6-100 characters)
    - **display_name**: Optional display name
    - **is_agent**: Whether this is an agent user
    """
    # Check if username already exists
    existing = get_user_by_username(request.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )

    # Create user
    display_name = request.display_name or request.username
    user = create_user(
        username=request.username,
        password=request.password,
        display_name=display_name,
        is_agent=request.is_agent,
    )

    return RegisterResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        user_type="agent" if user.user_type == UserType.agent else "human",
        created_at=user.created_at,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login",
)
async def login(request: LoginRequest, auth_service: AuthService = Depends(get_auth_service)) -> TokenResponse:
    """Authenticate with username and password.

    Returns access token and refresh token.
    """
    user = get_user_by_username(request.username)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    tokens = create_tokens(user.id, user.username, auth_service)

    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type="bearer",
        expires_in=tokens["expires_in"],
        user_id=user.id,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
)
async def refresh_token(request: RefreshRequest, auth_service: AuthService = Depends(get_auth_service)) -> TokenResponse:
    """Refresh access token using refresh token.

    - **refresh_token**: Valid refresh token from login
    """
    token_data: Optional[TokenData] = auth_service.verify_token(
        request.refresh_token, token_type="refresh"
    )

    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = token_data.user_id

    # Verify user still exists
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create new tokens
    tokens = create_tokens(user_id, user.username, auth_service)

    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type="bearer",
        expires_in=tokens["expires_in"],
        user_id=user_id,
    )


# ============================================================================
# In-Memory User Store (legacy, kept for backward compatibility with tests)
# ============================================================================

# Store for registered users: user_id -> UserCredentials
_registered_users: Dict[str, Any] = {}


def get_registered_users() -> Dict[str, Any]:
    """Get the registered users store."""
    return _registered_users


def clear_registered_users() -> None:
    """Clear all registered users (for testing).

    Clears both in-memory store and database tables.
    Deletes in correct order to respect foreign key constraints:
    1. conversation_members (references users)
    2. messages (references users as sender)
    3. conversations (references users as owner)
    4. users
    """
    _registered_users.clear()
    # Also clear from database in correct order to respect foreign keys
    db = SessionLocal()
    try:
        from sprinkle.models import User, ConversationMember, Message, Conversation
        # Delete in correct order to respect foreign key constraints
        db.query(ConversationMember).delete()
        db.query(Message).delete()
        db.query(Conversation).delete()
        db.query(User).delete()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
