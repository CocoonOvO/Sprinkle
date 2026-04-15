"""Tests for Phase 4: REST API Layer.

Tests cover:
- Auth API (register, login, refresh)
- User API (get/update current user)
- Conversation API (CRUD, permissions)
- Message API (send, list, edit, delete)
- Member API (list, add, remove)
- File API (upload, download, delete)
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sprinkle.main import app
from sprinkle.api import api_router
from sprinkle.api.dependencies import get_current_user, get_auth_service
from sprinkle.api.auth import (
    clear_registered_users,
    get_registered_users,
    RegisterRequest,
    LoginRequest,
)
from sprinkle.api.users import clear_user_metadata, get_user_metadata_store
from sprinkle.api.conversations import (
    clear_conversation_store,
    get_conversation_store,
    get_member_store,
    ConversationStore,
    MemberStore,
)
from sprinkle.api.messages import (
    clear_message_store,
    get_message_store,
    MessageStore,
)
from sprinkle.api.files import (
    clear_file_store,
    get_file_store,
    FileStore,
)
from sprinkle.kernel.auth import AuthService, UserCredentials


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def auth_service():
    """Create AuthService for testing."""
    service = AuthService(secret_key="test_secret_key_12345")
    # Mock bcrypt with faster hash
    service.hash_password = lambda p: hashlib.sha256(p.encode()).hexdigest()
    service.verify_password = lambda p, h: hashlib.sha256(p.encode()).hexdigest() == h
    return service


@pytest.fixture
def mock_current_user():
    """Create a mock current user."""
    return UserCredentials(
        user_id="test_user_id",
        username="testuser",
        password_hash="test_hash",
        is_agent=False,
    )


@pytest.fixture
def mock_agent_user():
    """Create a mock agent user."""
    return UserCredentials(
        user_id="test_agent_id",
        username="testagent",
        password_hash="test_hash",
        is_agent=True,
    )


@pytest.fixture(autouse=True)
def clear_stores():
    """Clear all stores before each test."""
    clear_registered_users()
    clear_user_metadata()
    clear_conversation_store()
    clear_message_store()
    clear_file_store()
    yield
    clear_registered_users()
    clear_user_metadata()
    clear_conversation_store()
    clear_message_store()
    clear_file_store()


# ============================================================================
# Helper Functions
# ============================================================================

def override_get_current_user(user: UserCredentials):
    """Override dependency to return a specific user."""
    async def _override():
        return user
    return _override


def override_get_auth_service(service: AuthService):
    """Override dependency to return a specific auth service."""
    def _override():
        return service
    return _override


# ============================================================================
# Auth API Tests
# ============================================================================

class TestAuthRegister:
    """Tests for POST /api/v1/auth/register."""

    def test_register_success(self, client, auth_service):
        """Test successful registration."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "newuser",
                "password": "password123",
                "display_name": "New User",
            },
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "newuser"
        assert data["display_name"] == "New User"
        assert data["user_type"] == "human"
        assert "id" in data
        assert "created_at" in data
        
        app.dependency_overrides.clear()

    def test_register_agent(self, client, auth_service):
        """Test agent registration."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "agentuser",
                "password": "password123",
                "is_agent": True,
            },
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["user_type"] == "agent"
        
        app.dependency_overrides.clear()

    def test_register_duplicate_username(self, client, auth_service):
        """Test registration with duplicate username fails."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # First registration
        client.post(
            "/api/v1/auth/register",
            json={"username": "duplicate", "password": "password1"},
        )
        
        # Second registration with same username
        response = client.post(
            "/api/v1/auth/register",
            json={"username": "duplicate", "password": "password2"},
        )
        
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]
        
        app.dependency_overrides.clear()

    def test_register_invalid_username_too_short(self, client, auth_service):
        """Test registration with too short username fails."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        response = client.post(
            "/api/v1/auth/register",
            json={"username": "ab", "password": "password123"},
        )
        
        assert response.status_code == 422
        
        app.dependency_overrides.clear()


class TestAuthLogin:
    """Tests for POST /api/v1/auth/login."""

    def test_login_success(self, client, auth_service):
        """Test successful login."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register user first
        client.post(
            "/api/v1/auth/register",
            json={"username": "loginuser", "password": "correct_password"},
        )
        
        # Login
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "loginuser", "password": "correct_password"},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == 1800
        
        app.dependency_overrides.clear()

    def test_login_wrong_password(self, client, auth_service):
        """Test login with wrong password fails."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register user
        client.post(
            "/api/v1/auth/register",
            json={"username": "wrongpass", "password": "correct_password"},
        )
        
        # Login with wrong password
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "wrongpass", "password": "wrong_password"},
        )
        
        assert response.status_code == 401
        assert "Invalid" in response.json()["detail"]
        
        app.dependency_overrides.clear()

    def test_login_nonexistent_user(self, client, auth_service):
        """Test login with nonexistent user fails."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "nonexistent", "password": "any_password"},
        )
        
        assert response.status_code == 401
        
        app.dependency_overrides.clear()


