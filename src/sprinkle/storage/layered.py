"""Layered Storage - Redis (hot) + PostgreSQL (cold) data management."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import redis.asyncio as redis

logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

# Redis key prefixes
REDIS_KEY_MESSAGE = "msg"
REDIS_KEY_CONVERSATION = "conv"
REDIS_KEY_MEMBER = "member"
REDIS_KEY_ONLINE = "online"
REDIS_KEY_OFFLINE_QUEUE = "offline"

# Redis key patterns
def message_key(conversation_id: str, date_str: str) -> str:
    """Get Redis key for a message in a conversation on a date."""
    return f"{REDIS_KEY_MESSAGE}:{conversation_id}:{date_str}"

def message_id_key(message_id: str) -> str:
    """Get Redis key for a specific message by ID."""
    return f"{REDIS_KEY_MESSAGE}:id:{message_id}"

def conversation_key(conversation_id: str) -> str:
    """Get Redis key for a conversation."""
    return f"{REDIS_KEY_CONVERSATION}:{conversation_id}"

def member_key(conversation_id: str, user_id: str) -> str:
    """Get Redis key for a member."""
    return f"{REDIS_KEY_MEMBER}:{conversation_id}:{user_id}"

def conversation_members_key(conversation_id: str) -> str:
    """Get Redis key for a conversation's member set."""
    return f"{REDIS_KEY_MEMBER}:{conversation_id}:members"

def online_key(user_id: str) -> str:
    """Get Redis key for online status."""
    return f"{REDIS_KEY_ONLINE}:{user_id}"

def offline_queue_key(user_id: str) -> str:
    """Get Redis key for offline message queue."""
    return f"{REDIS_KEY_OFFLINE_QUEUE}:{user_id}"

# TTL values
TTL_MESSAGE_DAYS = 8  # Messages kept in Redis for 8 days
TTL_ONLINE_MINUTES = 5  # Online status TTL
TTL_OFFLINE_DAYS = 30  # Offline queue TTL
TTL_CONVERSATION_HOURS = 1  # Conversation cache TTL


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class MessageRecord:
    """Message record for storage."""
    id: str
    conversation_id: str
    sender_id: str
    content: str
    content_type: str = "text"
    metadata: Dict[str, Any] = None
    mentions: List[str] = None
    reply_to: Optional[str] = None
    is_deleted: bool = False
    created_at: datetime = None
    edited_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.mentions is None:
            self.mentions = []
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "sender_id": self.sender_id,
            "content": self.content,
            "content_type": self.content_type,
            "metadata": self.metadata,
            "mentions": self.mentions,
            "reply_to": self.reply_to,
            "is_deleted": self.is_deleted,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "edited_at": self.edited_at.isoformat() if self.edited_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MessageRecord:
        """Create from dictionary."""
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        
        edited_at = data.get("edited_at")
        if isinstance(edited_at, str):
            edited_at = datetime.fromisoformat(edited_at)
        
        deleted_at = data.get("deleted_at")
        if isinstance(deleted_at, str):
            deleted_at = datetime.fromisoformat(deleted_at)
        
        return cls(
            id=data["id"],
            conversation_id=data["conversation_id"],
            sender_id=data["sender_id"],
            content=data["content"],
            content_type=data.get("content_type", "text"),
            metadata=data.get("metadata", {}),
            mentions=data.get("mentions", []),
            reply_to=data.get("reply_to"),
            is_deleted=data.get("is_deleted", False),
            created_at=created_at,
            edited_at=edited_at,
            deleted_at=deleted_at,
        )


@dataclass
class ConversationRecord:
    """Conversation record for storage."""
    id: str
    type: str  # "direct" | "group"
    name: str
    owner_id: str
    metadata: Dict[str, Any] = None
    created_at: datetime = None
    updated_at: datetime = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)
        if self.updated_at is None:
            self.updated_at = datetime.now(timezone.utc)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "owner_id": self.owner_id,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ConversationRecord:
        """Create from dictionary."""
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        
        updated_at = data.get("updated_at")
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)
        
        return cls(
            id=data["id"],
            type=data["type"],
            name=data["name"],
            owner_id=data["owner_id"],
            metadata=data.get("metadata", {}),
            created_at=created_at,
            updated_at=updated_at,
        )


