"""Tests for Layered Storage Module (storage/layered.py)."""

import pytest
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from sprinkle.storage.layered import (
    LayeredStorageService,
    StorageMigrationTask,
    MigrationResult,
    MessageRecord,
    ConversationRecord,
    MemberRecord,
    message_key,
    message_id_key,
    conversation_key,
    member_key,
    conversation_members_key,
    online_key,
    offline_queue_key,
    REDIS_KEY_MESSAGE,
    REDIS_KEY_CONVERSATION,
    REDIS_KEY_MEMBER,
    REDIS_KEY_ONLINE,
    REDIS_KEY_OFFLINE_QUEUE,
    TTL_MESSAGE_DAYS,
    TTL_ONLINE_MINUTES,
    TTL_OFFLINE_DAYS,
    TTL_CONVERSATION_HOURS,
)


# ============================================================================
# Test Key Helper Functions
# ============================================================================

class TestKeyHelpers:
    """Tests for Redis key helper functions."""

    def test_message_key(self):
        """Test message key generation."""
        key = message_key("conv_123", "2024-01-15")
        assert key == f"{REDIS_KEY_MESSAGE}:conv_123:2024-01-15"

    def test_message_id_key(self):
        """Test message ID key generation."""
        key = message_id_key("msg_456")
        assert key == f"{REDIS_KEY_MESSAGE}:id:msg_456"

    def test_conversation_key(self):
        """Test conversation key generation."""
        key = conversation_key("conv_123")
        assert key == f"{REDIS_KEY_CONVERSATION}:conv_123"

    def test_member_key(self):
        """Test member key generation."""
        key = member_key("conv_123", "user_456")
        assert key == f"{REDIS_KEY_MEMBER}:conv_123:user_456"

    def test_conversation_members_key(self):
        """Test conversation members key generation."""
        key = conversation_members_key("conv_123")
        assert key == f"{REDIS_KEY_MEMBER}:conv_123:members"

    def test_online_key(self):
        """Test online status key generation."""
        key = online_key("user_456")
        assert key == f"{REDIS_KEY_ONLINE}:user_456"

    def test_offline_queue_key(self):
        """Test offline queue key generation."""
        key = offline_queue_key("user_456")
        assert key == f"{REDIS_KEY_OFFLINE_QUEUE}:user_456"


# ============================================================================
# Test Record Classes
# ============================================================================

class TestMessageRecord:
    """Tests for MessageRecord dataclass."""

    def test_message_record_creation(self):
        """Test MessageRecord creation with defaults."""
        msg = MessageRecord(
            id="msg_123",
            conversation_id="conv_456",
            sender_id="user_789",
            content="Hello!",
        )
        assert msg.id == "msg_123"
        assert msg.conversation_id == "conv_456"
        assert msg.sender_id == "user_789"
        assert msg.content == "Hello!"
        assert msg.content_type == "text"
        assert msg.metadata == {}
        assert msg.mentions == []
        assert msg.reply_to is None
        assert msg.is_deleted is False
        assert msg.created_at is not None

    def test_message_record_to_dict(self):
        """Test MessageRecord serialization."""
        now = datetime.now(timezone.utc)
        msg = MessageRecord(
            id="msg_123",
            conversation_id="conv_456",
            sender_id="user_789",
            content="Hello!",
            content_type="text",
            metadata={"key": "value"},
            mentions=["user_001"],
            reply_to="msg_000",
            is_deleted=False,
            created_at=now,
        )
        data = msg.to_dict()
        assert data["id"] == "msg_123"
        assert data["conversation_id"] == "conv_456"
        assert data["sender_id"] == "user_789"
        assert data["content"] == "Hello!"
        assert data["content_type"] == "text"
        assert data["metadata"] == {"key": "value"}
        assert data["mentions"] == ["user_001"]
        assert data["reply_to"] == "msg_000"
        assert data["is_deleted"] is False
        assert data["created_at"] == now.isoformat()

    def test_message_record_from_dict(self):
        """Test MessageRecord deserialization."""
        now = datetime.now(timezone.utc)
        data = {
            "id": "msg_123",
            "conversation_id": "conv_456",
            "sender_id": "user_789",
            "content": "Hello!",
            "content_type": "text",
            "metadata": {"key": "value"},
            "mentions": ["user_001"],
            "reply_to": "msg_000",
            "is_deleted": False,
            "created_at": now.isoformat(),
            "edited_at": now.isoformat(),
            "deleted_at": None,
        }
        msg = MessageRecord.from_dict(data)
        assert msg.id == "msg_123"
        assert msg.conversation_id == "conv_456"
        assert msg.sender_id == "user_789"
        assert msg.content == "Hello!"
        assert msg.content_type == "text"
        assert msg.metadata == {"key": "value"}
        assert msg.mentions == ["user_001"]
        assert msg.reply_to == "msg_000"
        assert msg.is_deleted is False
        assert msg.created_at == now
        assert msg.edited_at == now
        assert msg.deleted_at is None

    def test_message_record_from_dict_minimal(self):
        """Test MessageRecord deserialization with minimal data."""
        data = {
            "id": "msg_123",
            "conversation_id": "conv_456",
            "sender_id": "user_789",
            "content": "Hello!",
        }
        msg = MessageRecord.from_dict(data)
        assert msg.id == "msg_123"
        assert msg.content_type == "text"
        assert msg.metadata == {}
        assert msg.mentions == []
        assert msg.is_deleted is False


