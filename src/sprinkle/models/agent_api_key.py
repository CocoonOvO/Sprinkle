"""Agent API Key model for Sprinkle - persistent authentication."""

from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime
from . import Base


class AgentApiKey(Base):
    """Agent API Key for persistent authentication.
    
    API Keys are used by agent users to establish long-lived WebSocket connections
    without token expiration concerns. Each key consists of:
    - id: Public identifier (used for lookup)
    - secret_hash: bcrypt hash of the actual secret (never stored plaintext)
    - extra_data: JSONB field for additional data like HMAC key hash
    
    The full key format is: sk_<id>_<secret>
    Only the id and secret_hash are stored in the database.
    """
    
    __tablename__ = "agent_api_keys"
    
    # Public identifier for looking up the key
    id = Column(String(36), primary_key=True)
    
    # Owner user (the agent)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    
    # Human-readable name for the key (e.g., "司康", "布莱妮")
    name = Column(String(100), nullable=False)
    
    # bcrypt hash of the actual secret
    # The plaintext secret is only shown ONCE when the key is created
    secret_hash = Column(String(255), nullable=False)
    
    # Optional description
    description = Column(String(255), nullable=True)
    
    # Additional data (hmac_key_hash, etc.)
    extra_data = Column(JSONB, default={}, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    
    # Last connection info
    last_used_ip = Column(String(45), nullable=True)
    
    # Status
    is_active = Column(Boolean, default=True, nullable=False)
    
    def __repr__(self) -> str:
        return f"<AgentApiKey(id={self.id}, name={self.name}, active={self.is_active})>"
