"""Agent API Key Service - HMAC-based authentication for agents."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sprinkle.models.agent_api_key import AgentApiKey
from sprinkle.models.user import User, UserType

logger = logging.getLogger(__name__)


# ============================================================================
# Configuration
# ============================================================================

# Password hashing for API secrets (for storage)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# HMAC configuration
HMAC_TIMESTAMP_WINDOW = 300  # ±5 minutes in seconds
NONCE_CACHE_SIZE = 10000  # Number of nonces to cache for replay prevention


# ============================================================================
# HMAC Authentication Result
# ============================================================================

@dataclass
class HmacAuthResult:
    """Result of HMAC authentication attempt."""
    success: bool
    message: str
    user: Optional[User] = None
    api_key: Optional[AgentApiKey] = None


# ============================================================================
# Nonce Cache (for replay attack prevention)
# ============================================================================

class NonceCache:
    """Cache of recently used nonces to prevent replay attacks.
    
    In a production environment with multiple servers, this should be
    replaced with Redis or similar distributed cache.
    """
    
    def __init__(self, max_size: int = NONCE_CACHE_SIZE):
        self._cache: set[str] = set()
        self._max_size = max_size
    
    def is_used(self, nonce: str) -> bool:
        """Check if nonce has been used."""
        return nonce in self._cache
    
    def add(self, nonce: str) -> None:
        """Mark nonce as used."""
        if len(self._cache) >= self._max_size:
            # Simple eviction: clear half when full
            self._cache.clear()
        self._cache.add(nonce)


# Global nonce cache instance
_nonce_cache = NonceCache()


# ============================================================================
# Agent API Key Service
# ============================================================================

class AgentKeyService:
    """Service for managing agent API keys and HMAC authentication.
    
    API Key Format:
        sk_<key_id>_<secret>
    
    Example:
        sk_01ABC123DEF456..._a1b2c3d4...
    
    Security Design:
        - Secret is generated once, shown once to user
        - bcrypt(secret) is stored for password-style verification
        - SHA256(secret) is stored as the HMAC verification key
        - Client computes: HMAC-SHA256(SHA256(secret), timestamp:nonce)
        - Server does the same with stored verification key
        
    Connection Flow:
        1. Client connects with: key_id, signature, timestamp, nonce
        2. Server validates timestamp within window (±5 minutes)
        3. Server validates nonce not reused (replay prevention)
        4. Server validates HMAC signature
        5. Server marks key as used and creates session
    """
    
    # Key ID prefix for identification
    KEY_PREFIX = "sk_"
    
    def __init__(self, db: AsyncSession):
        """Initialize service with database session.
        
        Args:
            db: Async database session
        """
        self._db = db
    
    # ------------------------------------------------------------------------
    # Key Generation
    # ------------------------------------------------------------------------
    
    def generate_key_id(self) -> str:
        """Generate a unique key ID.
        
        Returns:
            Key ID (e.g., "01ABC123DEF456...")
        """
        return uuid4().hex[:24].upper()
    
    def generate_secret(self) -> str:
        """Generate a random secret.
        
        Returns:
            256-bit random hex string
        """
        return secrets.token_hex(32)  # 256 bits
    
    def derive_hmac_key(self, secret: str) -> str:
        """Derive the HMAC key from the secret.
        
        We use SHA256 to derive a fixed-length key for HMAC operations.
        This allows us to verify HMAC without storing the original secret.
        
        Args:
            secret: The raw secret
            
        Returns:
            SHA256 hash of the secret (hex encoded)
        """
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()
    
    async def create_api_key(
        self,
        user: User,
        name: str,
        description: Optional[str] = None,
    ) -> tuple[str, str]:
        """Create a new API key for an agent user.
        
        This should only be called by admins or during initial setup.
        The full_key should be shown to the user ONLY ONCE and stored securely.
        
        Args:
            user: The agent user who will own this key
            name: Human-readable name (e.g., "司康")
            description: Optional description
            
        Returns:
            Tuple of (full_key, key_id) where full_key is shown only once
        """
        if user.user_type != UserType.agent:
            raise ValueError("API keys can only be created for agent users")
        
        key_id = self.generate_key_id()
        secret = self.generate_secret()
        
        # Store bcrypt hash of secret for authentication
        secret_hash = pwd_context.hash(secret)
        
        # Store SHA256 of secret for HMAC verification
        hmac_key_hash = self.derive_hmac_key(secret)
        
        # Create the key record
        api_key = AgentApiKey(
            id=key_id,
            user_id=user.id,
            name=name,
            secret_hash=secret_hash,
            description=description,
            is_active=True,
        )
        
        self._db.add(api_key)
        await self._db.commit()
        
        # Return full key (shown only once!)
        full_key = f"{self.KEY_PREFIX}{key_id}_{secret}"
        
        # Store the HMAC key hash in extra_data for later verification
        # We need to reload and update
        result = await self._db.execute(
            select(AgentApiKey).where(AgentApiKey.id == key_id)
        )
        api_key = result.scalar_one()
        api_key.extra_data = {"hmac_key_hash": hmac_key_hash}
        await self._db.commit()
        
        return full_key, key_id
    
    async def revoke_api_key(self, key_id: str, user_id: str) -> bool:
        """Revoke an API key.
        
        Args:
            key_id: The key ID to revoke
            user_id: The user requesting revocation (for verification)
            
        Returns:
            True if revoked, False if not found or not owner
        """
        result = await self._db.execute(
            select(AgentApiKey).where(
                AgentApiKey.id == key_id,
                AgentApiKey.user_id == user_id,
            )
        )
        api_key = result.scalar_one_or_none()
        
        if not api_key:
            return False
        
        api_key.is_active = False
        await self._db.commit()
        return True
    
    async def list_api_keys(self, user_id: str) -> list[AgentApiKey]:
        """List all API keys for a user.
        
        Args:
            user_id: The user whose keys to list
            
        Returns:
            List of API keys
        """
        result = await self._db.execute(
            select(AgentApiKey).where(AgentApiKey.user_id == user_id)
        )
        return list(result.scalars().all())
    
    async def get_hmac_key_hash(self, key_id: str) -> Optional[str]:
        """Get the HMAC key hash for a key ID.
        
        Args:
            key_id: The API key ID
            
        Returns:
            The HMAC key hash, or None if not found
        """
        result = await self._db.execute(
            select(AgentApiKey).where(AgentApiKey.id == key_id)
        )
        api_key = result.scalar_one_or_none()
        
        if not api_key:
            return None
        
        extra = getattr(api_key, 'extra_data', None) or {}
        return extra.get("hmac_key_hash")
    
    # ------------------------------------------------------------------------
    # HMAC Authentication
    # ------------------------------------------------------------------------
    
    def parse_key(self, full_key: str) -> tuple[str, str]:
        """Parse a full API key into components.
        
        Args:
            full_key: Full key in format sk_<key_id>_<secret>
            
        Returns:
            Tuple of (key_id, secret)
            
        Raises:
            ValueError: If key format is invalid
        """
        if not full_key.startswith(self.KEY_PREFIX):
            raise ValueError(f"Invalid key format: must start with {self.KEY_PREFIX}")
        
        parts = full_key[len(self.KEY_PREFIX):].split("_", 1)
        if len(parts) != 2:
            raise ValueError("Invalid key format: expected sk_<key_id>_<secret>")
        
        return parts[0], parts[1]
    
    async def authenticate_hmac(
        self,
        key_id: str,
        signature: str,
        timestamp: int,
        nonce: str,
    ) -> HmacAuthResult:
        """Authenticate using HMAC signature.
        
        This validates:
        1. The key exists and is active
        2. The timestamp is within the allowed window
        3. The nonce has not been used (replay prevention)
        4. The HMAC signature is valid
        
        Args:
            key_id: The API key ID
            signature: HMAC-SHA256 signature (hex encoded)
            timestamp: Unix timestamp of the request
            nonce: Random nonce for this request
            
        Returns:
            HmacAuthResult with success status and user if valid
        """
        # Check timestamp window
        current_time = int(time.time())
        if abs(current_time - timestamp) > HMAC_TIMESTAMP_WINDOW:
            return HmacAuthResult(
                success=False,
                message=f"Timestamp expired (window: ±{HMAC_TIMESTAMP_WINDOW}s)",
            )
        
        # Check nonce for replay
        if _nonce_cache.is_used(nonce):
            return HmacAuthResult(
                success=False,
                message="Nonce already used (replay attack detected)",
            )
        
        # Get API key from database
        result = await self._db.execute(
            select(AgentApiKey).where(AgentApiKey.id == key_id)
        )
        api_key = result.scalar_one_or_none()
        
        if not api_key:
            return HmacAuthResult(
                success=False,
                message="Invalid API key",
            )
        
        if not api_key.is_active:
            return HmacAuthResult(
                success=False,
                message="API key is revoked",
            )
        
        # Get the user
        user_result = await self._db.execute(
            select(User).where(User.id == api_key.user_id)
        )
        user = user_result.scalar_one_or_none()
        
        if not user:
            return HmacAuthResult(
                success=False,
                message="User not found",
            )
        
        if user.disabled:
            return HmacAuthResult(
                success=False,
                message="User account is disabled",
            )
        
        # Get HMAC key hash from extra_data
        extra = getattr(api_key, 'extra_data', None) or {}
        hmac_key_hash = extra.get("hmac_key_hash")
        
        if not hmac_key_hash:
            return HmacAuthResult(
                success=False,
                message="API key not properly initialized",
            )
        
        # Verify HMAC signature
        # Message is: timestamp:nonce
        message = f"{timestamp}:{nonce}"
        
        # Compute expected signature: HMAC-SHA256(hmac_key, message)
        expected_signature = hmac.new(
            hmac_key_hash.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        
        # Constant-time comparison to prevent timing attacks
        if not hmac.compare_digest(signature.lower(), expected_signature.lower()):
            return HmacAuthResult(
                success=False,
                message="Invalid signature",
            )
        
        # Mark nonce as used
        _nonce_cache.add(nonce)
        
        # Update last used info
        api_key.last_used_at = datetime.now(timezone.utc)
        await self._db.commit()
        
        return HmacAuthResult(
            success=True,
            message="Authentication successful",
            user=user,
            api_key=api_key,
        )
    
    @staticmethod
    def compute_hmac(hmac_key: str, timestamp: int, nonce: str) -> str:
        """Compute HMAC signature for a request.
        
        This should be called by the client to generate the signature.
        
        Args:
            hmac_key: The HMAC verification key (SHA256 of the secret)
            timestamp: Unix timestamp
            nonce: Random nonce
            
        Returns:
            Hex-encoded HMAC-SHA256 signature
        """
        message = f"{timestamp}:{nonce}"
        signature = hmac.new(
            hmac_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature


# ============================================================================
# Utility Functions
# ============================================================================

def format_api_key(key_id: str, secret: str) -> str:
    """Format a key ID and secret into full API key string.
    
    Args:
        key_id: The key ID
        secret: The secret
        
    Returns:
        Full API key string (sk_keyid_secret)
    """
    return f"sk_{key_id}_{secret}"


def is_valid_key_format(key: str) -> bool:
    """Check if a string looks like a valid API key format.
    
    Args:
        key: The string to check
        
    Returns:
        True if format is valid
    """
    if not key.startswith("sk_"):
        return False
    
    parts = key[len("sk_"):].split("_", 1)
    if len(parts) != 2:
        return False
    
    key_id, secret = parts
    # key_id should be 24 uppercase hex characters
    if len(key_id) != 24:
        return False
    try:
        int(key_id, 16)
    except ValueError:
        return False
    
    # secret should be 64 hex characters (256 bits)
    if len(secret) != 64:
        return False
    try:
        int(secret, 16)
    except ValueError:
        return False
    
    return True