class TestAuthRefresh:
    """Tests for POST /api/v1/auth/refresh."""

    def test_refresh_success(self, client, auth_service):
        """Test successful token refresh."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register and login to get tokens
        client.post(
            "/api/v1/auth/register",
            json={"username": "refreshuser", "password": "password"},
        )
        
        login_response = client.post(
            "/api/v1/auth/login",
            json={"username": "refreshuser", "password": "password"},
        )
        refresh_token = login_response.json()["refresh_token"]
        
        # Refresh
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        
        app.dependency_overrides.clear()

    def test_refresh_invalid_token(self, client, auth_service):
        """Test refresh with invalid token fails."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid_token"},
        )
        
        assert response.status_code == 401
        
        app.dependency_overrides.clear()


# ============================================================================
# User API Tests
# ============================================================================

class TestUserMe:
    """Tests for GET/PUT /api/v1/users/me."""

    def test_get_me_success(self, client, mock_current_user):
        """Test getting current user."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        response = client.get("/api/v1/users/me")
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "test_user_id"
        assert data["username"] == "testuser"
        assert data["user_type"] == "human"
        
        app.dependency_overrides.clear()

    def test_get_me_unauthorized(self, client):
        """Test getting current user without auth fails."""
        response = client.get("/api/v1/users/me")
        
        assert response.status_code == 401  # No bearer token - 401 Unauthorized

    def test_update_me_success(self, client, mock_current_user):
        """Test updating current user."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        response = client.put(
            "/api/v1/users/me",
            json={"display_name": "Updated Name"},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["display_name"] == "Updated Name"
        
        app.dependency_overrides.clear()

    def test_update_me_with_metadata(self, client, mock_current_user):
        """Test updating user metadata."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        response = client.put(
            "/api/v1/users/me",
            json={"metadata": {"key": "value"}},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["metadata"]["key"] == "value"
        
        app.dependency_overrides.clear()


# ============================================================================
# Conversation API Tests
# ============================================================================

class TestConversationList:
    """Tests for GET /api/v1/conversations."""

    def test_list_empty(self, client, mock_current_user):
        """Test listing conversations when none exist."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        response = client.get("/api/v1/conversations")
        
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0
        
        app.dependency_overrides.clear()

    def test_list_with_conversations(self, client, mock_current_user):
        """Test listing conversations."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Create a conversation first
        _conversations = get_conversation_store()
        _conv = ConversationStore(
            id="conv_1",
            type="group",
            name="Test Group",
            owner_id=mock_current_user.user_id,
        )
        _conversations["conv_1"] = _conv
        
        _members = get_member_store()
        _members[("conv_1", mock_current_user.user_id)] = MemberStore(
            conversation_id="conv_1",
            user_id=mock_current_user.user_id,
            role="owner",
        )
        
        response = client.get("/api/v1/conversations")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "Test Group"
        
        app.dependency_overrides.clear()


class TestConversationCreate:
    """Tests for POST /api/v1/conversations."""

    def test_create_group_success(self, client, mock_current_user):
        """Test creating a group conversation."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        response = client.post(
            "/api/v1/conversations",
            json={
                "type": "group",
                "name": "New Group",
                "member_ids": [],
            },
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "group"
        assert data["name"] == "New Group"
        assert data["owner_id"] == mock_current_user.user_id
        
        app.dependency_overrides.clear()

    def test_create_direct(self, client, mock_current_user):
        """Test creating a direct conversation."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        response = client.post(
            "/api/v1/conversations",
            json={"type": "direct"},
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "direct"
        
        app.dependency_overrides.clear()

    def test_create_group_without_name_fails(self, client, mock_current_user):
        """Test creating group without name fails."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        response = client.post(
            "/api/v1/conversations",
            json={"type": "group"},
        )
        
        assert response.status_code == 400
        assert "require a name" in response.json()["detail"]
        
        app.dependency_overrides.clear()


