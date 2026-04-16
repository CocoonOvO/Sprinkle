"""Tests for Phase 6: Business Logic (Permission, Layered Storage, Services)."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from uuid import uuid4
import json

# ============================================================================
# Permission Service Tests
# ============================================================================

class TestPermissionService:
    """Tests for PermissionService."""
    
    def setup_method(self):
        """Set up test fixtures."""
        from sprinkle.kernel.permission import (
            PermissionService,
            Role,
            Action,
            MemberInfo,
            get_permissions_for_role,
        )
        self.permission_service = PermissionService()
        self.Role = Role
        self.Action = Action
        self.MemberInfo = MemberInfo
        self.get_permissions_for_role = get_permissions_for_role
    
    def teardown_method(self):
        """Clean up after tests."""
        self.permission_service.clear_cache()
    
    # ========================================================================
    # Permission Matrix Tests
    # ========================================================================
    
    def test_owner_has_all_permissions(self):
        """Test that owner role has all permissions."""
        permissions = self.get_permissions_for_role(self.Role.OWNER)
        
        assert self.Action.SEND_MESSAGE in permissions
        assert self.Action.EDIT_OWN_MESSAGE in permissions
        assert self.Action.DELETE_OWN_MESSAGE in permissions
        assert self.Action.DELETE_ANY_MESSAGE in permissions
        assert self.Action.VIEW_CONVERSATION in permissions
        assert self.Action.EDIT_CONVERSATION in permissions
        assert self.Action.ADD_MEMBER in permissions
        assert self.Action.REMOVE_MEMBER in permissions
        assert self.Action.SET_ADMIN in permissions
        assert self.Action.DELETE_CONVERSATION in permissions
        assert self.Action.TRANSFER_OWNERSHIP in permissions
    
    def test_admin_permissions(self):
        """Test that admin role has most permissions except special ones."""
        permissions = self.get_permissions_for_role(self.Role.ADMIN)
        
        # Admin has these
        assert self.Action.SEND_MESSAGE in permissions
        assert self.Action.EDIT_OWN_MESSAGE in permissions
        assert self.Action.DELETE_OWN_MESSAGE in permissions
        assert self.Action.DELETE_ANY_MESSAGE in permissions
        assert self.Action.VIEW_CONVERSATION in permissions
        assert self.Action.EDIT_CONVERSATION in permissions
        assert self.Action.ADD_MEMBER in permissions
        assert self.Action.REMOVE_MEMBER in permissions
        
        # Admin does NOT have these
        assert self.Action.SET_ADMIN not in permissions
        assert self.Action.DELETE_CONVERSATION not in permissions
        assert self.Action.TRANSFER_OWNERSHIP not in permissions
    
    def test_human_member_permissions(self):
        """Test that human member has basic permissions."""
        permissions = self.get_permissions_for_role(self.Role.MEMBER, is_agent=False)
        
        assert self.Action.SEND_MESSAGE in permissions
        assert self.Action.EDIT_OWN_MESSAGE in permissions
        assert self.Action.DELETE_OWN_MESSAGE in permissions
        assert self.Action.VIEW_CONVERSATION in permissions
        
        assert self.Action.DELETE_ANY_MESSAGE not in permissions
        assert self.Action.EDIT_CONVERSATION not in permissions
        assert self.Action.ADD_MEMBER not in permissions
        assert self.Action.REMOVE_MEMBER not in permissions
    
    def test_agent_member_permissions(self):
        """Test that agent member has limited permissions (send only)."""
        permissions = self.get_permissions_for_role(self.Role.MEMBER, is_agent=True)
        
        # Agent can only send messages and view
        assert self.Action.SEND_MESSAGE in permissions
        assert self.Action.VIEW_CONVERSATION in permissions
        
        # Agent CANNOT edit or delete own messages
        assert self.Action.EDIT_OWN_MESSAGE not in permissions
        assert self.Action.DELETE_OWN_MESSAGE not in permissions
        
        # Agent cannot do admin things
        assert self.Action.DELETE_ANY_MESSAGE not in permissions
        assert self.Action.EDIT_CONVERSATION not in permissions
        assert self.Action.ADD_MEMBER not in permissions
        assert self.Action.REMOVE_MEMBER not in permissions
    
    # ========================================================================
    # Member Info Cache Tests
    # ========================================================================
    
    def test_set_and_get_member_info(self):
        """Test setting and getting member info from cache."""
        conv_id = "conv_123"
        user_id = "user_456"
        
        member_info = self.MemberInfo(
            conversation_id=conv_id,
            user_id=user_id,
            role=self.Role.OWNER,
            is_agent=False,
            is_active=True,
        )
        
        self.permission_service.set_member_info(member_info)
        
        retrieved = self.permission_service.get_member_info(conv_id, user_id)
        
        assert retrieved is not None
        assert retrieved.conversation_id == conv_id
        assert retrieved.user_id == user_id
        assert retrieved.role == self.Role.OWNER
        assert retrieved.is_agent == False
        assert retrieved.is_active == True
    
    def test_remove_member(self):
        """Test removing member from cache."""
        conv_id = "conv_123"
        user_id = "user_456"
        
        member_info = self.MemberInfo(
            conversation_id=conv_id,
            user_id=user_id,
            role=self.Role.MEMBER,
            is_agent=False,
            is_active=True,
        )
        
        self.permission_service.set_member_info(member_info)
        self.permission_service.remove_member(conv_id, user_id)
        
        retrieved = self.permission_service.get_member_info(conv_id, user_id)
        assert retrieved is None
    
    def test_set_and_check_agent_status(self):
        """Test setting and checking agent status."""
        user_id = "agent_123"
        
        self.permission_service.set_user_is_agent(user_id, True)
        
        assert self.permission_service.is_user_agent(user_id) == True
        
        self.permission_service.set_user_is_agent(user_id, False)
        assert self.permission_service.is_user_agent(user_id) == False
    
    # ========================================================================
    # Permission Check Tests
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_owner_can_delete_any_message(self):
        """Test that owner can delete any message."""
        conv_id = "conv_123"
        owner_id = "owner_456"
        
        member_info = self.MemberInfo(
            conversation_id=conv_id,
            user_id=owner_id,
            role=self.Role.OWNER,
            is_agent=False,
            is_active=True,
        )
        self.permission_service.set_member_info(member_info)
        
        result = await self.permission_service.check_permission(
            user_id=owner_id,
            conversation_id=conv_id,
            action=self.Action.DELETE_ANY_MESSAGE,
        )
        
        assert result.allowed == True
        assert result.role == self.Role.OWNER
    
    @pytest.mark.asyncio
    async def test_admin_can_delete_any_message(self):
        """Test that admin can delete any message."""
        conv_id = "conv_123"
        admin_id = "admin_456"
        
        member_info = self.MemberInfo(
            conversation_id=conv_id,
            user_id=admin_id,
            role=self.Role.ADMIN,
            is_agent=False,
            is_active=True,
        )
        self.permission_service.set_member_info(member_info)
        
        result = await self.permission_service.check_permission(
            user_id=admin_id,
            conversation_id=conv_id,
            action=self.Action.DELETE_ANY_MESSAGE,
        )
        
        assert result.allowed == True
    
    @pytest.mark.asyncio
    async def test_member_cannot_delete_others_message(self):
        """Test that regular member cannot delete others' messages."""
        conv_id = "conv_123"
        member_id = "member_456"
        
        member_info = self.MemberInfo(
            conversation_id=conv_id,
            user_id=member_id,
            role=self.Role.MEMBER,
            is_agent=False,
            is_active=True,
        )
        self.permission_service.set_member_info(member_info)
        
        result = await self.permission_service.check_permission(
            user_id=member_id,
            conversation_id=conv_id,
            action=self.Action.DELETE_ANY_MESSAGE,
        )
        
        assert result.allowed == False
    
    @pytest.mark.asyncio
    async def test_agent_cannot_edit_own_message(self):
        """Test that agent member cannot edit their own messages."""
        conv_id = "conv_123"
        agent_id = "agent_456"
        
        member_info = self.MemberInfo(
            conversation_id=conv_id,
            user_id=agent_id,
            role=self.Role.MEMBER,
            is_agent=True,  # Agent
            is_active=True,
        )
        self.permission_service.set_member_info(member_info)
        
        result = await self.permission_service.check_permission(
            user_id=agent_id,
            conversation_id=conv_id,
            action=self.Action.EDIT_OWN_MESSAGE,
        )
        
        assert result.allowed == False
    
    @pytest.mark.asyncio
    async def test_human_member_can_edit_own_message(self):
        """Test that human member can edit their own messages."""
        conv_id = "conv_123"
        member_id = "member_456"
        
        member_info = self.MemberInfo(
            conversation_id=conv_id,
            user_id=member_id,
            role=self.Role.MEMBER,
            is_agent=False,  # Human
            is_active=True,
        )
        self.permission_service.set_member_info(member_info)
        
        result = await self.permission_service.check_permission(
            user_id=member_id,
            conversation_id=conv_id,
            action=self.Action.EDIT_OWN_MESSAGE,
        )
        
        assert result.allowed == True
    
    @pytest.mark.asyncio
    async def test_non_member_access_denied(self):
        """Test that non-member is denied access."""
        conv_id = "conv_123"
        non_member_id = "non_member_456"
        
        # Don't add member to cache
        result = await self.permission_service.check_permission(
            user_id=non_member_id,
            conversation_id=conv_id,
            action=self.Action.VIEW_CONVERSATION,
        )
        
        assert result.allowed == False
        assert "not a member" in result.reason.lower()
    
    @pytest.mark.asyncio
    async def test_get_user_role(self):
        """Test getting user's role."""
        conv_id = "conv_123"
        user_id = "user_456"
        
        member_info = self.MemberInfo(
            conversation_id=conv_id,
            user_id=user_id,
            role=self.Role.ADMIN,
            is_agent=False,
            is_active=True,
        )
        self.permission_service.set_member_info(member_info)
        
        role = await self.permission_service.get_user_role(user_id, conv_id)
        
        assert role == self.Role.ADMIN
    
    @pytest.mark.asyncio
    async def test_get_user_role_not_member(self):
        """Test getting role for non-member returns None."""
        conv_id = "conv_123"
        user_id = "non_member_456"
        
        role = await self.permission_service.get_user_role(user_id, conv_id)
        
        assert role is None
    
    @pytest.mark.asyncio
    async def test_is_agent_admin(self):
        """Test checking if agent is admin."""
        conv_id = "conv_123"
        agent_admin_id = "agent_admin_456"
        
        member_info = self.MemberInfo(
            conversation_id=conv_id,
            user_id=agent_admin_id,
            role=self.Role.ADMIN,
            is_agent=True,  # Is an agent
            is_active=True,
        )
        self.permission_service.set_member_info(member_info)
        
        is_admin = await self.permission_service.is_agent_admin(agent_admin_id, conv_id)
        
        assert is_admin == True
    
    @pytest.mark.asyncio
    async def test_is_agent_admin_regular_agent(self):
        """Test that regular agent (not admin) returns False."""
        conv_id = "conv_123"
        agent_id = "agent_456"
        
        member_info = self.MemberInfo(
            conversation_id=conv_id,
            user_id=agent_id,
            role=self.Role.MEMBER,  # Not admin
            is_agent=True,  # Is an agent
            is_active=True,
        )
        self.permission_service.set_member_info(member_info)
        
        is_admin = await self.permission_service.is_agent_admin(agent_id, conv_id)
        
        assert is_admin == False
    
    # ========================================================================
    # Bulk Permission Tests
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_filter_users_by_permission(self):
        """Test filtering users by permission."""
        conv_id = "conv_123"
        user1 = "owner_1"
        user2 = "admin_2"
        user3 = "member_3"
        
        self.permission_service.set_member_info(self.MemberInfo(
            conversation_id=conv_id,
            user_id=user1,
            role=self.Role.OWNER,
            is_agent=False,
            is_active=True,
        ))
        self.permission_service.set_member_info(self.MemberInfo(
            conversation_id=conv_id,
            user_id=user2,
            role=self.Role.ADMIN,
            is_agent=False,
            is_active=True,
        ))
        self.permission_service.set_member_info(self.MemberInfo(
            conversation_id=conv_id,
            user_id=user3,
            role=self.Role.MEMBER,
            is_agent=False,
            is_active=True,
        ))
        
        users = [user1, user2, user3]
        
        # Filter by DELETE_ANY_MESSAGE (only owner/admin)
        allowed = await self.permission_service.filter_users_by_permission(
            users, conv_id, self.Action.DELETE_ANY_MESSAGE
        )
        
        assert user1 in allowed  # owner
        assert user2 in allowed  # admin
        assert user3 not in allowed  # member