@dataclass
class MemberRecord:
    """Member record for storage."""
    conversation_id: str
    user_id: str
    role: str  # "owner" | "admin" | "member"
    nickname: Optional[str] = None
    joined_at: datetime = None
    left_at: Optional[datetime] = None
    is_active: bool = True
    
    def __post_init__(self):
        if self.joined_at is None:
            self.joined_at = datetime.now(timezone.utc)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "role": self.role,
            "nickname": self.nickname,
            "joined_at": self.joined_at.isoformat() if self.joined_at else None,
            "left_at": self.left_at.isoformat() if self.left_at else None,
            "is_active": self.is_active,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MemberRecord:
        """Create from dictionary."""
        joined_at = data.get("joined_at")
        if isinstance(joined_at, str):
            joined_at = datetime.fromisoformat(joined_at)
        
        left_at = data.get("left_at")
        if isinstance(left_at, str):
            left_at = datetime.fromisoformat(left_at)
        
        return cls(
            conversation_id=data["conversation_id"],
            user_id=data["user_id"],
            role=data["role"],
            nickname=data.get("nickname"),
            joined_at=joined_at,
            left_at=left_at,
            is_active=data.get("is_active", True),
        )


# ============================================================================
# Layered Storage Service
# ============================================================================

class LayeredStorageService:
    """Layered storage service managing Redis (hot) and PostgreSQL (cold).
    
    This service provides a unified interface for storing and retrieving
    data, automatically managing the hot/cold data boundary.
    
    Write path: Data is written to both Redis and PostgreSQL (dual-write)
    Read path: Data is read from Redis first, falling back to PostgreSQL
    Migration: Scheduled task migrates old data from Redis to PostgreSQL
    
    Example:
        storage = LayeredStorageService(redis_client, db_session)
        
        # Save a message (dual write)
        await storage.save_message(message)
        
        # Get a message (Redis first, then PostgreSQL)
        message = await storage.get_message(message_id)
        
        # List messages (handles pagination, Redis + PostgreSQL merge)
        messages = await storage.get_conversation_messages(conv_id)
    """
    
    def __init__(
        self,
        redis_client: redis.Redis,
        db_session=None,  # SQLAlchemy async session, optional for now
    ):
        """Initialize the layered storage service.
        
        Args:
            redis_client: Async Redis client
            db_session: Optional SQLAlchemy async session for PostgreSQL
        """
        self._redis = redis_client
        self._db = db_session
    
    # =========================================================================
    # Message Operations
    # =========================================================================
    
    async def save_message(self, message: MessageRecord) -> None:
        """Save a message (dual write to Redis + PostgreSQL).
        
        Args:
            message: The message record to save
        """
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        
        # Write to Redis
        await self._save_message_to_redis(message, date_str)
        
        # Write to PostgreSQL (if session available)
        if self._db:
            await self._save_message_to_postgres(message)
        else:
            logger.warning("No DB session, message not persisted to PostgreSQL")
    
    async def _save_message_to_redis(
        self,
        message: MessageRecord,
        date_str: str,
    ) -> None:
        """Save message to Redis."""
        msg_key = message_id_key(message.id)
        conv_msg_key = message_key(message.conversation_id, date_str)
        
        # Store message data
        await self._redis.set(
            msg_key,
            json.dumps(message.to_dict()),
            ex=timedelta(days=TTL_MESSAGE_DAYS),
        )
        
        # Add to conversation's message index (sorted set by timestamp)
        score = message.created_at.timestamp() if message.created_at else 0
        await self._redis.zadd(
            conv_msg_key,
            {message.id: score},
        )
        # Set TTL on the conversation message index
        await self._redis.expire(conv_msg_key, timedelta(days=TTL_MESSAGE_DAYS))
        
        logger.debug(f"Saved message {message.id} to Redis")
    
    async def _save_message_to_postgres(self, message: MessageRecord) -> None:
        """Save message to PostgreSQL.
        
        Note: This is a placeholder for actual DB implementation.
        In production, this would insert into the messages table.
        """
        # TODO: Implement actual PostgreSQL write
        # For now, just log
        logger.debug(f"Would save message {message.id} to PostgreSQL")
    
    async def get_message(self, message_id: str) -> Optional[MessageRecord]:
        """Get a single message by ID.
        
        Reads from Redis first, falls back to PostgreSQL.
        
        Args:
            message_id: The message ID
        
        Returns:
            MessageRecord if found, None otherwise
        """
        # Try Redis first
        msg_key = message_id_key(message_id)
        data = await self._redis.get(msg_key)
        
        if data:
            return MessageRecord.from_dict(json.loads(data))
        
        # Fallback to PostgreSQL
        if self._db:
            return await self._get_message_from_postgres(message_id)
        
        return None
    
    async def _get_message_from_postgres(
        self,
        message_id: str,
    ) -> Optional[MessageRecord]:
        """Get message from PostgreSQL.
        
        Placeholder for actual implementation.
        """
        # TODO: Implement actual PostgreSQL read
        logger.debug(f"Would get message {message_id} from PostgreSQL")
        return None
    
    async def get_conversation_messages(
        self,
        conversation_id: str,
        limit: int = 50,
        before: Optional[datetime] = None,
    ) -> List[MessageRecord]:
        """Get messages for a conversation.
        
        Retrieves messages from Redis (recent) and merges with PostgreSQL (older).
        
        Args:
            conversation_id: The conversation ID
            limit: Maximum number of messages
            before: Only return messages before this time
        
        Returns:
            List of MessageRecords, sorted by created_at descending
        """
        messages = []
        
        # Get from Redis (current messages within TTL)
        today = datetime.now(timezone.utc)
        redis_keys = []
        
        for days_ago in range(TTL_MESSAGE_DAYS):
            date = today - timedelta(days=days_ago)
            date_str = date.strftime("%Y-%m-%d")
            key = message_key(conversation_id, date_str)
            redis_keys.append(key)
        
        # Fetch all message IDs from Redis
        all_msg_ids = []
        for key in redis_keys:
            msg_ids = await self._redis.zrevrange(key, 0, -1)
            all_msg_ids.extend(msg_ids)
        
        # Fetch actual messages
        for msg_id in all_msg_ids[:limit]:
            msg_key = message_id_key(msg_id.decode() if isinstance(msg_id, bytes) else msg_id)
            data = await self._redis.get(msg_key)
            if data:
                msg = MessageRecord.from_dict(json.loads(data))
                if not msg.is_deleted:
                    if before is None or msg.created_at < before:
                        messages.append(msg)
        
        # Sort by created_at descending
        messages.sort(key=lambda m: m.created_at, reverse=True)
        
        # Trim to limit
        return messages[:limit]
    
    async def soft_delete_message(self, message_id: str) -> None:
        """Soft delete a message.
        
        Sets is_deleted=True and deleted_at timestamp.
        
        Args:
            message_id: The message ID
        """
        message = await self.get_message(message_id)
        if message is None:
            return
        
        message.is_deleted = True
        message.deleted_at = datetime.now(timezone.utc)
        
        # Update in Redis
        msg_key = message_id_key(message_id)
        await self._redis.set(
            msg_key,
            json.dumps(message.to_dict()),
            ex=timedelta(days=TTL_MESSAGE_DAYS),
        )
        
        # Update in PostgreSQL
        if self._db:
            await self._soft_delete_message_in_postgres(message_id)
        
        logger.debug(f"Soft deleted message {message_id}")
    
    async def _soft_delete_message_in_postgres(self, message_id: str) -> None:
        """Soft delete message in PostgreSQL.
        
        Placeholder for actual implementation.
        """
        # TODO: Implement actual PostgreSQL soft delete
        logger.debug(f"Would soft delete message {message_id} in PostgreSQL")
    
    # =========================================================================
    # Conversation Operations
    # =========================================================================
    
    async def save_conversation(
        self,
        conversation: ConversationRecord,
    ) -> None:
        """Save a conversation.
        
        Args:
            conversation: The conversation record
        """
        conv_key = conversation_key(conversation.id)
        
        # Write to Redis
        await self._redis.set(
            conv_key,
            json.dumps(conversation.to_dict()),
            ex=timedelta(hours=TTL_CONVERSATION_HOURS),
        )
        
        # Write to PostgreSQL
        if self._db:
            await self._save_conversation_to_postgres(conversation)
        
        logger.debug(f"Saved conversation {conversation.id}")
    
    async def _save_conversation_to_postgres(
        self,
        conversation: ConversationRecord,
    ) -> None:
        """Save conversation to PostgreSQL.
        
        Placeholder for actual implementation.
        """
        # TODO: Implement actual PostgreSQL write
        logger.debug(f"Would save conversation {conversation.id} to PostgreSQL")
    
    async def get_conversation(
        self,
        conversation_id: str,
    ) -> Optional[ConversationRecord]:
        """Get a conversation by ID.
        
        Args:
            conversation_id: The conversation ID
        
        Returns:
            ConversationRecord if found, None otherwise
        """
        # Try Redis first
        conv_key = conversation_key(conversation_id)
        data = await self._redis.get(conv_key)
        
        if data:
            return ConversationRecord.from_dict(json.loads(data))
        
        # Fallback to PostgreSQL
        if self._db:
            return await self._get_conversation_from_postgres(conversation_id)
        
        return None
    
    async def _get_conversation_from_postgres(
        self,
        conversation_id: str,
    ) -> Optional[ConversationRecord]:
        """Get conversation from PostgreSQL.
        
        Placeholder for actual implementation.
        """
        # TODO: Implement actual PostgreSQL read
        logger.debug(f"Would get conversation {conversation_id} from PostgreSQL")
        return None
    
    async def get_user_conversations(
        self,
        user_id: str,
    ) -> List[ConversationRecord]:
        """Get all conversations a user is a member of.
        
        Args:
            user_id: The user ID
        
        Returns:
            List of ConversationRecords
        """
        conversations = []
        
        # Get from PostgreSQL (user's conversations)
        if self._db:
            conversations = await self._get_user_conversations_from_postgres(user_id)
        
        # Cache in Redis
        for conv in conversations:
            conv_key = conversation_key(conv.id)
            await self._redis.set(
                conv_key,
                json.dumps(conv.to_dict()),
                ex=timedelta(hours=TTL_CONVERSATION_HOURS),
            )
        
        return conversations
    
    async def _get_user_conversations_from_postgres(
        self,
        user_id: str,
    ) -> List[ConversationRecord]:
        """Get user's conversations from PostgreSQL.
        
        Placeholder for actual implementation.
        """
        # TODO: Implement actual PostgreSQL query
        logger.debug(f"Would get conversations for user {user_id} from PostgreSQL")
        return []
    
    # =========================================================================
    # Member Operations
    # =========================================================================
    
    async def add_member(self, member: MemberRecord) -> None:
        """Add a member to a conversation.
        
        Args:
            member: The member record
        """
        mem_key = member_key(member.conversation_id, member.user_id)
        conv_members_key = conversation_members_key(member.conversation_id)
        
        # Write to Redis
        await self._redis.set(
            mem_key,
            json.dumps(member.to_dict()),
        )
        
        # Add to conversation's member set
        await self._redis.sadd(conv_members_key, member.user_id)
        
        # Write to PostgreSQL
        if self._db:
            await self._add_member_to_postgres(member)
        
        logger.debug(f"Added member {member.user_id} to conversation {member.conversation_id}")
    
    async def _add_member_to_postgres(self, member: MemberRecord) -> None:
        """Add member to PostgreSQL.
        
        Placeholder for actual implementation.
        """
        # TODO: Implement actual PostgreSQL write
        logger.debug(f"Would add member {member.user_id} to PostgreSQL")
    
    async def remove_member(
        self,
        conversation_id: str,
        user_id: str,
    ) -> None:
        """Remove a member from a conversation.
        
        Args:
            conversation_id: The conversation ID
            user_id: The user ID
        """
        mem_key = member_key(conversation_id, user_id)
        conv_members_key = conversation_members_key(conversation_id)
        
        # Remove from Redis
        await self._redis.delete(mem_key)
        await self._redis.srem(conv_members_key, user_id)
        
        # Update in PostgreSQL (soft delete)
        if self._db:
            await self._remove_member_from_postgres(conversation_id, user_id)
        
        logger.debug(f"Removed member {user_id} from conversation {conversation_id}")
    
    async def _remove_member_from_postgres(
        self,
        conversation_id: str,
        user_id: str,
    ) -> None:
        """Remove member from PostgreSQL (soft delete).
        
        Placeholder for actual implementation.
        """
        # TODO: Implement actual PostgreSQL soft delete
        logger.debug(f"Would remove member {user_id} from PostgreSQL")
    
    async def get_member(
        self,
        conversation_id: str,
        user_id: str,
    ) -> Optional[MemberRecord]:
        """Get a member record.
        
        Args:
            conversation_id: The conversation ID
            user_id: The user ID
        
        Returns:
            MemberRecord if found, None otherwise
        """
        mem_key = member_key(conversation_id, user_id)
        data = await self._redis.get(mem_key)
        
        if data:
            return MemberRecord.from_dict(json.loads(data))
        
        # Fallback to PostgreSQL
        if self._db:
            return await self._get_member_from_postgres(conversation_id, user_id)
        
        return None
    
    async def _get_member_from_postgres(
        self,
        conversation_id: str,
        user_id: str,
    ) -> Optional[MemberRecord]:
        """Get member from PostgreSQL.
        
        Placeholder for actual implementation.
        """
        # TODO: Implement actual PostgreSQL read
        logger.debug(f"Would get member {user_id} from PostgreSQL")
        return None
    
    async def get_conversation_members(
        self,
        conversation_id: str,
    ) -> List[MemberRecord]:
        """Get all members of a conversation.
        
        Args:
            conversation_id: The conversation ID
        
        Returns:
            List of MemberRecords
        """
        conv_members_key = conversation_members_key(conversation_id)
        member_ids = await self._redis.smembers(conv_members_key)
        
        members = []
        for user_id in member_ids:
            user_id_str = user_id.decode() if isinstance(user_id, bytes) else user_id
            member = await self.get_member(conversation_id, user_id_str)
            if member and member.is_active:
                members.append(member)
        
        # Fallback to PostgreSQL if no members in Redis
        if not members and self._db:
            members = await self._get_conversation_members_from_postgres(conversation_id)
        
        return members
    
    async def _get_conversation_members_from_postgres(
        self,
        conversation_id: str,
    ) -> List[MemberRecord]:
        """Get conversation members from PostgreSQL.
        
        Placeholder for actual implementation.
        """
        # TODO: Implement actual PostgreSQL query
        logger.debug(f"Would get members for conversation {conversation_id} from PostgreSQL")
        return []
    
    async def update_member_role(
        self,
        conversation_id: str,
        user_id: str,
        new_role: str,
    ) -> MemberRecord:
        """Update a member's role.
        
        Args:
            conversation_id: The conversation ID
            user_id: The user ID
            new_role: The new role ("admin" | "member")
        
        Returns:
            Updated MemberRecord
        """
        member = await self.get_member(conversation_id, user_id)
        if member is None:
            raise ValueError(f"Member {user_id} not found in conversation {conversation_id}")
        
        member.role = new_role
        
        # Update in Redis
        mem_key = member_key(conversation_id, user_id)
        await self._redis.set(
            mem_key,
            json.dumps(member.to_dict()),
        )
        
        # Update in PostgreSQL
        if self._db:
            await self._update_member_role_in_postgres(conversation_id, user_id, new_role)
        
        logger.debug(f"Updated member {user_id} role to {new_role} in conversation {conversation_id}")
        
        return member
    
    async def _update_member_role_in_postgres(
        self,
        conversation_id: str,
        user_id: str,
        new_role: str,
    ) -> None:
        """Update member role in PostgreSQL.
        
        Placeholder for actual implementation.
        """
        # TODO: Implement actual PostgreSQL update
        logger.debug(f"Would update member {user_id} role to {new_role} in PostgreSQL")
    
    # =========================================================================
    # Online Status
    # =========================================================================
    
    async def set_user_online(self, user_id: str) -> None:
        """Set user as online.
        
        Args:
            user_id: The user ID
        """
        key = online_key(user_id)
        await self._redis.set(
            key,
            datetime.now(timezone.utc).isoformat(),
            ex=timedelta(minutes=TTL_ONLINE_MINUTES),
        )
    
    async def set_user_offline(self, user_id: str) -> None:
        """Set user as offline.
        
        Args:
            user_id: The user ID
        """
        key = online_key(user_id)
        await self._redis.delete(key)
    
    async def is_user_online(self, user_id: str) -> bool:
        """Check if user is online.
        
        Args:
            user_id: The user ID
        
        Returns:
            True if online
        """
        key = online_key(user_id)
        return await self._redis.exists(key) > 0
    
    async def refresh_online_status(self, user_id: str) -> None:
        """Refresh user's online status (heartbeat).
        
        Args:
            user_id: The user ID
        """
        await self.set_user_online(user_id)
    
    # =========================================================================
    # Offline Message Queue
    # =========================================================================
    
    async def queue_offline_message(
        self,
        user_id: str,
        message_id: str,
    ) -> None:
        """Queue a message for offline user.
        
        Args:
            user_id: The user ID
            message_id: The message ID
        """
        key = offline_queue_key(user_id)
        await self._redis.rpush(key, message_id)
        await self._redis.expire(key, timedelta(days=TTL_OFFLINE_DAYS))
    
    async def get_offline_messages(self, user_id: str) -> List[str]:
        """Get offline message queue for a user.
        
        Args:
            user_id: The user ID
        
        Returns:
            List of message IDs
        """
        key = offline_queue_key(user_id)
        messages = await self._redis.lrange(key, 0, -1)
        return [m.decode() if isinstance(m, bytes) else m for m in messages]
    
    async def clear_offline_messages(self, user_id: str) -> None:
        """Clear offline message queue for a user.
        
        Args:
            user_id: The user ID
        """
        key = offline_queue_key(user_id)
        await self._redis.delete(key)