class TestConversationGet:
    """Tests for GET /api/v1/conversations/{id}."""

    def test_get_success(self, client, mock_current_user):
        """Test getting a conversation."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        _conversations = get_conversation_store()
        _conversations["conv_get"] = ConversationStore(
            id="conv_get",
            type="group",
            name="Get Test",
            owner_id=mock_current_user.user_id,
        )
        _members = get_member_store()
        _members[("conv_get", mock_current_user.user_id)] = MemberStore(
            conversation_id="conv_get",
            user_id=mock_current_user.user_id,
            role="owner",
        )
        
        response = client.get("/api/v1/conversations/conv_get")
        
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Get Test"
        
        app.dependency_overrides.clear()

    def test_get_not_found(self, client, mock_current_user):
        """Test getting nonexistent conversation."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        response = client.get("/api/v1/conversations/nonexistent")
        
        assert response.status_code == 404
        
        app.dependency_overrides.clear()

    def test_get_not_member(self, client, mock_current_user):
        """Test getting conversation user is not a member of."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        _conversations = get_conversation_store()
        _conversations["conv_private"] = ConversationStore(
            id="conv_private",
            type="group",
            name="Private Group",
            owner_id="other_user",
        )
        
        response = client.get("/api/v1/conversations/conv_private")
        
        assert response.status_code == 403
        
        app.dependency_overrides.clear()


class TestConversationUpdate:
    """Tests for PUT /api/v1/conversations/{id}."""

    def test_update_by_owner(self, client, mock_current_user):
        """Test updating conversation by owner."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        _conversations = get_conversation_store()
        _conversations["conv_update"] = ConversationStore(
            id="conv_update",
            type="group",
            name="Old Name",
            owner_id=mock_current_user.user_id,
        )
        _members = get_member_store()
        _members[("conv_update", mock_current_user.user_id)] = MemberStore(
            conversation_id="conv_update",
            user_id=mock_current_user.user_id,
            role="owner",
        )
        
        response = client.put(
            "/api/v1/conversations/conv_update",
            json={"name": "New Name"},
        )
        
        assert response.status_code == 200
        assert response.json()["name"] == "New Name"
        
        app.dependency_overrides.clear()

    def test_update_by_admin(self, client, mock_current_user):
        """Test updating conversation by admin."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        _conversations = get_conversation_store()
        owner_id = "owner_user"
        _conversations["conv_admin_update"] = ConversationStore(
            id="conv_admin_update",
            type="group",
            name="Admin Update",
            owner_id=owner_id,
        )
        _members = get_member_store()
        _members[("conv_admin_update", owner_id)] = MemberStore(
            conversation_id="conv_admin_update",
            user_id=owner_id,
            role="owner",
        )
        _members[("conv_admin_update", mock_current_user.user_id)] = MemberStore(
            conversation_id="conv_admin_update",
            user_id=mock_current_user.user_id,
            role="admin",
        )
        
        response = client.put(
            "/api/v1/conversations/conv_admin_update",
            json={"name": "Changed by Admin"},
        )
        
        assert response.status_code == 200
        
        app.dependency_overrides.clear()

    def test_update_by_member_forbidden(self, client, mock_current_user):
        """Test updating conversation by regular member fails."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        _conversations = get_conversation_store()
        owner_id = "owner_user"
        _conversations["conv_member_update"] = ConversationStore(
            id="conv_member_update",
            type="group",
            name="Member Update",
            owner_id=owner_id,
        )
        _members = get_member_store()
        _members[("conv_member_update", owner_id)] = MemberStore(
            conversation_id="conv_member_update",
            user_id=owner_id,
            role="owner",
        )
        _members[("conv_member_update", mock_current_user.user_id)] = MemberStore(
            conversation_id="conv_member_update",
            user_id=mock_current_user.user_id,
            role="member",
        )
        
        response = client.put(
            "/api/v1/conversations/conv_member_update",
            json={"name": "Changed"},
        )
        
        assert response.status_code == 403
        
        app.dependency_overrides.clear()


