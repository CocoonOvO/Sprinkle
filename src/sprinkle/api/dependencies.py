"""Common dependencies for API endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from sprinkle.kernel.auth import AuthService, UserCredentials, TokenData
from sprinkle.storage.database import get_async_session

# Bearer Token security scheme
security = HTTPBearer()

# Global AuthService instance (lazy initialization)
_auth_service: Optional[AuthService] = None


def get_auth_service() -> AuthService:
    """Get or create AuthService instance."""
    global _auth_service
    if _auth_service is None:
        from sprinkle.config import settings
        _auth_service = AuthService(settings)
    return _auth_service


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    auth_service: AuthService = Depends(get_auth_service),
) -> UserCredentials:
    """Get current authenticated user.
    
    Raises:
        HTTPException: 401 if token is invalid or expired
    """
    token = credentials.credentials
    
    # Verify token
    token_data: Optional[TokenData] = auth_service.verify_token(token, token_type="access")
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Look up user in database
    from sprinkle.api.auth import get_user_by_id
    from sprinkle.models.user import User, UserType
    
    db_user = get_user_by_id(token_data.user_id)
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Convert to UserCredentials
    return UserCredentials(
        user_id=db_user.id,
        username=db_user.username,
        password_hash=db_user.password_hash,
        disabled=False,
        is_agent=db_user.user_type == UserType.agent,
        permissions=[],
    )


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(
        HTTPBearer(auto_error=False)
    ),
    auth_service: AuthService = Depends(get_auth_service),
) -> Optional[UserCredentials]:
    """Get current user if authenticated, None otherwise.
    
    Used for public endpoints that work differently based on auth state.
    """
    if not credentials:
        return None
    
    token_data: Optional[TokenData] = auth_service.verify_token(
        credentials.credentials, token_type="access"
    )
    if not token_data:
        return None
    
    from sprinkle.api.auth import get_user_by_id
    from sprinkle.models.user import User, UserType
    
    db_user = get_user_by_id(token_data.user_id)
    if not db_user:
        return None
    
    return UserCredentials(
        user_id=db_user.id,
        username=db_user.username,
        password_hash=db_user.password_hash,
        disabled=False,
        is_agent=db_user.user_type == UserType.agent,
        permissions=[],
    )


async def get_db_session():
    """Get database session (placeholder for future integration)."""
    async for session in get_async_session():
        yield session