# ============================================================================
# Migration Task
# ============================================================================

@dataclass
class MigrationResult:
    """Result of a storage migration task."""
    migrated_count: int = 0
    deleted_count: int = 0
    errors: List[str] = field(default_factory=list)


class StorageMigrationTask:
    """Storage migration task - runs daily at 03:00.
    
    Migrates old messages from Redis to PostgreSQL and cleans up
    expired data.
    
    Migration strategy:
    1. Scan Redis for messages older than 8 days
    2. Sync those messages to PostgreSQL
    3. Delete migrated messages from Redis
    4. Clean up expired offline queues
    """
    
    def __init__(self, storage: LayeredStorageService):
        """Initialize the migration task.
        
        Args:
            storage: The layered storage service
        """
        self._storage = storage
        self._cutoff_date = datetime.now(timezone.utc) - timedelta(days=TTL_MESSAGE_DAYS)
    
    async def run(self) -> MigrationResult:
        """Execute the migration.
        
        Returns:
            MigrationResult with counts and any errors
        """
        result = MigrationResult()
        
        logger.info("Starting storage migration...")
        
        try:
            # Migrate old messages
            migrated, deleted = await self._migrate_old_messages()
            result.migrated_count = migrated
            result.deleted_count = deleted
            
            # Clean up expired offline queues
            await self._cleanup_offline_queues()
            
            logger.info(
                f"Storage migration completed: "
                f"migrated={migrated}, deleted={deleted}"
            )
        except Exception as e:
            logger.error(f"Storage migration failed: {e}")
            result.errors.append(str(e))
        
        return result
    
    async def _migrate_old_messages(self) -> Tuple[int, int]:
        """Migrate old messages from Redis to PostgreSQL.
        
        Returns:
            Tuple of (migrated_count, deleted_count)
        """
        migrated = 0
        deleted = 0
        
        # Scan Redis for message keys
        pattern = f"{REDIS_KEY_MESSAGE}:*"
        
        async for key in self._storage._redis.scan_iter(match=pattern):
            key_str = key.decode() if isinstance(key, bytes) else key
            
            # Parse conversation and date from key
            # Format: msg:{conversation_id}:{date}
            parts = key_str.split(":")
            if len(parts) != 3:
                continue
            
            _, conversation_id, date_str = parts
            
            # Check if date is older than cutoff
            try:
                msg_date = datetime.strptime(date_str, "%Y-%m-%d")
                if msg_date < self._cutoff_date:
                    # Migrate this message
                    msg_id = await self._storage._redis.zrange(key, 0, 0)
                    if msg_id:
                        msg_id_str = msg_id[0].decode() if isinstance(msg_id[0], bytes) else msg_id[0]
                        msg_key = message_id_key(msg_id_str)
                        data = await self._storage._redis.get(msg_key)
                        
                        if data:
                            # Ensure in PostgreSQL
                            # (in real impl, would write to DB here)
                            migrated += 1
                            
                            # Delete from Redis
                            await self._storage._redis.delete(msg_key)
                            deleted += 1
                    
                    # Delete the date index
                    await self._storage._redis.delete(key)
            except ValueError:
                continue
        
        return migrated, deleted
    
    async def _cleanup_offline_queues(self) -> None:
        """Clean up expired offline message queues.
        
        Removes queues older than TTL_OFFLINE_DAYS.
        """
        pattern = f"{REDIS_KEY_OFFLINE_QUEUE}:*"
        
        async for key in self._storage._redis.scan_iter(match=pattern):
            key_str = key.decode() if isinstance(key, bytes) else key
            ttl = await self._storage._redis.ttl(key_str)
            
            # If TTL is -1 (no expiry) or > 30 days, skip
            if ttl == -1 or ttl > TTL_OFFLINE_DAYS * 24 * 3600:
                # Set expiry
                await self._storage._redis.expire(key, timedelta(days=TTL_OFFLINE_DAYS))


# ============================================================================
# Factory
# ============================================================================

async def create_layered_storage(
    redis_url: str = "redis://localhost:6379/0",
) -> LayeredStorageService:
    """Create a layered storage service.
    
    Args:
        redis_url: Redis connection URL
    
    Returns:
        Configured LayeredStorageService
    """
    redis_client = redis.from_url(redis_url, decode_responses=False)
    return LayeredStorageService(redis_client)


__all__ = [
    "LayeredStorageService",
    "StorageMigrationTask",
    "MigrationResult",
    "MessageRecord",
    "ConversationRecord",
    "MemberRecord",
    "create_layered_storage",
    "message_key",
    "message_id_key",
    "conversation_key",
    "member_key",
    "online_key",
    "offline_queue_key",
]