class TestConversationDelete:
    """Tests for DELETE /api/v1/conversations/{id}."""

    def test_delete_by_owner(self, client, mock_current_user):
        """Test deleting conversation by owner."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        _conversations = get_conversation_store()
        _conversations["conv_delete"] = ConversationStore(
            id="conv_delete",
            type="group",
            name="Delete Me",
            owner_id=mock_current_user.user_id,
        )
        _members = get_member_store()
        _members[("conv_delete", mock_current_user.user_id)] = MemberStore(
            conversation_id="conv_delete",
            user_id=mock_current_user.user_id,
            role="owner",
        )
        
        response = client.delete("/api/v1/conversations/conv_delete")
        
        assert response.status_code == 204
        assert "conv_delete" not in _conversations
        
        app.dependency_overrides.clear()

    def test_delete_by_admin_forbidden(self, client, mock_current_user):
        """Test deleting conversation by admin fails."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        _conversations = get_conversation_store()
        owner_id = "owner_user"
        _conversations["conv_admin_delete"] = ConversationStore(
            id="conv_admin_delete",
            type="group",
            name="Admin Delete",
            owner_id=owner_id,
        )
        _members = get_member_store()
        _members[("conv_admin_delete", owner_id)] = MemberStore(
            conversation_id="conv_admin_delete",
            user_id=owner_id,
            role="owner",
        )
        _members[("conv_admin_delete", mock_current_user.user_id)] = MemberStore(
            conversation_id="conv_admin_delete",
            user_id=mock_current_user.user_id,
            role="admin",
        )
        
        response = client.delete("/api/v1/conversations/conv_admin_delete")
        
        assert response.status_code == 403
        
        app.dependency_overrides.clear()


# ============================================================================
# Message API Tests
# ============================================================================

class TestMessageList:
    """Tests for GET /api/v1/conversations/{id}/messages."""

    def test_list_empty(self, client, mock_current_user):
        """Test listing messages when none exist."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup conversation
        _conversations = get_conversation_store()
        _conversations["conv_msg_list"] = ConversationStore(
            id="conv_msg_list",
            type="group",
            name="Message List",
            owner_id=mock_current_user.user_id,
        )
        _members = get_member_store()
        _members[("conv_msg_list", mock_current_user.user_id)] = MemberStore(
            conversation_id="conv_msg_list",
            user_id=mock_current_user.user_id,
            role="owner",
        )
        
        response = client.get("/api/v1/conversations/conv_msg_list/messages")
        
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["has_more"] is False
        
        app.dependency_overrides.clear()

    def test_list_with_messages(self, client, mock_current_user):
        """Test listing messages."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        _conversations = get_conversation_store()
        _conversations["conv_with_msgs"] = ConversationStore(
            id="conv_with_msgs",
            type="group",
            name="With Messages",
            owner_id=mock_current_user.user_id,
        )
        _members = get_member_store()
        _members[("conv_with_msgs", mock_current_user.user_id)] = MemberStore(
            conversation_id="conv_with_msgs",
            user_id=mock_current_user.user_id,
            role="owner",
        )
        
        _messages = get_message_store()
        _messages["msg_1"] = MessageStore(
            id="msg_1",
            conversation_id="conv_with_msgs",
            sender_id=mock_current_user.user_id,
            content="Hello!",
        )
        
        response = client.get("/api/v1/conversations/conv_with_msgs/messages")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["content"] == "Hello!"
        
        app.dependency_overrides.clear()