class TestConversationRecord:
    """Tests for ConversationRecord dataclass."""

    def test_conversation_record_creation(self):
        """Test ConversationRecord creation with defaults."""
        conv = ConversationRecord(
            id="conv_123",
            type="group",
            name="Test Group",
            owner_id="user_456",
        )
        assert conv.id == "conv_123"
        assert conv.type == "group"
        assert conv.name == "Test Group"
        assert conv.owner_id == "user_456"
        assert conv.metadata == {}
        assert conv.created_at is not None
        assert conv.updated_at is not None

    def test_conversation_record_to_dict(self):
        """Test ConversationRecord serialization."""
        now = datetime.now(timezone.utc)
        conv = ConversationRecord(
            id="conv_123",
            type="group",
            name="Test Group",
            owner_id="user_456",
            metadata={"description": "A test group"},
            created_at=now,
            updated_at=now,
        )
        data = conv.to_dict()
        assert data["id"] == "conv_123"
        assert data["type"] == "group"
        assert data["name"] == "Test Group"
        assert data["owner_id"] == "user_456"
        assert data["metadata"] == {"description": "A test group"}
        assert data["created_at"] == now.isoformat()

    def test_conversation_record_from_dict(self):
        """Test ConversationRecord deserialization."""
        now = datetime.now(timezone.utc)
        data = {
            "id": "conv_123",
            "type": "group",
            "name": "Test Group",
            "owner_id": "user_456",
            "metadata": {"description": "A test group"},
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        conv = ConversationRecord.from_dict(data)
        assert conv.id == "conv_123"
        assert conv.type == "group"
        assert conv.name == "Test Group"
        assert conv.owner_id == "user_456"
        assert conv.metadata == {"description": "A test group"}


class TestMemberRecord:
    """Tests for MemberRecord dataclass."""

    def test_member_record_creation(self):
        """Test MemberRecord creation with defaults."""
        member = MemberRecord(
            conversation_id="conv_123",
            user_id="user_456",
            role="member",
        )
        assert member.conversation_id == "conv_123"
        assert member.user_id == "user_456"
        assert member.role == "member"
        assert member.nickname is None
        assert member.joined_at is not None
        assert member.left_at is None
        assert member.is_active is True

    def test_member_record_to_dict(self):
        """Test MemberRecord serialization."""
        now = datetime.now(timezone.utc)
        member = MemberRecord(
            conversation_id="conv_123",
            user_id="user_456",
            role="admin",
            nickname="TestAdmin",
            joined_at=now,
            is_active=True,
        )
        data = member.to_dict()
        assert data["conversation_id"] == "conv_123"
        assert data["user_id"] == "user_456"
        assert data["role"] == "admin"
        assert data["nickname"] == "TestAdmin"
        assert data["is_active"] is True

    def test_member_record_from_dict(self):
        """Test MemberRecord deserialization."""
        now = datetime.now(timezone.utc)
        data = {
            "conversation_id": "conv_123",
            "user_id": "user_456",
            "role": "admin",
            "nickname": "TestAdmin",
            "joined_at": now.isoformat(),
            "left_at": None,
            "is_active": True,
        }
        member = MemberRecord.from_dict(data)
        assert member.conversation_id == "conv_123"
        assert member.user_id == "user_456"
        assert member.role == "admin"
        assert member.nickname == "TestAdmin"
        assert member.is_active is True


# ============================================================================
# Test LayeredStorageService
# ============================================================================

class TestLayeredStorageService:
    """Tests for LayeredStorageService."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client."""
        redis_mock = MagicMock()
        redis_mock.get = AsyncMock(return_value=None)
        redis_mock.set = AsyncMock(return_value=True)
        redis_mock.zadd = AsyncMock(return_value=1)
        redis_mock.zrange = AsyncMock(return_value=[])
        redis_mock.zrevrange = AsyncMock(return_value=[])
        redis_mock.sadd = AsyncMock(return_value=1)
        redis_mock.srem = AsyncMock(return_value=1)
        redis_mock.smembers = AsyncMock(return_value=set())
        redis_mock.delete = AsyncMock(return_value=1)
        redis_mock.expire = AsyncMock(return_value=True)
        redis_mock.exists = AsyncMock(return_value=0)
        redis_mock.scan_iter = MagicMock(return_value=iter([]))
        redis_mock.ttl = AsyncMock(return_value=-1)
        redis_mock.lrange = AsyncMock(return_value=[])
        redis_mock.rpush = AsyncMock(return_value=1)
        redis_mock.hset = AsyncMock(return_value=1)
        redis_mock.hgetall = AsyncMock(return_value={})
        redis_mock.pipeline = MagicMock(return_value=MagicMock())
        return redis_mock

    @pytest.fixture
    def storage_service(self, mock_redis):
        """Create a LayeredStorageService with mock Redis."""
        return LayeredStorageService(redis_client=mock_redis, db_session=None)

    # ========================================================================
    # Message Operations
    # ========================================================================

    @pytest.mark.asyncio
    async def test_save_message(self, storage_service, mock_redis):
        """Test saving a message to both Redis and PostgreSQL."""
        now = datetime.now(timezone.utc)
        msg = MessageRecord(
            id="msg_123",
            conversation_id="conv_456",
            sender_id="user_789",
            content="Test message",
            created_at=now,
        )
        await storage_service.save_message(msg)
        assert mock_redis.set.called
        assert mock_redis.zadd.called
        assert mock_redis.expire.called

    @pytest.mark.asyncio
    async def test_get_message_from_redis(self, storage_service, mock_redis):
        """Test getting a message from Redis."""
        now = datetime.now(timezone.utc)
        message_data = {
            "id": "msg_123",
            "conversation_id": "conv_456",
            "sender_id": "user_789",
            "content": "Test message",
            "content_type": "text",
            "metadata": {},
            "mentions": [],
            "reply_to": None,
            "is_deleted": False,
            "created_at": now.isoformat(),
            "edited_at": None,
            "deleted_at": None,
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(message_data))
        msg = await storage_service.get_message("msg_123")
        assert msg is not None
        assert msg.id == "msg_123"
        assert msg.content == "Test message"

    @pytest.mark.asyncio
    async def test_get_message_not_found(self, storage_service, mock_redis):
        """Test getting a non-existent message returns None."""
        mock_redis.get = AsyncMock(return_value=None)
        msg = await storage_service.get_message("nonexistent")
        assert msg is None

    @pytest.mark.asyncio
    async def test_get_conversation_messages(self, storage_service, mock_redis):
        """Test getting conversation messages."""
        now = datetime.now(timezone.utc)
        message_data = {
            "id": "msg_123",
            "conversation_id": "conv_456",
            "sender_id": "user_789",
            "content": "Test message",
            "content_type": "text",
            "metadata": {},
            "mentions": [],
            "reply_to": None,
            "is_deleted": False,
            "created_at": now.isoformat(),
            "edited_at": None,
            "deleted_at": None,
        }
        # zrevrange returns list of bytes - only return message for today's key
        # The function scans 8 days, so we need to handle each day's key
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        async def mock_zrevrange(key, start, end):
            if today in key:
                return [b"msg_123"]
            return []
        
        mock_redis.zrevrange = AsyncMock(side_effect=mock_zrevrange)
        mock_redis.get = AsyncMock(return_value=json.dumps(message_data))
        msgs = await storage_service.get_conversation_messages("conv_456")
        assert len(msgs) == 1
        assert msgs[0].id == "msg_123"

    @pytest.mark.asyncio
    async def test_get_conversation_messages_filters_deleted(self, storage_service, mock_redis):
        """Test that soft-deleted messages are filtered out."""
        now = datetime.now(timezone.utc)
        message_data = {
            "id": "msg_deleted",
            "conversation_id": "conv_456",
            "sender_id": "user_789",
            "content": "Deleted message",
            "content_type": "text",
            "metadata": {},
            "mentions": [],
            "reply_to": None,
            "is_deleted": True,  # Marked as deleted
            "created_at": now.isoformat(),
            "edited_at": None,
            "deleted_at": None,
        }
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        async def mock_zrevrange(key, start, end):
            if today in key:
                return [b"msg_deleted"]
            return []
        
        mock_redis.zrevrange = AsyncMock(side_effect=mock_zrevrange)
        mock_redis.get = AsyncMock(return_value=json.dumps(message_data))
        msgs = await storage_service.get_conversation_messages("conv_456")
        assert len(msgs) == 0  # Deleted message should be filtered

    @pytest.mark.asyncio
    async def test_soft_delete_message(self, storage_service, mock_redis):
        """Test soft deleting a message."""
        now = datetime.now(timezone.utc)
        message_data = {
            "id": "msg_123",
            "conversation_id": "conv_456",
            "sender_id": "user_789",
            "content": "Test message",
            "content_type": "text",
            "metadata": {},
            "mentions": [],
            "reply_to": None,
            "is_deleted": False,
            "created_at": now.isoformat(),
            "edited_at": None,
            "deleted_at": None,
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(message_data))
        await storage_service.soft_delete_message("msg_123")
        assert mock_redis.set.called
        # Verify the update call contains is_deleted=True
        call_args = mock_redis.set.call_args
        updated_data = json.loads(call_args[0][1])
        assert updated_data["is_deleted"] is True
        assert updated_data["deleted_at"] is not None

    @pytest.mark.asyncio
    async def test_soft_delete_nonexistent_message(self, storage_service, mock_redis):
        """Test soft deleting a non-existent message does nothing."""
        mock_redis.get = AsyncMock(return_value=None)
        await storage_service.soft_delete_message("nonexistent")
        assert not mock_redis.set.called

    # ========================================================================
    # Conversation Operations
    # ========================================================================

    @pytest.mark.asyncio
    async def test_save_conversation(self, storage_service, mock_redis):
        """Test saving a conversation."""
        now = datetime.now(timezone.utc)
        conv = ConversationRecord(
            id="conv_123",
            type="group",
            name="Test Group",
            owner_id="user_456",
            created_at=now,
            updated_at=now,
        )
        await storage_service.save_conversation(conv)
        assert mock_redis.set.called

    @pytest.mark.asyncio
    async def test_get_conversation_from_redis(self, storage_service, mock_redis):
        """Test getting a conversation from Redis."""
        now = datetime.now(timezone.utc)
        conv_data = {
            "id": "conv_123",
            "type": "group",
            "name": "Test Group",
            "owner_id": "user_456",
            "metadata": {},
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(conv_data))
        conv = await storage_service.get_conversation("conv_123")
        assert conv is not None
        assert conv.id == "conv_123"
        assert conv.name == "Test Group"

    @pytest.mark.asyncio
    async def test_get_conversation_not_found(self, storage_service, mock_redis):
        """Test getting a non-existent conversation returns None."""
        mock_redis.get = AsyncMock(return_value=None)
        conv = await storage_service.get_conversation("nonexistent")
        assert conv is None

    @pytest.mark.asyncio
    async def test_get_user_conversations(self, storage_service, mock_redis):
        """Test getting all conversations for a user."""
        now = datetime.now(timezone.utc)
        conv_data = {
            "id": "conv_123",
            "type": "group",
            "name": "Test Group",
            "owner_id": "user_456",
            "metadata": {},
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        # Mock with db_session to trigger PostgreSQL path
        storage_service._db = MagicMock()
        storage_service._get_user_conversations_from_postgres = AsyncMock(return_value=[
            ConversationRecord.from_dict(conv_data)
        ])
        convs = await storage_service.get_user_conversations("user_456")
        assert len(convs) == 1
        assert convs[0].id == "conv_123"

    # ========================================================================
    # Member Operations
    # ========================================================================

    @pytest.mark.asyncio
    async def test_add_member(self, storage_service, mock_redis):
        """Test adding a member to a conversation."""
        now = datetime.now(timezone.utc)
        member = MemberRecord(
            conversation_id="conv_123",
            user_id="user_456",
            role="member",
            joined_at=now,
            is_active=True,
        )
        await storage_service.add_member(member)
        assert mock_redis.set.called
        assert mock_redis.sadd.called

    @pytest.mark.asyncio
    async def test_get_member_from_redis(self, storage_service, mock_redis):
        """Test getting a member from Redis."""
        now = datetime.now(timezone.utc)
        member_data = {
            "conversation_id": "conv_123",
            "user_id": "user_456",
            "role": "admin",
            "nickname": "TestAdmin",
            "joined_at": now.isoformat(),
            "left_at": None,
            "is_active": True,
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(member_data))
        member = await storage_service.get_member("conv_123", "user_456")
        assert member is not None
        assert member.user_id == "user_456"
        assert member.role == "admin"

    @pytest.mark.asyncio
    async def test_get_member_not_found(self, storage_service, mock_redis):
        """Test getting a non-existent member returns None."""
        mock_redis.get = AsyncMock(return_value=None)
        member = await storage_service.get_member("conv_123", "nonexistent")
        assert member is None

    @pytest.mark.asyncio
    async def test_remove_member(self, storage_service, mock_redis):
        """Test removing a member from a conversation."""
        await storage_service.remove_member("conv_123", "user_456")
        assert mock_redis.delete.called
        assert mock_redis.srem.called

    @pytest.mark.asyncio
    async def test_get_conversation_members(self, storage_service, mock_redis):
        """Test getting all members of a conversation."""
        now = datetime.now(timezone.utc)
        member_data = {
            "conversation_id": "conv_123",
            "user_id": "user_456",
            "role": "member",
            "nickname": None,
            "joined_at": now.isoformat(),
            "left_at": None,
            "is_active": True,
        }
        mock_redis.smembers = AsyncMock(return_value={b"user_456"})
        mock_redis.get = AsyncMock(return_value=json.dumps(member_data))
        members = await storage_service.get_conversation_members("conv_123")
        assert len(members) == 1
        assert members[0].user_id == "user_456"

    @pytest.mark.asyncio
    async def test_get_conversation_members_filters_inactive(self, storage_service, mock_redis):
        """Test that inactive members are filtered out."""
        now = datetime.now(timezone.utc)
        member_data = {
            "conversation_id": "conv_123",
            "user_id": "user_456",
            "role": "member",
            "nickname": None,
            "joined_at": now.isoformat(),
            "left_at": None,
            "is_active": False,  # Inactive member
        }
        mock_redis.smembers = AsyncMock(return_value={b"user_456"})
        mock_redis.get = AsyncMock(return_value=json.dumps(member_data))
        members = await storage_service.get_conversation_members("conv_123")
        assert len(members) == 0

    @pytest.mark.asyncio
    async def test_get_conversation_members_falls_back_to_postgres(self, storage_service, mock_redis):
        """Test that PostgreSQL is fallback when Redis has no members."""
        now = datetime.now(timezone.utc)
        member_data = {
            "conversation_id": "conv_123",
            "user_id": "user_456",
            "role": "member",
            "nickname": None,
            "joined_at": now.isoformat(),
            "left_at": None,
            "is_active": True,
        }
        mock_redis.smembers = AsyncMock(return_value=set())  # Empty in Redis
        storage_service._db = MagicMock()
        storage_service._get_conversation_members_from_postgres = AsyncMock(return_value=[
            MemberRecord.from_dict(member_data)
        ])
        members = await storage_service.get_conversation_members("conv_123")
        assert len(members) == 1

    @pytest.mark.asyncio
    async def test_update_member_role(self, storage_service, mock_redis):
        """Test updating a member's role."""
        now = datetime.now(timezone.utc)
        member_data = {
            "conversation_id": "conv_123",
            "user_id": "user_456",
            "role": "member",
            "nickname": None,
            "joined_at": now.isoformat(),
            "left_at": None,
            "is_active": True,
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(member_data))
        member = await storage_service.update_member_role("conv_123", "user_456", "admin")
        assert member.role == "admin"
        assert mock_redis.set.called

    @pytest.mark.asyncio
    async def test_update_member_role_not_found(self, storage_service, mock_redis):
        """Test updating role of non-existent member raises ValueError."""
        mock_redis.get = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="not found"):
            await storage_service.update_member_role("conv_123", "nonexistent", "admin")

    # ========================================================================
    # Online Status Operations
    # ========================================================================

    @pytest.mark.asyncio
    async def test_set_user_online(self, storage_service, mock_redis):
        """Test setting user as online."""
        await storage_service.set_user_online("user_456")
        assert mock_redis.set.called
        call_args = mock_redis.set.call_args
        assert call_args[0][0] == online_key("user_456")

    @pytest.mark.asyncio
    async def test_set_user_offline(self, storage_service, mock_redis):
        """Test setting user as offline."""
        await storage_service.set_user_offline("user_456")
        assert mock_redis.delete.called
        call_args = mock_redis.delete.call_args
        assert online_key("user_456") in call_args[0][0]

    @pytest.mark.asyncio
    async def test_is_user_online_true(self, storage_service, mock_redis):
        """Test checking if user is online (True case)."""
        mock_redis.exists = AsyncMock(return_value=1)
        result = await storage_service.is_user_online("user_456")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_user_online_false(self, storage_service, mock_redis):
        """Test checking if user is online (False case)."""
        mock_redis.exists = AsyncMock(return_value=0)
        result = await storage_service.is_user_online("user_456")
        assert result is False

    @pytest.mark.asyncio
    async def test_refresh_online_status(self, storage_service, mock_redis):
        """Test refreshing user's online status."""
        await storage_service.refresh_online_status("user_456")
        assert mock_redis.set.called

    # ========================================================================
    # Offline Message Queue Operations
    # ========================================================================

    @pytest.mark.asyncio
    async def test_queue_offline_message(self, storage_service, mock_redis):
        """Test queueing a message for offline user."""
        await storage_service.queue_offline_message("user_456", "msg_123")
        assert mock_redis.rpush.called
        assert mock_redis.expire.called

    @pytest.mark.asyncio
    async def test_get_offline_messages(self, storage_service, mock_redis):
        """Test getting offline message queue."""
        mock_redis.lrange = AsyncMock(return_value=[b"msg_123", b"msg_456"])
        messages = await storage_service.get_offline_messages("user_456")
        assert messages == ["msg_123", "msg_456"]

    @pytest.mark.asyncio
    async def test_get_offline_messages_empty(self, storage_service, mock_redis):
        """Test getting empty offline message queue."""
        mock_redis.lrange = AsyncMock(return_value=[])
        messages = await storage_service.get_offline_messages("user_456")
        assert messages == []

    @pytest.mark.asyncio
    async def test_clear_offline_messages(self, storage_service, mock_redis):
        """Test clearing offline message queue."""
        await storage_service.clear_offline_messages("user_456")
        assert mock_redis.delete.called


# ============================================================================
# Test StorageMigrationTask
# ============================================================================

class TestStorageMigrationTask:
    """Tests for StorageMigrationTask."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client."""
        redis_mock = MagicMock()
        redis_mock.get = AsyncMock(return_value=None)
        redis_mock.set = AsyncMock(return_value=True)
        redis_mock.zadd = AsyncMock(return_value=1)
        redis_mock.zrange = AsyncMock(return_value=[])
        redis_mock.zrevrange = AsyncMock(return_value=[])
        redis_mock.sadd = AsyncMock(return_value=1)
        redis_mock.srem = AsyncMock(return_value=1)
        redis_mock.smembers = AsyncMock(return_value=set())
        redis_mock.delete = AsyncMock(return_value=1)
        redis_mock.expire = AsyncMock(return_value=True)
        redis_mock.exists = AsyncMock(return_value=0)
        redis_mock.ttl = AsyncMock(return_value=-1)
        redis_mock.lrange = AsyncMock(return_value=[])
        redis_mock.rpush = AsyncMock(return_value=1)
        redis_mock.pipeline = MagicMock(return_value=MagicMock())
        return redis_mock

    @pytest.fixture
    def storage_service(self, mock_redis):
        """Create a LayeredStorageService with mock Redis."""
        return LayeredStorageService(redis_client=mock_redis, db_session=None)

    @pytest.fixture
    def migration_task(self, storage_service):
        """Create a StorageMigrationTask."""
        return StorageMigrationTask(storage_service)

    @pytest.mark.asyncio
    async def test_migration_runs_successfully(self, migration_task, mock_redis):
        """Test that migration runs without errors when no old messages exist."""
        # scan_iter returns an empty async iterator
        async def empty_scan(match):
            return
            yield  # make it a generator

        mock_redis.scan_iter = MagicMock(return_value=empty_scan("msg:*"))
        result = await migration_task.run()
        assert result.migrated_count == 0
        assert result.deleted_count == 0
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_migration_catches_exceptions(self, migration_task, mock_redis):
        """Test that migration catches and records exceptions."""
        async def failing_scan(match):
            raise Exception("Redis error")
            yield  # make it a generator

        mock_redis.scan_iter = MagicMock(return_value=failing_scan("msg:*"))
        result = await migration_task.run()
        assert "Redis error" in result.errors

    @pytest.mark.asyncio
    async def test_cleanup_offline_queues(self, migration_task, mock_redis):
        """Test cleanup of expired offline queues.
        
        This test verifies that _cleanup_offline_queues sets TTL on queues without expiry.
        """
        import asyncio
        
        # Track which patterns were scanned
        scan_results = {}
        
        async def make_async_iter(items):
            """Create an async generator from a list."""
            for item in items:
                yield item
        
        # First call: scan for messages (empty result)
        # Second call: scan for offline queues
        call_count = [0]
        
        async def mock_scan_iter(match):
            call_count[0] += 1
            if "msg:" in match:
                # Message scan - return empty
                async for _ in make_async_iter([]):
                    yield _
            elif "offline:" in match:
                # Offline queue scan - return one key with no TTL
                async for k in make_async_iter([b"offline:user_123"]):
                    yield k
        
        mock_redis.scan_iter = mock_scan_iter
        mock_redis.ttl = AsyncMock(return_value=-1)  # No expiry set

        result = await migration_task.run()

        # Verify expire was called to set TTL on offline queue
        assert mock_redis.expire.called
        # Verify scan_iter was called at least once
        assert call_count[0] >= 1


# ============================================================================
# Test Constants
# ============================================================================

class TestConstants:
    """Tests for module constants."""

    def test_ttl_values(self):
        """Test TTL constants are defined correctly."""
        assert TTL_MESSAGE_DAYS == 8
        assert TTL_ONLINE_MINUTES == 5
        assert TTL_OFFLINE_DAYS == 30
        assert TTL_CONVERSATION_HOURS == 1

    def test_redis_key_prefixes(self):
        """Test Redis key prefixes are defined correctly."""
        assert REDIS_KEY_MESSAGE == "msg"
        assert REDIS_KEY_CONVERSATION == "conv"
        assert REDIS_KEY_MEMBER == "member"
        assert REDIS_KEY_ONLINE == "online"
        assert REDIS_KEY_OFFLINE_QUEUE == "offline"
