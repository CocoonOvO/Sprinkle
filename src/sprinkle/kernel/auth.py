"""Auth Service - JWT token management and user authentication."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from typing import (
    Any,
    Dict,
    List,
    Optional,
)

from jose import JWTError, jwt
from passlib.context import CryptContext

from sprinkle.config import Settings

logger = logging.getLogger(__name__)


# ============================================================================
# Configuration
# ============================================================================

# Default JWT configuration
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


# ============================================================================
# Password Context
# ============================================================================

# Bcrypt password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ============================================================================
# Token Data
# ============================================================================

@dataclass
class TokenData:
    """Token payload data.
    
    Attributes:
        sub: Subject (user_id)
        exp: Expiration time
        iat: Issued at time
        type: Token type ('access' or 'refresh')
        metadata: Additional token metadata
    """
    sub: str
    exp: datetime
    iat: datetime
    type: str = "access"
    metadata: Dict[str, Any] = None
    
    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}
    
    @property
    def user_id(self) -> str:
        """Get user ID from subject."""
        return self.sub


@dataclass
class UserCredentials:
    """User authentication credentials.
    
    Attributes:
        user_id: User identifier
        username: Username
        password_hash: Hashed password
        disabled: Whether user is disabled
        is_agent: Whether user is an agent
        permissions: List of permissions
    """
    user_id: str
    username: str
    password_hash: str
    disabled: bool = False
    is_agent: bool = False
    permissions: List[str] = None
    
    def __post_init__(self) -> None:
        if self.permissions is None:
            self.permissions = []


# ============================================================================
# Auth Service
# ============================================================================

class AuthService:
    """Authentication and authorization service.
    
    Features:
    - JWT token generation and verification
    - Password hashing with bcrypt
    - User authentication
    - Token refresh
    
    Example:
        auth = AuthService()
        
        # Hash password
        hashed = auth.hash_password("my_password")
        
        # Verify password
        if auth.verify_password("my_password", hashed):
            print("Password valid")
        
        # Generate tokens
        tokens = auth.create_tokens("user_123")
        
        # Verify token
        data = auth.verify_token(tokens["access_token"])
    """
    
    def __init__(
        self,
        settings: Optional[Settings] = None,
        secret_key: Optional[str] = None,
        jwt_algorithm: str = JWT_ALGORITHM,
        access_token_expire_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES,
        refresh_token_expire_days: int = REFRESH_TOKEN_EXPIRE_DAYS,
    ):
        """Initialize AuthService.
        
        Args:
            settings: Application settings (for secret key)
            secret_key: JWT secret key (overrides settings)
            jwt_algorithm: JWT signing algorithm (default: HS256)
            access_token_expire_minutes: Access token TTL (default: 30)
            refresh_token_expire_days: Refresh token TTL (default: 7)
        """
        self._settings = settings
        
        # Get secret key
        if secret_key:
            self._secret_key = secret_key
        elif settings:
            # Derive from app settings or database password
            app_name = settings.app.name if settings.app else "Sprinkle"
            db_pass = settings.database.password if settings.database else ""
            self._secret_key = f"{app_name}_secret_{db_pass}"[:32]
        else:
            # Generate a random key (not recommended for production)
            self._secret_key = secrets.token_hex(32)
        
        self._jwt_algorithm = jwt_algorithm
        self._access_token_expire_minutes = access_token_expire_minutes
        self._refresh_token_expire_days = refresh_token_expire_days
        
        # User store (in-memory, replace with DB in production)
        self._users: Dict[str, UserCredentials] = {}
        
        # Token blacklist (for logout/revocation)
        self._token_blacklist: set = set()
    
    # ------------------------------------------------------------------------
    # Password Operations
    # ------------------------------------------------------------------------
    
    def hash_password(self, password: str) -> str:
        """Hash a password using bcrypt.
        
        Args:
            password: Plain text password
            
        Returns:
            Hashed password
        """
        return pwd_context.hash(password)
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash.
        
        Args:
            plain_password: Plain text password to verify
            hashed_password: Stored hash to verify against
            
        Returns:
            True if password matches
        """
        try:
            return pwd_context.verify(plain_password, hashed_password)
        except Exception as e:
            logger.error(f"Password verification error: {e}")
            return False
    
    # ------------------------------------------------------------------------
    # Token Operations
    # ------------------------------------------------------------------------
    
    def create_access_token(
        self,
        user_id: str,
        expires_delta: Optional[timedelta] = None,
        additional_claims: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create a JWT access token.
        
        Args:
            user_id: User identifier (subject)
            expires_delta: Optional custom expiration time
            additional_claims: Additional JWT claims
            
        Returns:
            Encoded JWT token
        """
        now = datetime.now(timezone.utc)
        
        if expires_delta:
            expire = now + expires_delta
        else:
            expire = now + timedelta(minutes=self._access_token_expire_minutes)
        
        payload = {
            "sub": user_id,
            "exp": expire,
            "iat": now,
            "type": "access",
        }
        
        if additional_claims:
            payload.update(additional_claims)
        
        token = jwt.encode(payload, self._secret_key, algorithm=self._jwt_algorithm)
        
        logger.debug(f"Created access token for user {user_id}")
        return token
    
    def create_refresh_token(
        self,
        user_id: str,
        expires_delta: Optional[timedelta] = None,
    ) -> str:
        """Create a JWT refresh token.
        
        Args:
            user_id: User identifier (subject)
            expires_delta: Optional custom expiration time
            
        Returns:
            Encoded JWT refresh token
        """
        now = datetime.now(timezone.utc)
        
        if expires_delta:
            expire = now + expires_delta
        else:
            expire = now + timedelta(days=self._refresh_token_expire_days)
        
        payload = {
            "sub": user_id,
            "exp": expire,
            "iat": now,
            "type": "refresh",
        }
        
        token = jwt.encode(payload, self._secret_key, algorithm=self._jwt_algorithm)
        
        logger.debug(f"Created refresh token for user {user_id}")
        return token
    
    def create_tokens(
        self,
        user_id: str,
        access_expires_delta: Optional[timedelta] = None,
        refresh_expires_delta: Optional[timedelta] = None,
        additional_claims: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """Create both access and refresh tokens.
        
        Args:
            user_id: User identifier (subject)
            access_expires_delta: Optional custom access token expiration
            refresh_expires_delta: Optional custom refresh token expiration
            additional_claims: Additional JWT claims (for access token)
            
        Returns:
            Dictionary with 'access_token' and 'refresh_token'
        """
        return {
            "access_token": self.create_access_token(
                user_id,
                access_expires_delta,
                additional_claims,
            ),
            "refresh_token": self.create_refresh_token(
                user_id,
                refresh_expires_delta,
            ),
            "token_type": "bearer",
        }
    
    def verify_token(
        self,
        token: str,
        token_type: str = "access",
    ) -> Optional[TokenData]:
        """Verify and decode a JWT token.
        
        Args:
            token: JWT token to verify
            token_type: Expected token type ('access' or 'refresh')
            
        Returns:
            TokenData if valid, None otherwise
        """
        # Check blacklist
        if token in self._token_blacklist:
            logger.warning("Token is blacklisted")
            return None
        
        try:
            payload = jwt.decode(
                token,
                self._secret_key,
                algorithms=[self._jwt_algorithm],
            )
            
            # Check token type
            if payload.get("type") != token_type:
                logger.warning(f"Token type mismatch: expected {token_type}")
                return None
            
            # Extract token data
            sub = payload.get("sub")
            exp = datetime.fromtimestamp(payload.get("exp"), tz=timezone.utc)
            iat = datetime.fromtimestamp(payload.get("iat"), tz=timezone.utc)
            
            if not sub:
                logger.warning("Token missing subject")
                return None
            
            # Create token data (excluding internal keys)
            metadata = {k: v for k, v in payload.items()
                       if k not in ("sub", "exp", "iat", "type")}
            
            return TokenData(
                sub=sub,
                exp=exp,
                iat=iat,
                type=payload.get("type", token_type),
                metadata=metadata,
            )
            
        except jwt.ExpiredSignatureError:
            logger.warning("Token has expired")
            return None
        except JWTError as e:
            logger.error(f"Token verification failed: {e}")
            return None
    
    def refresh_access_token(self, refresh_token: str) -> Optional[Dict[str, str]]:
        """Refresh an access token using a refresh token.
        
        Args:
            refresh_token: Valid refresh token
            
        Returns:
            New tokens if refresh successful, None otherwise
        """
        token_data = self.verify_token(refresh_token, token_type="refresh")
        
        if not token_data:
            return None
        
        # Optionally blacklist old refresh token
        # self._token_blacklist.add(refresh_token)
        
        return {
            "access_token": self.create_access_token(token_data.user_id),
            "refresh_token": refresh_token,  # Return same refresh token
            "token_type": "bearer",
        }
    
    def revoke_token(self, token: str) -> bool:
        """Revoke a token (add to blacklist).
        
        Args:
            token: Token to revoke
            
        Returns:
            True if revoked successfully
        """
        token_data = self.verify_token(token)
        if not token_data:
            return False
        
        self._token_blacklist.add(token)
        logger.info(f"Token revoked for user {token_data.user_id}")
        return True
    
    def is_token_blacklisted(self, token: str) -> bool:
        """Check if a token is blacklisted.
        
        Args:
            token: Token to check
            
        Returns:
            True if blacklisted
        """
        return token in self._token_blacklist
    
    # ------------------------------------------------------------------------
    # User Management (Simplified - replace with DB in production)
    # ------------------------------------------------------------------------
    
    async def register_user(
        self,
        username: str,
        password: str,
        user_id: Optional[str] = None,
        is_agent: bool = False,
    ) -> Optional[UserCredentials]:
        """Register a new user.
        
        Writes to both in-memory cache AND database for consistency.
        
        Args:
            username: Username (must be unique)
            password: Plain text password
            user_id: Optional user ID (generated if not provided)
            is_agent: Whether this is an agent user
            
        Returns:
            UserCredentials if registered, None if username exists
        """
        # Check if username exists (check both in-memory and database)
        from sprinkle.models import User, UserType
        from sprinkle.storage.database import SessionLocal
        
        db = SessionLocal()
        try:
            # Check database
            existing = db.query(User).filter(User.username == username).first()
            if existing:
                logger.warning(f"Username already exists in DB: {username}")
                return None
            
            # Also check in-memory
            for user in self._users.values():
                if user.username == username:
                    logger.warning(f"Username already exists in memory: {username}")
                    return None
            
            # Generate user ID
            if not user_id:
                import uuid
                user_id = str(uuid.uuid4())
            
            # Hash password
            password_hash = self.hash_password(password)
            
            # Create user in database
            db_user = User(
                id=user_id,
                username=username,
                password_hash=password_hash,
                display_name=username,
                user_type=UserType.agent if is_agent else UserType.human,
                extra_data={},
            )
            db.add(db_user)
            db.commit()
            
            # Create user credentials for in-memory cache
            user = UserCredentials(
                user_id=user_id,
                username=username,
                password_hash=password_hash,
                is_agent=is_agent,
            )
            
            self._users[user_id] = user
            
            logger.info(f"User registered: {username} (id: {user_id})")
            return user
        except Exception as e:
            db.rollback()
            raise e
        finally:
            db.close()
    
    async def authenticate(
        self,
        username: str,
        password: str,
    ) -> Optional[UserCredentials]:
        """Authenticate a user with username and password.
        
        Args:
            username: Username
            password: Plain text password
            
        Returns:
            UserCredentials if authenticated, None otherwise
        """
        # Find user by username
        user = None
        for u in self._users.values():
            if u.username == username:
                user = u
                break
        
        if not user:
            logger.warning(f"User not found: {username}")
            return None
        
        # Check if disabled
        if user.disabled:
            logger.warning(f"User disabled: {username}")
            return None
        
        # Verify password
        if not self.verify_password(password, user.password_hash):
            logger.warning(f"Invalid password for user: {username}")
            return None
        
        logger.info(f"User authenticated: {username}")
        return user
    
    async def authenticate_token(
        self,
        token: str,
    ) -> Optional[UserCredentials]:
        """Authenticate using a JWT token.
        
        Args:
            token: JWT access token
            
        Returns:
            UserCredentials if token valid, None otherwise
        """
        token_data = self.verify_token(token)
        if not token_data:
            return None
        
        # Look up user in database instead of in-memory dict
        user = self._get_user_by_id_from_db(token_data.user_id)
        if not user:
            return None
        
        if user.disabled:
            return None
        
        return user
    
    def _get_user_by_id_from_db(self, user_id: str) -> Optional[UserCredentials]:
        """Get user by ID from database.
        
        Args:
            user_id: User identifier
            
        Returns:
            UserCredentials if found, None otherwise
        """
        from sprinkle.models import User, UserType
        from sprinkle.storage.database import SessionLocal
        
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                return None
            
            return UserCredentials(
                user_id=user.id,
                username=user.username,
                password_hash=user.password_hash,
                disabled=False,  # We check disabled status separately
                is_agent=user.user_type == UserType.agent,
                permissions=[],  # Permissions are not stored per-user
            )
        finally:
            db.close()
    
    async def get_user(self, user_id: str) -> Optional[UserCredentials]:
        """Get user by ID.
        
        Args:
            user_id: User identifier
            
        Returns:
            UserCredentials if found, None otherwise
        """
        return self._users.get(user_id)
    
    async def get_user_by_username(self, username: str) -> Optional[UserCredentials]:
        """Get user by username.
        
        Args:
            username: Username
            
        Returns:
            UserCredentials if found, None otherwise
        """
        for user in self._users.values():
            if user.username == username:
                return user
        return None
    
    async def update_user(
        self,
        user_id: str,
        disabled: Optional[bool] = None,
        permissions: Optional[List[str]] = None,
    ) -> Optional[UserCredentials]:
        """Update user properties.
        
        Args:
            user_id: User identifier
            disabled: New disabled status
            permissions: New permissions list
            
        Returns:
            Updated UserCredentials if found, None otherwise
        """
        user = self._users.get(user_id)
        if not user:
            return None
        
        if disabled is not None:
            user.disabled = disabled
        if permissions is not None:
            user.permissions = permissions
        
        return user
    
    async def delete_user(self, user_id: str) -> bool:
        """Delete a user.
        
        Args:
            user_id: User identifier
            
        Returns:
            True if deleted, False if not found
        """
        if user_id in self._users:
            del self._users[user_id]
            logger.info(f"User deleted: {user_id}")
            return True
        return False
    
    # ------------------------------------------------------------------------
    # Permission Checking
    # ------------------------------------------------------------------------
    
    def has_permission(
        self,
        user: UserCredentials,
        permission: str,
    ) -> bool:
        """Check if user has a specific permission.
        
        Args:
            user: User credentials
            permission: Permission string to check
            
        Returns:
            True if user has permission
        """
        # Agents have all permissions by default
        if user.is_agent:
            return True
        
        return permission in user.permissions
    
    def has_any_permission(
        self,
        user: UserCredentials,
        permissions: List[str],
    ) -> bool:
        """Check if user has any of the specified permissions.
        
        Args:
            user: User credentials
            permissions: List of permission strings
            
        Returns:
            True if user has any permission
        """
        return any(self.has_permission(user, p) for p in permissions)
    
    def has_all_permissions(
        self,
        user: UserCredentials,
        permissions: List[str],
    ) -> bool:
        """Check if user has all of the specified permissions.
        
        Args:
            user: User credentials
            permissions: List of permission strings
            
        Returns:
            True if user has all permissions
        """
        return all(self.has_permission(user, p) for p in permissions)
    
    # ------------------------------------------------------------------------
    # Token Introspection (for API endpoints)
    # ------------------------------------------------------------------------
    
    def introspect_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Introspect a token (RFC 7662 style).
        
        Args:
            token: Token to introspect
            
        Returns:
            Token metadata if active, None otherwise
        """
        token_data = self.verify_token(token)
        if not token_data:
            return {"active": False}
        
        user = self._users.get(token_data.user_id)
        
        return {
            "active": True,
            "sub": token_data.user_id,
            "type": token_data.type,
            "exp": token_data.exp.isoformat(),
            "iat": token_data.iat.isoformat(),
            "username": user.username if user else None,
            "scope": " ".join(user.permissions) if user else "",
            "is_agent": user.is_agent if user else False,
        }