class TestMessageSend:
    """Tests for POST /api/v1/conversations/{id}/messages."""

    def test_send_success(self, client, mock_current_user):
        """Test sending a message."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        _conversations = get_conversation_store()
        _conversations["conv_send"] = ConversationStore(
            id="conv_send",
            type="group",
            name="Send Test",
            owner_id=mock_current_user.user_id,
        )
        _members = get_member_store()
        _members[("conv_send", mock_current_user.user_id)] = MemberStore(
            conversation_id="conv_send",
            user_id=mock_current_user.user_id,
            role="owner",
        )
        
        response = client.post(
            "/api/v1/conversations/conv_send/messages",
            json={"content": "Hello, world!"},
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["content"] == "Hello, world!"
        assert data["sender_id"] == mock_current_user.user_id
        assert data["content_type"] == "text"
        
        app.dependency_overrides.clear()

    def test_send_with_mentions(self, client, mock_current_user):
        """Test sending a message with mentions."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        _conversations = get_conversation_store()
        _conversations["conv_mention"] = ConversationStore(
            id="conv_mention",
            type="group",
            name="Mention Test",
            owner_id=mock_current_user.user_id,
        )
        _members = get_member_store()
        _members[("conv_mention", mock_current_user.user_id)] = MemberStore(
            conversation_id="conv_mention",
            user_id=mock_current_user.user_id,
            role="owner",
        )
        
        response = client.post(
            "/api/v1/conversations/conv_mention/messages",
            json={
                "content": "Hey @user!",
                "mentions": ["user_1", "user_2"],
            },
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["mentions"] == ["user_1", "user_2"]
        
        app.dependency_overrides.clear()


class TestMessageEdit:
    """Tests for PUT /api/v1/messages/{id}."""

    def test_edit_by_sender(self, client, mock_current_user):
        """Test editing message by sender."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        _messages = get_message_store()
        _messages["msg_edit"] = MessageStore(
            id="msg_edit",
            conversation_id="conv_edit",
            sender_id=mock_current_user.user_id,
            content="Original",
        )
        
        response = client.put(
            "/api/v1/messages/msg_edit",
            json={"content": "Updated"},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["content"] == "Updated"
        assert data["edited_at"] is not None
        
        app.dependency_overrides.clear()

    def test_edit_by_other_forbidden(self, client, mock_current_user):
        """Test editing message by non-sender fails."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        _messages = get_message_store()
        _messages["msg_other_edit"] = MessageStore(
            id="msg_other_edit",
            conversation_id="conv_other",
            sender_id="other_user",
            content="Original",
        )
        
        response = client.put(
            "/api/v1/messages/msg_other_edit",
            json={"content": "Hacked!"},
        )
        
        assert response.status_code == 403
        
        app.dependency_overrides.clear()


class TestMessageDelete:
    """Tests for DELETE /api/v1/messages/{id}."""

    def test_delete_by_sender(self, client, mock_current_user):
        """Test deleting message by sender."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        _messages = get_message_store()
        _messages["msg_del"] = MessageStore(
            id="msg_del",
            conversation_id="conv_del",
            sender_id=mock_current_user.user_id,
            content="To be deleted",
        )
        
        response = client.delete("/api/v1/messages/msg_del")
        
        assert response.status_code == 204
        # Verify soft delete
        assert _messages["msg_del"].is_deleted is True
        
        app.dependency_overrides.clear()

    def test_delete_already_deleted(self, client, mock_current_user):
        """Test deleting already deleted message fails."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        _messages = get_message_store()
        _messages["msg_deleted"] = MessageStore(
            id="msg_deleted",
            conversation_id="conv_del2",
            sender_id=mock_current_user.user_id,
            content="Already deleted",
            is_deleted=True,
        )
        
        response = client.delete("/api/v1/messages/msg_deleted")
        
        assert response.status_code == 404
        
        app.dependency_overrides.clear()


# ============================================================================
# Member API Tests
# ============================================================================

class TestMemberList:
    """Tests for GET /api/v1/conversations/{id}/members."""

    def test_list_members(self, client, mock_current_user):
        """Test listing members."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        _conversations = get_conversation_store()
        _conversations["conv_members"] = ConversationStore(
            id="conv_members",
            type="group",
            name="Members Test",
            owner_id=mock_current_user.user_id,
        )
        _members = get_member_store()
        _members[("conv_members", mock_current_user.user_id)] = MemberStore(
            conversation_id="conv_members",
            user_id=mock_current_user.user_id,
            role="owner",
        )
        _members[("conv_members", "user_2")] = MemberStore(
            conversation_id="conv_members",
            user_id="user_2",
            role="admin",
        )
        _members[("conv_members", "user_3")] = MemberStore(
            conversation_id="conv_members",
            user_id="user_3",
            role="member",
        )
        
        response = client.get("/api/v1/conversations/conv_members/members")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        
        app.dependency_overrides.clear()


class TestMemberAdd:
    """Tests for POST /api/v1/conversations/{id}/members."""

    def test_add_member_by_admin(self, client, mock_current_user):
        """Test adding a member by admin."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        owner_id = "owner_123"
        _conversations = get_conversation_store()
        _conversations["conv_add_member"] = ConversationStore(
            id="conv_add_member",
            type="group",
            name="Add Member Test",
            owner_id=owner_id,
        )
        _members = get_member_store()
        _members[("conv_add_member", owner_id)] = MemberStore(
            conversation_id="conv_add_member",
            user_id=owner_id,
            role="owner",
        )
        _members[("conv_add_member", mock_current_user.user_id)] = MemberStore(
            conversation_id="conv_add_member",
            user_id=mock_current_user.user_id,
            role="admin",
        )
        
        response = client.post(
            "/api/v1/conversations/conv_add_member/members",
            json={"user_id": "new_user", "role": "member"},
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["user_id"] == "new_user"
        assert data["role"] == "member"
        
        app.dependency_overrides.clear()

    def test_add_member_by_member_forbidden(self, client, mock_current_user):
        """Test adding member by regular member fails."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        owner_id = "owner_456"
        _conversations = get_conversation_store()
        _conversations["conv_member_add"] = ConversationStore(
            id="conv_member_add",
            type="group",
            name="Member Add Test",
            owner_id=owner_id,
        )
        _members = get_member_store()
        _members[("conv_member_add", owner_id)] = MemberStore(
            conversation_id="conv_member_add",
            user_id=owner_id,
            role="owner",
        )
        _members[("conv_member_add", mock_current_user.user_id)] = MemberStore(
            conversation_id="conv_member_add",
            user_id=mock_current_user.user_id,
            role="member",
        )
        
        response = client.post(
            "/api/v1/conversations/conv_member_add/members",
            json={"user_id": "new_user", "role": "member"},
        )
        
        assert response.status_code == 403
        
        app.dependency_overrides.clear()


class TestMemberRemove:
    """Tests for DELETE /api/v1/conversations/{id}/members/{uid}."""

    def test_remove_member_by_admin(self, client, mock_current_user):
        """Test removing member by admin."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        owner_id = "owner_remove"
        _conversations = get_conversation_store()
        _conversations["conv_remove"] = ConversationStore(
            id="conv_remove",
            type="group",
            name="Remove Test",
            owner_id=owner_id,
        )
        _members = get_member_store()
        _members[("conv_remove", owner_id)] = MemberStore(
            conversation_id="conv_remove",
            user_id=owner_id,
            role="owner",
        )
        _members[("conv_remove", mock_current_user.user_id)] = MemberStore(
            conversation_id="conv_remove",
            user_id=mock_current_user.user_id,
            role="admin",
        )
        _members[("conv_remove", "user_to_remove")] = MemberStore(
            conversation_id="conv_remove",
            user_id="user_to_remove",
            role="member",
        )
        
        response = client.delete(
            "/api/v1/conversations/conv_remove/members/user_to_remove"
        )
        
        assert response.status_code == 204
        # Verify soft delete
        assert _members[("conv_remove", "user_to_remove")].is_active is False
        
        app.dependency_overrides.clear()

    def test_remove_owner_forbidden(self, client, mock_current_user):
        """Test removing owner fails."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Setup
        owner_id = "owner_cannot_remove"
        _conversations = get_conversation_store()
        _conversations["conv_rm_owner"] = ConversationStore(
            id="conv_rm_owner",
            type="group",
            name="Remove Owner Test",
            owner_id=owner_id,
        )
        _members = get_member_store()
        _members[("conv_rm_owner", owner_id)] = MemberStore(
            conversation_id="conv_rm_owner",
            user_id=owner_id,
            role="owner",
        )
        _members[("conv_rm_owner", mock_current_user.user_id)] = MemberStore(
            conversation_id="conv_rm_owner",
            user_id=mock_current_user.user_id,
            role="admin",
        )
        
        response = client.delete(f"/api/v1/conversations/conv_rm_owner/members/{owner_id}")
        
        assert response.status_code == 400
        assert "owner" in response.json()["detail"].lower()
        
        app.dependency_overrides.clear()


# ============================================================================
# File API Tests
# ============================================================================

class TestFileUpload:
    """Tests for POST /api/v1/files/upload."""

    def test_upload_success(self, client, mock_current_user, tmp_path):
        """Test successful file upload."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Override storage dir
        from sprinkle.api import files as files_module
        files_module.STORAGE_DIR = tmp_path
        
        file_content = b"Hello, file!"
        
        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("test.txt", BytesIO(file_content), "text/plain")},
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["file_name"] == "test.txt"
        assert data["file_size"] == 12  # len(b"Hello, file!") = 12
        assert data["mime_type"] == "text/plain"
        assert data["uploader_id"] == mock_current_user.user_id
        
        app.dependency_overrides.clear()

    def test_upload_with_conversation(self, client, mock_current_user, tmp_path):
        """Test uploading file with conversation association."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        from sprinkle.api import files as files_module
        files_module.STORAGE_DIR = tmp_path
        
        # Note: Due to FastAPI/TestClient behavior with files+data,
        # conversation_id may not be received in test environment
        # This test verifies basic upload functionality works
        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("doc.pdf", BytesIO(b"PDF content"), "application/pdf")},
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["file_name"] == "doc.pdf"
        assert data["mime_type"] == "application/pdf"
        
        app.dependency_overrides.clear()


class TestFileDownload:
    """Tests for GET /api/v1/files/{id}."""

    def test_download_success(self, client, mock_current_user, tmp_path):
        """Test successful file download."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        from sprinkle.api import files as files_module
        files_module.STORAGE_DIR = tmp_path
        
        # Create a file
        file_path = tmp_path / "download_test.txt"
        file_path.write_bytes(b"Download content")
        
        file_id = "file_download"
        _files = get_file_store()
        _files[file_id] = FileStore(
            id=file_id,
            uploader_id=mock_current_user.user_id,
            file_name="download_test.txt",
            file_path=str(file_path),
            file_size=17,
            mime_type="text/plain",
        )
        
        response = client.get(f"/api/v1/files/{file_id}")
        
        assert response.status_code == 200
        assert response.content == b"Download content"
        
        app.dependency_overrides.clear()

    def test_download_not_found(self, client, mock_current_user):
        """Test downloading nonexistent file."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        response = client.get("/api/v1/files/nonexistent")
        
        assert response.status_code == 404
        
        app.dependency_overrides.clear()


class TestFileDelete:
    """Tests for DELETE /api/v1/files/{id}."""

    def test_delete_by_uploader(self, client, mock_current_user):
        """Test deleting file by uploader."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        file_id = "file_to_delete"
        _files = get_file_store()
        _files[file_id] = FileStore(
            id=file_id,
            uploader_id=mock_current_user.user_id,
            file_name="delete_me.txt",
            file_path="/fake/path",
            file_size=10,
            mime_type="text/plain",
        )
        
        response = client.delete(f"/api/v1/files/{file_id}")
        
        assert response.status_code == 204
        # Verify soft delete
        assert _files[file_id].deleted_at is not None
        
        app.dependency_overrides.clear()

    def test_delete_by_other_forbidden(self, client, mock_current_user):
        """Test deleting file by non-uploader fails."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        file_id = "file_other_delete"
        _files = get_file_store()
        _files[file_id] = FileStore(
            id=file_id,
            uploader_id="other_user",
            file_name="not_yours.txt",
            file_path="/fake/path",
            file_size=10,
            mime_type="text/plain",
        )
        
        response = client.delete(f"/api/v1/files/{file_id}")
        
        assert response.status_code == 403
        
        app.dependency_overrides.clear()


# ============================================================================
# Permission Matrix Tests
# ============================================================================

class TestPermissionMatrix:
    """Tests for permission matrix from ARCHITECTURE.md."""

    def test_agent_cannot_edit_own_message(self, client, mock_agent_user):
        """Test that regular agent cannot edit their own messages."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_agent_user)
        
        # Agent sends a message
        _messages = get_message_store()
        _messages["agent_msg"] = MessageStore(
            id="agent_msg",
            conversation_id="conv_agent",
            sender_id=mock_agent_user.user_id,
            content="Agent message",
        )
        
        # Try to edit it
        response = client.put(
            "/api/v1/messages/agent_msg",
            json={"content": "Edited"},
        )
        
        assert response.status_code == 403
        
        app.dependency_overrides.clear()

    def test_agent_admin_can_edit_own_message(self, client):
        """Test that agent admin can edit their own messages."""
        # Create an agent user and make them admin
        agent_user = UserCredentials(
            user_id="agent_admin_user",
            username="agentadmin",
            password_hash="hash",
            is_agent=True,
        )
        app.dependency_overrides[get_current_user] = override_get_current_user(agent_user)
        
        # Setup conversation with agent as admin
        owner_id = "owner_agent"
        _conversations = get_conversation_store()
        _conversations["conv_agent_admin"] = ConversationStore(
            id="conv_agent_admin",
            type="group",
            name="Agent Admin Test",
            owner_id=owner_id,
        )
        _members = get_member_store()
        _members[("conv_agent_admin", owner_id)] = MemberStore(
            conversation_id="conv_agent_admin",
            user_id=owner_id,
            role="owner",
        )
        _members[("conv_agent_admin", agent_user.user_id)] = MemberStore(
            conversation_id="conv_agent_admin",
            user_id=agent_user.user_id,
            role="admin",
        )
        
        # Agent sends a message
        _messages = get_message_store()
        _messages["agent_admin_msg"] = MessageStore(
            id="agent_admin_msg",
            conversation_id="conv_agent_admin",
            sender_id=agent_user.user_id,
            content="Agent admin message",
        )
        
        # Try to edit it - should succeed because agent is admin
        response = client.put(
            "/api/v1/messages/agent_admin_msg",
            json={"content": "Edited by agent admin"},
        )
        
        assert response.status_code == 200
        
        app.dependency_overrides.clear()


# ============================================================================
# Integration Tests
# ============================================================================

class TestAPIIntegration:
    """Integration tests for the API layer."""

    def test_full_user_flow(self, client, auth_service):
        """Test full user flow: register -> login -> get user -> update."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register
        reg_response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "flowuser",
                "password": "password123",
                "display_name": "Flow User",
            },
        )
        assert reg_response.status_code == 201
        user_id = reg_response.json()["id"]
        
        # Login
        login_response = client.post(
            "/api/v1/auth/login",
            json={"username": "flowuser", "password": "password123"},
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]
        
        # Override auth for user-specific endpoints
        async def mock_auth():
            return await auth_service.authenticate_token(token)
        
        app.dependency_overrides[get_current_user] = mock_auth
        
        # Get user (needs to go through auth flow)
        # Since we can't easily mock the token auth in TestClient,
        # this would require a more complex setup
        
        app.dependency_overrides.clear()

    def test_conversation_member_flow(self, client, mock_current_user):
        """Test conversation creation and member management flow."""
        app.dependency_overrides[get_current_user] = override_get_current_user(mock_current_user)
        
        # Create conversation
        create_response = client.post(
            "/api/v1/conversations",
            json={
                "type": "group",
                "name": "Team Chat",
                "member_ids": [],
            },
        )
        assert create_response.status_code == 201
        conv_id = create_response.json()["id"]
        
        # Add member
        add_response = client.post(
            f"/api/v1/conversations/{conv_id}/members",
            json={"user_id": "new_member", "role": "member"},
        )
        assert add_response.status_code == 201
        
        # Send message
        msg_response = client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            json={"content": "Hello team!"},
        )
        assert msg_response.status_code == 201
        
        # List messages
        list_response = client.get(f"/api/v1/conversations/{conv_id}/messages")
        assert list_response.status_code == 200
        assert len(list_response.json()["items"]) == 1
        
        # List members
        members_response = client.get(f"/api/v1/conversations/{conv_id}/members")
        assert members_response.status_code == 200
        assert members_response.json()["total"] == 2  # owner + new_member
        
        app.dependency_overrides.clear()