# ============================================================================
# Layered Storage Tests
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
        redis_mock.srem = AsyncMock(return_value=1)  # Added srem
        redis_mock.smembers = AsyncMock(return_value=set())
        redis_mock.delete = AsyncMock(return_value=1)
        redis_mock.expire = AsyncMock(return_value=True)
        redis_mock.exists = AsyncMock(return_value=0)
        redis_mock.scan_iter = AsyncMock(return_value=iter([]))
        redis_mock.ttl = AsyncMock(return_value=-1)
        redis_mock.lrange = AsyncMock(return_value=[])
        redis_mock.rpush = AsyncMock(return_value=1)
        return redis_mock
    
    @pytest.fixture
    def storage_service(self, mock_redis):
        """Create a LayeredStorageService with mock Redis."""
        from sprinkle.storage.layered import LayeredStorageService
        return LayeredStorageService(redis_client=mock_redis, db_session=None)
    
    # ========================================================================
    # Message Record Tests
    # ========================================================================
    
    def test_message_record_to_dict(self):
        """Test MessageRecord serialization."""
        from sprinkle.storage.layered import MessageRecord
        
        now = datetime.now(timezone.utc)
        message = MessageRecord(
            id="msg_123",
            conversation_id="conv_456",
            sender_id="user_789",
            content="Hello!",
            content_type="text",
            created_at=now,
        )
        
        data = message.to_dict()
        
        assert data["id"] == "msg_123"
        assert data["conversation_id"] == "conv_456"
        assert data["sender_id"] == "user_789"
        assert data["content"] == "Hello!"
        assert data["content_type"] == "text"
        assert data["is_deleted"] == False
        assert data["created_at"] == now.isoformat()
    
    def test_message_record_from_dict(self):
        """Test MessageRecord deserialization."""
        from sprinkle.storage.layered import MessageRecord
        
        now = datetime.now(timezone.utc)
        data = {
            "id": "msg_123",
            "conversation_id": "conv_456",
            "sender_id": "user_789",
            "content": "Hello!",
            "content_type": "text",
            "metadata": {},
            "mentions": [],
            "reply_to": None,
            "is_deleted": False,
            "created_at": now.isoformat(),
            "edited_at": None,
            "deleted_at": None,
        }
        
        message = MessageRecord.from_dict(data)
        
        assert message.id == "msg_123"
        assert message.conversation_id == "conv_456"
        assert message.content == "Hello!"
    
    # ========================================================================
    # Conversation Record Tests
    # ========================================================================
    
    def test_conversation_record_to_dict(self):
        """Test ConversationRecord serialization."""
        from sprinkle.storage.layered import ConversationRecord
        
        now = datetime.now(timezone.utc)
        conv = ConversationRecord(
            id="conv_123",
            type="group",
            name="Test Group",
            owner_id="user_456",
            created_at=now,
            updated_at=now,
        )
        
        data = conv.to_dict()
        
        assert data["id"] == "conv_123"
        assert data["type"] == "group"
        assert data["name"] == "Test Group"
        assert data["owner_id"] == "user_456"
    
    def test_conversation_record_from_dict(self):
        """Test ConversationRecord deserialization."""
        from sprinkle.storage.layered import ConversationRecord
        
        now = datetime.now(timezone.utc)
        data = {
            "id": "conv_123",
            "type": "group",
            "name": "Test Group",
            "owner_id": "user_456",
            "metadata": {},
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        
        conv = ConversationRecord.from_dict(data)
        
        assert conv.id == "conv_123"
        assert conv.type == "group"
        assert conv.name == "Test Group"
    
    # ========================================================================
    # Member Record Tests
    # ========================================================================
    
    def test_member_record_to_dict(self):
        """Test MemberRecord serialization."""
        from sprinkle.storage.layered import MemberRecord
        
        now = datetime.now(timezone.utc)
        member = MemberRecord(
            conversation_id="conv_123",
            user_id="user_456",
            role="admin",
            joined_at=now,
            is_active=True,
        )
        
        data = member.to_dict()
        
        assert data["conversation_id"] == "conv_123"
        assert data["user_id"] == "user_456"
        assert data["role"] == "admin"
        assert data["is_active"] == True
    
    def test_member_record_from_dict(self):
        """Test MemberRecord deserialization."""
        from sprinkle.storage.layered import MemberRecord
        
        now = datetime.now(timezone.utc)
        data = {
            "conversation_id": "conv_123",
            "user_id": "user_456",
            "role": "admin",
            "nickname": None,
            "joined_at": now.isoformat(),
            "left_at": None,
            "is_active": True,
        }
        
        member = MemberRecord.from_dict(data)
        
        assert member.conversation_id == "conv_123"
        assert member.user_id == "user_456"
        assert member.role == "admin"
        assert member.is_active == True
    
    # ========================================================================
    # Storage Operations Tests
    # ========================================================================
    
    @pytest.mark.asyncio
    async def test_save_message(self, storage_service, mock_redis):
        """Test saving a message."""
        from sprinkle.storage.layered import MessageRecord
        
        now = datetime.now(timezone.utc)
        message = MessageRecord(
            id="msg_123",
            conversation_id="conv_456",
            sender_id="user_789",
            content="Test message",
            created_at=now,
        )
        
        await storage_service.save_message(message)
        
        # Verify Redis set was called
        assert mock_redis.set.called
        assert mock_redis.zadd.called
    
    @pytest.mark.asyncio
    async def test_get_message_from_redis(self, storage_service, mock_redis):
        """Test getting a message from Redis."""
        from sprinkle.storage.layered import MessageRecord
        
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
        
        message = await storage_service.get_message("msg_123")
        
        assert message is not None
        assert message.id == "msg_123"
        assert message.content == "Test message"
    
    @pytest.mark.asyncio
    async def test_get_message_not_found(self, storage_service, mock_redis):
        """Test getting a non-existent message."""
        mock_redis.get = AsyncMock(return_value=None)
        
        message = await storage_service.get_message("nonexistent")
        
        assert message is None
    
    @pytest.mark.asyncio
    async def test_save_conversation(self, storage_service, mock_redis):
        """Test saving a conversation."""
        from sprinkle.storage.layered import ConversationRecord
        
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
    async def test_add_member(self, storage_service, mock_redis):
        """Test adding a member."""
        from sprinkle.storage.layered import MemberRecord
        
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
    async def test_remove_member(self, storage_service, mock_redis):
        """Test removing a member."""
        await storage_service.remove_member("conv_123", "user_456")
        
        assert mock_redis.delete.called
        assert mock_redis.srem.called
    
    @pytest.mark.asyncio
    async def test_soft_delete_message(self, storage_service, mock_redis):
        """Test soft deleting a message."""
        from sprinkle.storage.layered import MessageRecord
        
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
        
        # Verify message was updated with is_deleted=True
        assert mock_redis.set.called


# ============================================================================
# Conversation Service Tests
# ============================================================================

class TestConversationService:
    """Tests for ConversationService."""
    
    @pytest.fixture
    def mock_storage(self):
        """Create a mock storage service."""
        storage = MagicMock()
        storage.save_conversation = AsyncMock()
        storage.get_conversation = AsyncMock(return_value=None)
        storage.get_user_conversations = AsyncMock(return_value=[])
        storage.add_member = AsyncMock()
        storage.remove_member = AsyncMock()
        storage.get_member = AsyncMock(return_value=None)
        storage.get_conversation_members = AsyncMock(return_value=[])
        storage.update_member_role = AsyncMock()
        return storage
    
    @pytest.fixture
    def mock_permission(self):
        """Create a mock permission service."""
        from sprinkle.kernel.permission import Role, Action, PermissionCheckResult, MemberInfo
        
        permission = MagicMock()
        
        # Default permission check - denied
        async def check_permission(user_id, conversation_id, action):
            return PermissionCheckResult(allowed=False, reason="denied")
        
        permission.check_permission = AsyncMock(side_effect=check_permission)
        
        # Default get user role - None
        permission.get_user_role = AsyncMock(return_value=None)
        
        # Default is user agent - False
        permission.is_user_agent = MagicMock(return_value=False)
        
        # Member info management
        permission.set_member_info = MagicMock()
        permission.remove_member = MagicMock()
        
        permission.Role = Role
        permission.MemberInfo = MemberInfo
        
        return permission
    
    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.emit_async = AsyncMock()
        return bus
    
    @pytest.fixture
    def conversation_service(self, mock_storage, mock_permission, mock_event_bus):
        """Create a ConversationService with mocks."""
        from sprinkle.services.conversation_service import ConversationService
        return ConversationService(
            storage=mock_storage,
            permission_service=mock_permission,
            event_bus=mock_event_bus,
        )
    
    @pytest.mark.asyncio
    async def test_create_group_conversation(self, conversation_service, mock_storage, mock_permission):
        """Test creating a group conversation."""
        from sprinkle.storage.layered import ConversationRecord
        
        creator_id = "user_123"
        member_ids = ["user_456", "user_789"]
        
        conv = await conversation_service.create_conversation(
            creator_id=creator_id,
            type="group",
            name="Test Group",
            member_ids=member_ids,
        )
        
        assert conv is not None
        assert conv.type == "group"
        assert conv.name == "Test Group"
        assert conv.owner_id == creator_id
        
        # Verify storage was called
        assert mock_storage.save_conversation.called
        assert mock_storage.add_member.call_count >= 1  # At least creator
        
        # Verify permission cache was updated
        assert mock_permission.set_member_info.called
    
    @pytest.mark.asyncio
    async def test_create_direct_conversation(self, conversation_service, mock_storage, mock_permission):
        """Test creating a direct conversation."""
        conv = await conversation_service.create_conversation(
            creator_id="user_123",
            type="direct",
            member_ids=["user_456"],
        )
        
        assert conv is not None
        assert conv.type == "direct"
    
    @pytest.mark.asyncio
    async def test_create_group_without_name_fails(self, conversation_service):
        """Test that creating a group without name fails."""
        from sprinkle.services.conversation_service import InvalidOperationError
        
        with pytest.raises(InvalidOperationError):
            await conversation_service.create_conversation(
                creator_id="user_123",
                type="group",
                name=None,  # Missing name
            )
    
    @pytest.mark.asyncio
    async def test_invite_member_permission_denied(self, conversation_service, mock_permission):
        """Test that inviting member without permission fails."""
        from sprinkle.kernel.permission import Action, PermissionCheckResult
        
        # Set up permission to deny
        async def check_permission(user_id, conversation_id, action):
            if action == Action.ADD_MEMBER:
                return PermissionCheckResult(allowed=False, reason="Not authorized")
            return PermissionCheckResult(allowed=True)
        
        mock_permission.check_permission = AsyncMock(side_effect=check_permission)
        
        from sprinkle.services.conversation_service import PermissionDeniedError
        
        with pytest.raises(PermissionDeniedError):
            await conversation_service.invite_member(
                conversation_id="conv_123",
                inviter_id="user_456",
                user_id="user_789",
            )
    
    @pytest.mark.asyncio
    async def test_invite_member_success(self, conversation_service, mock_storage, mock_permission):
        """Test successfully inviting a member."""
        from sprinkle.kernel.permission import Action, PermissionCheckResult, MemberInfo, Role
        from sprinkle.storage.layered import MemberRecord
        
        # Set up permission to allow
        async def check_permission(user_id, conversation_id, action):
            if action == Action.ADD_MEMBER:
                return PermissionCheckResult(allowed=True, role=Role.OWNER)
            return PermissionCheckResult(allowed=True)
        
        mock_permission.check_permission = AsyncMock(side_effect=check_permission)
        
        # Mock existing conversation
        mock_storage.get_conversation = AsyncMock(return_value=MagicMock(
            id="conv_123",
            updated_at=datetime.now(timezone.utc),
        ))
        
        # Mock member not existing
        mock_storage.get_member = AsyncMock(return_value=None)
        
        member = await conversation_service.invite_member(
            conversation_id="conv_123",
            inviter_id="owner_456",
            user_id="user_789",
        )
        
        assert member is not None
        assert member.user_id == "user_789"
        assert member.role == "member"
        
        # Verify member was added to storage
        mock_storage.add_member.assert_called()
    
    @pytest.mark.asyncio
    async def test_invite_existing_member_fails(self, conversation_service, mock_storage, mock_permission):
        """Test that inviting an existing member fails."""
        from sprinkle.kernel.permission import Action, PermissionCheckResult, Role
        from sprinkle.storage.layered import MemberRecord
        from sprinkle.services.conversation_service import InvalidOperationError
        
        # Set up permission to allow
        async def check_permission(user_id, conversation_id, action):
            return PermissionCheckResult(allowed=True, role=Role.OWNER)
        
        mock_permission.check_permission = AsyncMock(side_effect=check_permission)
        
        # Mock existing active member
        mock_storage.get_member = AsyncMock(return_value=MemberRecord(
            conversation_id="conv_123",
            user_id="user_789",
            role="member",
            is_active=True,
        ))
        
        with pytest.raises(InvalidOperationError):
            await conversation_service.invite_member(
                conversation_id="conv_123",
                inviter_id="owner_456",
                user_id="user_789",
            )
    
    @pytest.mark.asyncio
    async def test_remove_member_success(self, conversation_service, mock_storage, mock_permission):
        """Test successfully removing a member."""
        from sprinkle.kernel.permission import Action, PermissionCheckResult, Role
        from sprinkle.storage.layered import MemberRecord
        
        # Set up permission to allow
        async def check_permission(user_id, conversation_id, action):
            if action == Action.REMOVE_MEMBER:
                return PermissionCheckResult(allowed=True, role=Role.ADMIN)
            return PermissionCheckResult(allowed=True)
        
        mock_permission.check_permission = AsyncMock(side_effect=check_permission)
        
        # Mock member exists
        mock_storage.get_member = AsyncMock(return_value=MemberRecord(
            conversation_id="conv_123",
            user_id="user_789",
            role="member",  # Not owner
            is_active=True,
        ))
        
        # Mock conversation
        mock_storage.get_conversation = AsyncMock(return_value=MagicMock(
            id="conv_123",
            updated_at=datetime.now(timezone.utc),
        ))
        
        await conversation_service.remove_member(
            conversation_id="conv_123",
            remover_id="admin_456",
            user_id="user_789",
        )
        
        mock_storage.remove_member.assert_called_with("conv_123", "user_789")
    
    @pytest.mark.asyncio
    async def test_remove_owner_fails(self, conversation_service, mock_storage):
        """Test that removing owner fails."""
        from sprinkle.storage.layered import MemberRecord
        from sprinkle.services.conversation_service import InvalidOperationError
        
        # Mock owner member
        mock_storage.get_member = AsyncMock(return_value=MemberRecord(
            conversation_id="conv_123",
            user_id="owner_456",
            role="owner",
            is_active=True,
        ))
        
        with pytest.raises(InvalidOperationError):
            await conversation_service.remove_member(
                conversation_id="conv_123",
                remover_id="admin_789",
                user_id="owner_456",
            )
    
    @pytest.mark.asyncio
    async def test_update_member_role_to_admin(self, conversation_service, mock_storage, mock_permission):
        """Test updating a member's role to admin."""
        from sprinkle.kernel.permission import Action, PermissionCheckResult, Role
        from sprinkle.storage.layered import MemberRecord
        
        # Set up permission to allow (only owner can set admin)
        async def check_permission(user_id, conversation_id, action):
            if action == Action.SET_ADMIN:
                return PermissionCheckResult(allowed=True, role=Role.OWNER)
            return PermissionCheckResult(allowed=True)
        
        mock_permission.check_permission = AsyncMock(side_effect=check_permission)
        
        # Mock member exists
        mock_storage.get_member = AsyncMock(return_value=MemberRecord(
            conversation_id="conv_123",
            user_id="user_789",
            role="member",
            is_active=True,
        ))
        
        # Mock update
        mock_storage.update_member_role = AsyncMock(return_value=MemberRecord(
            conversation_id="conv_123",
            user_id="user_789",
            role="admin",
            is_active=True,
        ))
        
        # Mock conversation
        mock_storage.get_conversation = AsyncMock(return_value=MagicMock(
            id="conv_123",
            updated_at=datetime.now(timezone.utc),
        ))
        
        updated = await conversation_service.update_member_role(
            conversation_id="conv_123",
            updater_id="owner_456",
            user_id="user_789",
            new_role="admin",
        )
        
        assert updated.role == "admin"
        mock_storage.update_member_role.assert_called_with("conv_123", "user_789", "admin")


# ============================================================================
# Message Service Tests
# ============================================================================

class TestMessageService:
    """Tests for MessageService."""
    
    @pytest.fixture
    def mock_storage(self):
        """Create a mock storage service."""
        storage = MagicMock()
        storage.save_message = AsyncMock()
        storage.get_message = AsyncMock(return_value=None)
        storage.get_conversation_messages = AsyncMock(return_value=[])
        storage.soft_delete_message = AsyncMock()
        storage.get_conversation = AsyncMock(return_value=None)
        storage.save_conversation = AsyncMock()  # Added for send_message test
        return storage
    
    @pytest.fixture
    def mock_permission(self):
        """Create a mock permission service."""
        from sprinkle.kernel.permission import Role, Action, PermissionCheckResult, MemberInfo
        
        permission = MagicMock()
        
        async def check_permission(user_id, conversation_id, action):
            return PermissionCheckResult(allowed=False, reason="denied")
        
        permission.check_permission = AsyncMock(side_effect=check_permission)
        permission.get_user_role = AsyncMock(return_value=None)
        permission.is_user_agent = MagicMock(return_value=False)
        
        permission.Role = Role
        
        return permission
    
    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.emit_async = AsyncMock()
        return bus
    
    @pytest.fixture
    def message_service(self, mock_storage, mock_permission, mock_event_bus):
        """Create a MessageService with mocks."""
        from sprinkle.services.message_service import MessageService
        return MessageService(
            storage=mock_storage,
            permission_service=mock_permission,
            event_bus=mock_event_bus,
            ws_manager=None,
        )
    
    @pytest.mark.asyncio
    async def test_send_message_success(self, message_service, mock_storage, mock_permission):
        """Test successfully sending a message."""
        from sprinkle.kernel.permission import Action, PermissionCheckResult, Role
        from sprinkle.storage.layered import ConversationRecord, MessageRecord
        
        conv_id = "conv_123"
        sender_id = "user_456"
        
        # Set up permission to allow
        async def check_permission(user_id, conversation_id, action):
            if action == Action.SEND_MESSAGE:
                return PermissionCheckResult(allowed=True, role=Role.MEMBER)
            return PermissionCheckResult(allowed=False)
        
        mock_permission.check_permission = AsyncMock(side_effect=check_permission)
        
        # Mock conversation exists
        mock_storage.get_conversation = AsyncMock(return_value=ConversationRecord(
            id=conv_id,
            type="group",
            name="Test",
            owner_id="owner",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ))
        
        message = await message_service.send_message(
            sender_id=sender_id,
            conversation_id=conv_id,
            content="Hello!",
            content_type="text",
        )
        
        assert message is not None
        assert message.content == "Hello!"
        assert message.conversation_id == conv_id
        assert message.sender_id == sender_id
        
        # Verify storage was called
        mock_storage.save_message.assert_called()
    
    @pytest.mark.asyncio
    async def test_send_message_permission_denied(self, message_service, mock_permission):
        """Test that sending message without permission fails."""
        from sprinkle.services.message_service import PermissionDeniedError
        
        with pytest.raises(PermissionDeniedError):
            await message_service.send_message(
                sender_id="user_123",
                conversation_id="conv_456",
                content="Hello!",
            )
    
    @pytest.mark.asyncio
    async def test_send_message_reply_to_not_found(self, message_service, mock_storage, mock_permission):
        """Test that replying to non-existent message fails."""
        from sprinkle.kernel.permission import Action, PermissionCheckResult, Role
        from sprinkle.services.message_service import InvalidOperationError
        
        conv_id = "conv_123"
        
        async def check_permission(user_id, conversation_id, action):
            return PermissionCheckResult(allowed=True, role=Role.MEMBER)
        
        mock_permission.check_permission = AsyncMock(side_effect=check_permission)
        
        # Mock conversation exists
        mock_storage.get_conversation = AsyncMock(return_value=MagicMock(
            id=conv_id,
            updated_at=datetime.now(timezone.utc),
        ))
        
        # Mock reply_to message not found
        mock_storage.get_message = AsyncMock(return_value=None)
        
        with pytest.raises(InvalidOperationError):
            await message_service.send_message(
                sender_id="user_456",
                conversation_id=conv_id,
                content="Reply",
                reply_to="nonexistent_msg",
            )
    
    @pytest.mark.asyncio
    async def test_edit_message_success(self, message_service, mock_storage, mock_permission):
        """Test successfully editing a message."""
        from sprinkle.kernel.permission import Role, PermissionCheckResult
        from sprinkle.storage.layered import MessageRecord
        
        msg_id = "msg_123"
        editor_id = "user_456"
        
        # Mock message exists
        mock_storage.get_message = AsyncMock(return_value=MessageRecord(
            id=msg_id,
            conversation_id="conv_123",
            sender_id=editor_id,  # Editor is the sender
            content="Original",
            created_at=datetime.now(timezone.utc),
            is_deleted=False,
        ))
        
        # Mock permission - sender can edit
        async def check_permission(user_id, conversation_id, action):
            return PermissionCheckResult(allowed=True, role=Role.MEMBER)
        
        mock_permission.check_permission = AsyncMock(side_effect=check_permission)
        mock_permission.get_user_role = AsyncMock(return_value=Role.MEMBER)
        mock_permission.is_user_agent = MagicMock(return_value=False)
        
        # Mock Redis for update
        mock_storage._redis = MagicMock()
        mock_storage._redis.set = AsyncMock()
        
        updated = await message_service.edit_message(
            message_id=msg_id,
            editor_id=editor_id,
            new_content="Updated!",
        )
        
        assert updated.content == "Updated!"
    
    @pytest.mark.asyncio
    async def test_edit_deleted_message_fails(self, message_service, mock_storage):
        """Test that editing a deleted message fails."""
        from sprinkle.storage.layered import MessageRecord
        from sprinkle.services.message_service import InvalidOperationError
        
        # Mock deleted message
        mock_storage.get_message = AsyncMock(return_value=MessageRecord(
            id="msg_123",
            conversation_id="conv_123",
            sender_id="user_456",
            content="Deleted",
            is_deleted=True,  # Already deleted
            created_at=datetime.now(timezone.utc),
        ))
        
        with pytest.raises(InvalidOperationError):
            await message_service.edit_message(
                message_id="msg_123",
                editor_id="user_456",
                new_content="New content",
            )
    
    @pytest.mark.asyncio
    async def test_delete_message_success(self, message_service, mock_storage, mock_permission):
        """Test successfully deleting a message."""
        from sprinkle.kernel.permission import Role
        from sprinkle.storage.layered import MessageRecord
        
        msg_id = "msg_123"
        deleter_id = "user_456"
        
        # Mock message exists
        mock_storage.get_message = AsyncMock(return_value=MessageRecord(
            id=msg_id,
            conversation_id="conv_123",
            sender_id=deleter_id,  # Deleter is the sender
            content="To delete",
            is_deleted=False,
            created_at=datetime.now(timezone.utc),
        ))
        
        # Mock permission
        async def get_role(user_id, conv_id):
            return Role.MEMBER
        
        mock_permission.get_user_role = AsyncMock(side_effect=get_role)
        mock_permission.is_user_agent = MagicMock(return_value=False)
        
        # Mock storage delete
        mock_storage.soft_delete_message = AsyncMock()
        
        await message_service.delete_message(
            message_id=msg_id,
            deleter_id=deleter_id,
        )
        
        mock_storage.soft_delete_message.assert_called_with(msg_id)
    
    @pytest.mark.asyncio
    async def test_delete_message_not_found(self, message_service, mock_storage):
        """Test deleting a non-existent message."""
        from sprinkle.services.message_service import MessageNotFoundError
        
        mock_storage.get_message = AsyncMock(return_value=None)
        
        with pytest.raises(MessageNotFoundError):
            await message_service.delete_message(
                message_id="nonexistent",
                deleter_id="user_123",
            )


# ============================================================================
# Migration Task Tests
# ============================================================================

class TestStorageMigrationTask:
    """Tests for StorageMigrationTask."""
    
    @pytest.fixture
    def mock_storage(self):
        """Create a mock storage service."""
        storage = MagicMock()
        storage._redis = MagicMock()
        storage._redis.scan_iter = AsyncMock(return_value=iter([]))
        return storage
    
    @pytest.mark.asyncio
    async def test_migration_runs_successfully(self, mock_storage):
        """Test that migration task runs without errors."""
        from sprinkle.storage.layered import StorageMigrationTask, MigrationResult
        
        mock_storage._redis.scan_iter = AsyncMock(return_value=iter([
            b"msg:conv_123:2026-04-01",  # Old message
        ]))
        mock_storage._redis.zrange = AsyncMock(return_value=[b"msg_456"])
        mock_storage._redis.get = AsyncMock(return_value=json.dumps({
            "id": "msg_456",
            "conversation_id": "conv_123",
            "sender_id": "user_789",
            "content": "Old message",
            "content_type": "text",
            "metadata": {},
            "mentions": [],
            "reply_to": None,
            "is_deleted": False,
            "created_at": "2026-04-01T00:00:00+00:00",
            "edited_at": None,
            "deleted_at": None,
        }))
        mock_storage._redis.delete = AsyncMock()
        mock_storage._redis.ttl = AsyncMock(return_value=-1)
        mock_storage._redis.expire = AsyncMock()
        
        task = StorageMigrationTask(mock_storage)
        result = await task.run()
        
        assert isinstance(result, MigrationResult)
        # Old messages should be migrated/deleted
        assert mock_storage._redis.scan_iter.called


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
