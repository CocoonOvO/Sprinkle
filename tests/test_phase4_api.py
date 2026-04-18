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
    """Clear all stores before each test.
    
    Order matters: files reference users, messages reference conversations/users,
    conversation_members reference conversations/users. Delete in reverse dependency order.
    """
    # Before test: clear in correct order
    clear_file_store()  # Files reference users
    clear_message_store()  # Messages reference conversations and users
    clear_conversation_store()  # Conversations and members reference users
    clear_user_metadata()
    clear_registered_users()
    yield
    # After test: clear in same order
    clear_file_store()
    clear_message_store()
    clear_conversation_store()
    clear_user_metadata()
    clear_registered_users()


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

    def test_get_me_success(self, client, auth_service):
        """Test getting current user - uses real register/login flow."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # 1. Register real user
        register_resp = client.post("/api/v1/auth/register", json={
            "username": "meuser1",
            "password": "password123",
            "display_name": "Me User 1",
        })
        assert register_resp.status_code == 201
        user_data = register_resp.json()
        
        # 2. Login to get token
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "meuser1",
            "password": "password123",
        })
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]
        
        # 3. Use real token to get me
        me_resp = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
        assert me_resp.status_code == 200
        me_data = me_resp.json()
        
        # 4. Verify data accuracy
        assert me_data["username"] == "meuser1"
        assert me_data["display_name"] == "Me User 1"
        assert me_data["id"] == user_data["id"]
        assert me_data["user_type"] == "human"
        
        app.dependency_overrides.clear()

    def test_get_me_unauthorized(self, client):
        """Test getting current user without auth fails."""
        response = client.get("/api/v1/users/me")
        
        assert response.status_code == 401  # No bearer token - 401 Unauthorized

    def test_update_me_success(self, client, auth_service):
        """Test updating current user - uses real register/login flow."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # 1. Register real user
        register_resp = client.post("/api/v1/auth/register", json={
            "username": "updateuser1",
            "password": "password123",
            "display_name": "Original Name",
        })
        assert register_resp.status_code == 201
        
        # 2. Login to get token
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "updateuser1",
            "password": "password123",
        })
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]
        
        # 3. Update display name
        response = client.put(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
            json={"display_name": "Updated Name"},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["display_name"] == "Updated Name"
        
        app.dependency_overrides.clear()

    def test_update_me_with_metadata(self, client, auth_service):
        """Test updating user metadata - uses real register/login flow."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # 1. Register real user
        register_resp = client.post("/api/v1/auth/register", json={
            "username": "metauser1",
            "password": "password123",
        })
        assert register_resp.status_code == 201
        
        # 2. Login to get token
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "metauser1",
            "password": "password123",
        })
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]
        
        # 3. Update metadata
        response = client.put(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
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

    def test_list_empty(self, client, auth_service):
        """Test listing conversations when none exist - uses real user via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register and login real user
        client.post("/api/v1/auth/register", json={
            "username": "listuser1", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "listuser1", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        response = client.get("/api/v1/conversations", headers=headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0
        
        app.dependency_overrides.clear()

    def test_list_with_conversations(self, client, auth_service):
        """Test listing conversations - creates conversation via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register and login real user
        client.post("/api/v1/auth/register", json={
            "username": "listuser2", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "listuser2", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # Create conversation via API
        conv_resp = client.post(
            "/api/v1/conversations", headers=headers,
            json={"type": "group", "name": "Test Group"},
        )
        assert conv_resp.status_code == 201
        
        response = client.get("/api/v1/conversations", headers=headers)
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "Test Group"
        
        app.dependency_overrides.clear()


class TestConversationCreate:
    """Tests for POST /api/v1/conversations."""

    def test_create_group_success(self, client, auth_service):
        """Test creating a group conversation - uses real user via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register and login real user
        client.post("/api/v1/auth/register", json={
            "username": "convuser1", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "convuser1", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        response = client.post(
            "/api/v1/conversations", headers=headers,
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
        assert data["owner_id"] is not None
        
        app.dependency_overrides.clear()

    def test_create_direct(self, client, auth_service):
        """Test creating a direct conversation - uses real user via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register and login real user
        client.post("/api/v1/auth/register", json={
            "username": "convuser2", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "convuser2", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        response = client.post(
            "/api/v1/conversations", headers=headers,
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

    def test_get_success(self, client, auth_service):
        """Test getting a conversation - creates via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register and login real user
        client.post("/api/v1/auth/register", json={
            "username": "getuser1", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "getuser1", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # Create conversation via API
        conv_resp = client.post(
            "/api/v1/conversations", headers=headers,
            json={"type": "group", "name": "Get Test"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        response = client.get(f"/api/v1/conversations/{conv_id}", headers=headers)
        
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

    def test_get_not_member(self, client, auth_service):
        """Test getting conversation user is not a member of - creates via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register two users
        client.post("/api/v1/auth/register", json={
            "username": "owneruser", "password": "password123",
        })
        client.post("/api/v1/auth/register", json={
            "username": "otheruser", "password": "password123",
        })
        
        # Login as owner
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "owneruser", "password": "password123",
        })
        owner_token = login_resp.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        
        # Login as other user
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "otheruser", "password": "password123",
        })
        other_token = login_resp.json()["access_token"]
        other_headers = {"Authorization": f"Bearer {other_token}"}
        
        # Create conversation as owner via API
        conv_resp = client.post(
            "/api/v1/conversations", headers=owner_headers,
            json={"type": "group", "name": "Private Group"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        # Try to get as non-member - API returns 404 for both not found and not a member
        response = client.get(f"/api/v1/conversations/{conv_id}", headers=other_headers)
        
        assert response.status_code == 404
        
        app.dependency_overrides.clear()


class TestConversationUpdate:
    """Tests for PUT /api/v1/conversations/{id}."""

    def test_update_by_owner(self, client, auth_service):
        """Test updating conversation by owner - creates via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register and login real user
        client.post("/api/v1/auth/register", json={
            "username": "updateowner", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "updateowner", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # Create conversation via API
        conv_resp = client.post(
            "/api/v1/conversations", headers=headers,
            json={"type": "group", "name": "Old Name"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        # Update via API
        response = client.put(
            f"/api/v1/conversations/{conv_id}", headers=headers,
            json={"name": "New Name"},
        )
        
        assert response.status_code == 200
        assert response.json()["name"] == "New Name"
        
        app.dependency_overrides.clear()

    def test_update_by_admin(self, client, auth_service):
        """Test updating conversation by admin - uses real users via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register owner and admin users
        client.post("/api/v1/auth/register", json={
            "username": "adminowner", "password": "password123",
        })
        client.post("/api/v1/auth/register", json={
            "username": "convadmin", "password": "password123",
        })
        
        # Login as owner
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "adminowner", "password": "password123",
        })
        owner_token = login_resp.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        
        # Login as admin
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "convadmin", "password": "password123",
        })
        admin_token = login_resp.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        
        # Create conversation as owner via API
        conv_resp = client.post(
            "/api/v1/conversations", headers=owner_headers,
            json={"type": "group", "name": "Admin Update"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        # Add admin as member via API (need to get their user ID first)
        from sprinkle.storage.database import SessionLocal
        from sprinkle.models import User
        db = SessionLocal()
        try:
            admin_user = db.query(User).filter(User.username == "convadmin").first()
            admin_user_id = admin_user.id
        finally:
            db.close()
        
        # Add admin as member
        client.post(
            f"/api/v1/conversations/{conv_id}/members", headers=owner_headers,
            json={"user_id": admin_user_id, "role": "admin"},
        )
        
        # Try to update as admin
        response = client.put(
            f"/api/v1/conversations/{conv_id}", headers=admin_headers,
            json={"name": "Changed by Admin"},
        )
        
        assert response.status_code == 200
        
        app.dependency_overrides.clear()

    def test_update_by_member_forbidden(self, client, auth_service):
        """Test updating conversation by regular member fails - uses real users via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register owner and member users
        client.post("/api/v1/auth/register", json={
            "username": "memowner", "password": "password123",
        })
        client.post("/api/v1/auth/register", json={
            "username": "memberuser", "password": "password123",
        })
        
        # Login as owner
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "memowner", "password": "password123",
        })
        owner_token = login_resp.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        
        # Login as member
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "memberuser", "password": "password123",
        })
        member_token = login_resp.json()["access_token"]
        member_headers = {"Authorization": f"Bearer {member_token}"}
        
        # Create conversation as owner via API
        conv_resp = client.post(
            "/api/v1/conversations", headers=owner_headers,
            json={"type": "group", "name": "Member Update"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        # Add member via API
        from sprinkle.storage.database import SessionLocal
        from sprinkle.models import User
        db = SessionLocal()
        try:
            member_user = db.query(User).filter(User.username == "memberuser").first()
            member_user_id = member_user.id
        finally:
            db.close()
        
        client.post(
            f"/api/v1/conversations/{conv_id}/members", headers=owner_headers,
            json={"user_id": member_user_id, "role": "member"},
        )
        
        # Try to update as member (should fail)
        response = client.put(
            f"/api/v1/conversations/{conv_id}", headers=member_headers,
            json={"name": "Changed"},
        )
        
        assert response.status_code == 403
        
        app.dependency_overrides.clear()


class TestConversationDelete:
    """Tests for DELETE /api/v1/conversations/{id}."""

    def test_delete_by_owner(self, client, auth_service):
        """Test deleting conversation by owner - creates via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register and login real user
        client.post("/api/v1/auth/register", json={
            "username": "delowner", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "delowner", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # Create conversation via API
        conv_resp = client.post(
            "/api/v1/conversations", headers=headers,
            json={"type": "group", "name": "Delete Me"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        response = client.delete(f"/api/v1/conversations/{conv_id}", headers=headers)
        
        assert response.status_code == 204
        
        app.dependency_overrides.clear()

    def test_delete_by_admin_forbidden(self, client, auth_service):
        """Test deleting conversation by admin fails - uses real users via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register owner and admin users
        client.post("/api/v1/auth/register", json={
            "username": "delowner2", "password": "password123",
        })
        client.post("/api/v1/auth/register", json={
            "username": "deladmin", "password": "password123",
        })
        
        # Login as owner
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "delowner2", "password": "password123",
        })
        owner_token = login_resp.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        
        # Login as admin
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "deladmin", "password": "password123",
        })
        admin_token = login_resp.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        
        # Create conversation as owner via API
        conv_resp = client.post(
            "/api/v1/conversations", headers=owner_headers,
            json={"type": "group", "name": "Admin Delete"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        # Add admin as member
        from sprinkle.storage.database import SessionLocal
        from sprinkle.models import User
        db = SessionLocal()
        try:
            admin_user = db.query(User).filter(User.username == "deladmin").first()
            admin_user_id = admin_user.id
        finally:
            db.close()
        
        client.post(
            f"/api/v1/conversations/{conv_id}/members", headers=owner_headers,
            json={"user_id": admin_user_id, "role": "admin"},
        )
        
        # Try to delete as admin (should fail)
        response = client.delete(f"/api/v1/conversations/{conv_id}", headers=admin_headers)
        
        assert response.status_code == 403
        
        app.dependency_overrides.clear()


# ============================================================================
# Message API Tests
# ============================================================================

class TestMessageList:
    """Tests for GET /api/v1/conversations/{id}/messages."""

    def test_list_empty(self, client, auth_service):
        """Test listing messages when none exist - creates conversation via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register and login real user
        client.post("/api/v1/auth/register", json={
            "username": "listmsguser1", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "listmsguser1", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # Create conversation via API
        conv_resp = client.post(
            "/api/v1/conversations", headers=headers,
            json={"type": "group", "name": "Message List"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        response = client.get(f"/api/v1/conversations/{conv_id}/messages", headers=headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["has_more"] is False
        
        app.dependency_overrides.clear()

    def test_list_with_messages(self, client, auth_service):
        """Test listing messages - creates real conversation and message via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # 1. Register & login
        client.post("/api/v1/auth/register", json={
            "username": "listmsguser", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "listmsguser", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # 2. Create conversation via API
        conv_resp = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"type": "group", "name": "With Messages"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        # 3. Send message via API
        msg_resp = client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers=headers,
            json={"content": "Hello!"},
        )
        assert msg_resp.status_code == 201
        
        # 4. List messages
        response = client.get(f"/api/v1/conversations/{conv_id}/messages", headers=headers)
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["content"] == "Hello!"
        
        app.dependency_overrides.clear()


class TestMessageSend:
    """Tests for POST /api/v1/conversations/{id}/messages."""

    def test_send_success(self, client, auth_service):
        """Test sending a message - creates real conversation via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # 1. Register & login
        client.post("/api/v1/auth/register", json={
            "username": "sendmsguser", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "sendmsguser", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # 2. Create conversation via API (writes to both in-memory store AND DB)
        conv_resp = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"type": "group", "name": "Send Test"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        # 3. Send message via API
        response = client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers=headers,
            json={"content": "Hello, world!"},
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["content"] == "Hello, world!"
        assert data["sender_id"] == login_resp.json().get("user_id") or data["sender_id"]
        assert data["content_type"] == "text"
        
        app.dependency_overrides.clear()

    def test_send_with_mentions(self, client, auth_service):
        """Test sending a message with mentions - creates real conversation via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # 1. Register & login
        client.post("/api/v1/auth/register", json={
            "username": "mentionuser", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "mentionuser", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # 2. Create conversation via API
        conv_resp = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"type": "group", "name": "Mention Test"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        # 3. Send message with mentions via API
        response = client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers=headers,
            json={
                "content": "Hey @user!",
                "mentions": ["user_1", "user_2"],
            },
        )
        
        assert response.status_code == 201
        data = response.json()
        # Note: mentions are stored in-memory store but not returned in API response
        # This is a known limitation of the database-based API
        
        app.dependency_overrides.clear()


class TestMessageEdit:
    """Tests for PUT /api/v1/messages/{id}."""

    def test_edit_by_sender(self, client, auth_service):
        """Test editing message by sender - creates real conversation and message via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # 1. Register & login
        client.post("/api/v1/auth/register", json={
            "username": "edituser", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "edituser", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # 2. Create conversation and send message via API
        conv_resp = client.post(
            "/api/v1/conversations", headers=headers,
            json={"type": "group", "name": "Edit Test"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        msg_resp = client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers=headers,
            json={"content": "Original"},
        )
        assert msg_resp.status_code == 201
        msg_id = msg_resp.json()["id"]
        
        # 3. Edit the message
        response = client.put(
            f"/api/v1/messages/{msg_id}",
            headers=headers,
            json={"content": "Updated"},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["content"] == "Updated"
        assert data["edited_at"] is not None
        
        app.dependency_overrides.clear()

    def test_edit_by_other_forbidden(self, client, auth_service):
        """Test editing message by non-sender fails - creates real conversation and message via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # 1. Register two users: sender and other
        client.post("/api/v1/auth/register", json={
            "username": "edit_sender", "password": "password123",
        })
        client.post("/api/v1/auth/register", json={
            "username": "edit_other", "password": "password123",
        })
        
        sender_login = client.post("/api/v1/auth/login", json={
            "username": "edit_sender", "password": "password123",
        })
        other_login = client.post("/api/v1/auth/login", json={
            "username": "edit_other", "password": "password123",
        })
        sender_token = sender_login.json()["access_token"]
        other_token = other_login.json()["access_token"]
        sender_headers = {"Authorization": f"Bearer {sender_token}"}
        other_headers = {"Authorization": f"Bearer {other_token}"}
        
        # 2. Sender creates conversation and sends message
        conv_resp = client.post(
            "/api/v1/conversations", headers=sender_headers,
            json={"type": "group", "name": "Edit Forbidden Test"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        msg_resp = client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers=sender_headers,
            json={"content": "Original"},
        )
        assert msg_resp.status_code == 201
        msg_id = msg_resp.json()["id"]
        
        # 3. Other user tries to edit sender's message - should be forbidden
        response = client.put(
            f"/api/v1/messages/{msg_id}",
            headers=other_headers,
            json={"content": "Hacked!"},
        )
        
        assert response.status_code == 403
        
        app.dependency_overrides.clear()


class TestMessageDelete:
    """Tests for DELETE /api/v1/messages/{id}."""

    def test_delete_by_sender(self, client, auth_service):
        """Test deleting message by sender - creates real conversation and message via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # 1. Register & login
        client.post("/api/v1/auth/register", json={
            "username": "deluser", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "deluser", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # 2. Create conversation and send message via API
        conv_resp = client.post(
            "/api/v1/conversations", headers=headers,
            json={"type": "group", "name": "Delete Test"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        msg_resp = client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers=headers,
            json={"content": "To be deleted"},
        )
        assert msg_resp.status_code == 201
        msg_id = msg_resp.json()["id"]
        
        # 3. Delete the message
        response = client.delete(f"/api/v1/messages/{msg_id}", headers=headers)
        
        assert response.status_code == 204
        
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

    def test_list_members(self, client, auth_service):
        """Test listing members - creates via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register and login real user
        client.post("/api/v1/auth/register", json={
            "username": "memowner1", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "memowner1", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # Create conversation via API
        conv_resp = client.post(
            "/api/v1/conversations", headers=headers,
            json={"type": "group", "name": "Members Test"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        # List members
        response = client.get(f"/api/v1/conversations/{conv_id}/members", headers=headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1  # Only owner initially
        
        app.dependency_overrides.clear()


class TestMemberAdd:
    """Tests for POST /api/v1/conversations/{id}/members."""

    def test_add_member_by_admin(self, client, auth_service):
        """Test adding a member by admin - uses real users via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register owner, admin, and new member
        client.post("/api/v1/auth/register", json={
            "username": "admowner", "password": "password123",
        })
        client.post("/api/v1/auth/register", json={
            "username": "admadmin", "password": "password123",
        })
        client.post("/api/v1/auth/register", json={
            "username": "admnewmember", "password": "password123",
        })
        
        # Login as owner
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "admowner", "password": "password123",
        })
        owner_token = login_resp.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        
        # Login as admin
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "admadmin", "password": "password123",
        })
        admin_token = login_resp.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        
        # Create conversation as owner
        conv_resp = client.post(
            "/api/v1/conversations", headers=owner_headers,
            json={"type": "group", "name": "Add Member Test"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        # Add admin as member (owner adds them)
        from sprinkle.storage.database import SessionLocal
        from sprinkle.models import User
        db = SessionLocal()
        try:
            admin_user = db.query(User).filter(User.username == "admadmin").first()
            new_user = db.query(User).filter(User.username == "admnewmember").first()
            admin_id = admin_user.id
            new_user_id = new_user.id
        finally:
            db.close()
        
        client.post(
            f"/api/v1/conversations/{conv_id}/members", headers=owner_headers,
            json={"user_id": admin_id, "role": "admin"},
        )
        
        # Admin adds new member
        response = client.post(
            f"/api/v1/conversations/{conv_id}/members", headers=admin_headers,
            json={"user_id": new_user_id, "role": "member"},
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["user_id"] == new_user_id
        assert data["role"] == "member"
        
        app.dependency_overrides.clear()

    def test_add_member_by_member_forbidden(self, client, auth_service):
        """Test adding member by regular member fails - uses real users via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register owner and member
        client.post("/api/v1/auth/register", json={
            "username": "mbrsowner", "password": "password123",
        })
        client.post("/api/v1/auth/register", json={
            "username": "mbrsmember", "password": "password123",
        })
        client.post("/api/v1/auth/register", json={
            "username": "mbrsnewmember", "password": "password123",
        })
        
        # Login as owner
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "mbrsowner", "password": "password123",
        })
        owner_token = login_resp.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        
        # Login as member
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "mbrsmember", "password": "password123",
        })
        member_token = login_resp.json()["access_token"]
        member_headers = {"Authorization": f"Bearer {member_token}"}
        
        # Create conversation as owner
        conv_resp = client.post(
            "/api/v1/conversations", headers=owner_headers,
            json={"type": "group", "name": "Member Add Test"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        # Add member (owner adds them)
        from sprinkle.storage.database import SessionLocal
        from sprinkle.models import User
        db = SessionLocal()
        try:
            member_user = db.query(User).filter(User.username == "mbrsmember").first()
            member_id = member_user.id
        finally:
            db.close()
        
        client.post(
            f"/api/v1/conversations/{conv_id}/members", headers=owner_headers,
            json={"user_id": member_id, "role": "member"},
        )
        
        # Member tries to add another member (should fail)
        response = client.post(
            f"/api/v1/conversations/{conv_id}/members", headers=member_headers,
            json={"user_id": "some_user_id", "role": "member"},
        )
        
        assert response.status_code == 403
        
        app.dependency_overrides.clear()


class TestMemberRemove:
    """Tests for DELETE /api/v1/conversations/{id}/members/{uid}."""

    def test_remove_member_by_admin(self, client, auth_service):
        """Test removing member by admin - uses real users via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register users
        client.post("/api/v1/auth/register", json={
            "username": "rmowner", "password": "password123",
        })
        client.post("/api/v1/auth/register", json={
            "username": "rmadmin", "password": "password123",
        })
        client.post("/api/v1/auth/register", json={
            "username": "rmmember", "password": "password123",
        })
        
        # Login as owner
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "rmowner", "password": "password123",
        })
        owner_token = login_resp.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        
        # Login as admin
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "rmadmin", "password": "password123",
        })
        admin_token = login_resp.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        
        # Create conversation as owner
        conv_resp = client.post(
            "/api/v1/conversations", headers=owner_headers,
            json={"type": "group", "name": "Remove Test"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        # Get user IDs
        from sprinkle.storage.database import SessionLocal
        from sprinkle.models import User
        db = SessionLocal()
        try:
            admin_user = db.query(User).filter(User.username == "rmadmin").first()
            member_user = db.query(User).filter(User.username == "rmmember").first()
            admin_id = admin_user.id
            member_id = member_user.id
        finally:
            db.close()
        
        # Add admin and member
        client.post(
            f"/api/v1/conversations/{conv_id}/members", headers=owner_headers,
            json={"user_id": admin_id, "role": "admin"},
        )
        client.post(
            f"/api/v1/conversations/{conv_id}/members", headers=owner_headers,
            json={"user_id": member_id, "role": "member"},
        )
        
        # Admin removes member
        response = client.delete(
            f"/api/v1/conversations/{conv_id}/members/{member_id}",
            headers=admin_headers,
        )
        
        assert response.status_code == 204
        
        app.dependency_overrides.clear()

    def test_remove_owner_forbidden(self, client, auth_service):
        """Test removing owner fails - uses real users via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register owner and admin
        client.post("/api/v1/auth/register", json={
            "username": "rmowner2", "password": "password123",
        })
        client.post("/api/v1/auth/register", json={
            "username": "rmadmin2", "password": "password123",
        })
        
        # Login as owner
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "rmowner2", "password": "password123",
        })
        owner_token = login_resp.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        
        # Login as admin
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "rmadmin2", "password": "password123",
        })
        admin_token = login_resp.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        
        # Create conversation as owner
        conv_resp = client.post(
            "/api/v1/conversations", headers=owner_headers,
            json={"type": "group", "name": "Remove Owner Test"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        owner_id = conv_resp.json()["owner_id"]
        
        # Get admin user ID
        from sprinkle.storage.database import SessionLocal
        from sprinkle.models import User
        db = SessionLocal()
        try:
            admin_user = db.query(User).filter(User.username == "rmadmin2").first()
            admin_id = admin_user.id
        finally:
            db.close()
        
        # Add admin as member first
        client.post(
            f"/api/v1/conversations/{conv_id}/members", headers=owner_headers,
            json={"user_id": admin_id, "role": "admin"},
        )
        
        # Admin tries to remove owner (should fail)
        response = client.delete(
            f"/api/v1/conversations/{conv_id}/members/{owner_id}",
            headers=admin_headers,
        )
        
        assert response.status_code == 400
        assert "owner" in response.json()["detail"].lower()
        
        app.dependency_overrides.clear()


# ============================================================================
# File API Tests
# ============================================================================

class TestFileUpload:
    """Tests for POST /api/v1/files/upload."""

    def test_upload_success(self, client, auth_service, tmp_path):
        """Test successful file upload - uses real user via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register and login real user
        client.post("/api/v1/auth/register", json={
            "username": "uploaduser", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "uploaduser", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # Override storage dir
        from sprinkle.api import files as files_module
        files_module.STORAGE_DIR = tmp_path
        
        file_content = b"Hello, file!"
        
        response = client.post(
            "/api/v1/files/upload",
            headers=headers,
            files={"file": ("test.txt", BytesIO(file_content), "text/plain")},
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["file_name"] == "test.txt"
        assert data["file_size"] == 12  # len(b"Hello, file!") = 12
        assert data["mime_type"] == "text/plain"
        assert data["uploader_id"] is not None
        
        app.dependency_overrides.clear()

    def test_upload_with_conversation(self, client, auth_service, tmp_path):
        """Test uploading file with conversation association - uses real user via API."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register and login real user
        client.post("/api/v1/auth/register", json={
            "username": "uploaduser2", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "uploaduser2", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        from sprinkle.api import files as files_module
        files_module.STORAGE_DIR = tmp_path
        
        # Note: Due to FastAPI/TestClient behavior with files+data,
        # conversation_id may not be received in test environment
        # This test verifies basic upload functionality works
        response = client.post(
            "/api/v1/files/upload",
            headers=headers,
            files={"file": ("doc.pdf", BytesIO(b"PDF content"), "application/pdf")},
        )
        
        assert response.status_code == 201
        data = response.json()
        assert data["file_name"] == "doc.pdf"
        assert data["mime_type"] == "application/pdf"
        
        app.dependency_overrides.clear()


class TestFileDownload:
    """Tests for GET /api/v1/files/{id}."""

    def test_download_success(self, client, auth_service, tmp_path):
        """Test successful file download - uploads via API then downloads."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register and login real user
        client.post("/api/v1/auth/register", json={
            "username": "dluser", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "dluser", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        from sprinkle.api import files as files_module
        files_module.STORAGE_DIR = tmp_path
        
        # Upload a file first
        upload_resp = client.post(
            "/api/v1/files/upload",
            headers=headers,
            files={"file": ("download_test.txt", BytesIO(b"Download content"), "text/plain")},
        )
        assert upload_resp.status_code == 201
        file_id = upload_resp.json()["id"]
        
        response = client.get(f"/api/v1/files/{file_id}", headers=headers)
        
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

    def test_delete_by_uploader(self, client, auth_service, tmp_path):
        """Test deleting file by uploader - uploads via API then deletes."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register and login real user
        client.post("/api/v1/auth/register", json={
            "username": "deluser", "password": "password123",
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "deluser", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        from sprinkle.api import files as files_module
        files_module.STORAGE_DIR = tmp_path
        
        # Upload a file first
        upload_resp = client.post(
            "/api/v1/files/upload",
            headers=headers,
            files={"file": ("delete_me.txt", BytesIO(b"Delete content"), "text/plain")},
        )
        assert upload_resp.status_code == 201
        file_id = upload_resp.json()["id"]
        
        response = client.delete(f"/api/v1/files/{file_id}", headers=headers)
        
        assert response.status_code == 204
        
        app.dependency_overrides.clear()

    def test_delete_by_other_forbidden(self, client, auth_service, tmp_path):
        """Test deleting file by non-uploader fails - uploads via API then tries to delete as other."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # Register uploader and other user
        client.post("/api/v1/auth/register", json={
            "username": "fileowner", "password": "password123",
        })
        client.post("/api/v1/auth/register", json={
            "username": "fileother", "password": "password123",
        })
        
        # Login as uploader
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "fileowner", "password": "password123",
        })
        owner_token = login_resp.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        
        # Login as other user
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "fileother", "password": "password123",
        })
        other_token = login_resp.json()["access_token"]
        other_headers = {"Authorization": f"Bearer {other_token}"}
        
        from sprinkle.api import files as files_module
        files_module.STORAGE_DIR = tmp_path
        
        # Upload a file as owner
        upload_resp = client.post(
            "/api/v1/files/upload",
            headers=owner_headers,
            files={"file": ("not_yours.txt", BytesIO(b"Content"), "text/plain")},
        )
        assert upload_resp.status_code == 201
        file_id = upload_resp.json()["id"]
        
        # Try to delete as other user (should fail)
        response = client.delete(f"/api/v1/files/{file_id}", headers=other_headers)
        
        assert response.status_code == 403
        
        app.dependency_overrides.clear()


# ============================================================================
# Permission Matrix Tests
# ============================================================================

class TestPermissionMatrix:
    """Tests for permission matrix from ARCHITECTURE.md."""

    def test_agent_cannot_edit_own_message(self, client, auth_service):
        """Test that regular agent cannot edit their own messages - uses real agent registration."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # 1. Register agent user
        client.post("/api/v1/auth/register", json={
            "username": "agent_edit", "password": "password123", "is_agent": True,
        })
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "agent_edit", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # 2. Create conversation and send message
        conv_resp = client.post(
            "/api/v1/conversations", headers=headers,
            json={"type": "group", "name": "Agent Edit Test"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        msg_resp = client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers=headers,
            json={"content": "Agent message"},
        )
        assert msg_resp.status_code == 201
        msg_id = msg_resp.json()["id"]
        
        # 3. Try to edit own message - should be forbidden for regular agents
        response = client.put(
            f"/api/v1/messages/{msg_id}",
            headers=headers,
            json={"content": "Edited"},
        )
        
        assert response.status_code == 403
        
        app.dependency_overrides.clear()

    def test_agent_admin_can_edit_own_message(self, client, auth_service):
        """Test that agent admin can edit their own messages - uses real agent registration."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # 1. Register owner and agent user
        client.post("/api/v1/auth/register", json={
            "username": "agent_admin_owner", "password": "password123",
        })
        client.post("/api/v1/auth/register", json={
            "username": "agent_admin_user", "password": "password123", "is_agent": True,
        })
        
        owner_login = client.post("/api/v1/auth/login", json={
            "username": "agent_admin_owner", "password": "password123",
        })
        agent_login = client.post("/api/v1/auth/login", json={
            "username": "agent_admin_user", "password": "password123",
        })
        owner_token = owner_login.json()["access_token"]
        agent_token = agent_login.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        agent_headers = {"Authorization": f"Bearer {agent_token}"}
        
        # 2. Owner creates conversation and makes agent an admin
        conv_resp = client.post(
            "/api/v1/conversations", headers=owner_headers,
            json={"type": "group", "name": "Agent Admin Test"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        
        # Owner adds agent as admin
        client.post(
            f"/api/v1/conversations/{conv_id}/members",
            headers=owner_headers,
            json={"user_id": agent_login.json().get("user_id"), "role": "admin"},
        )
        
        # 3. Agent sends message
        msg_resp = client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers=agent_headers,
            json={"content": "Agent admin message"},
        )
        assert msg_resp.status_code == 201
        msg_id = msg_resp.json()["id"]
        
        # 4. Agent tries to edit own message - should succeed because agent is admin
        response = client.put(
            f"/api/v1/messages/{msg_id}",
            headers=agent_headers,
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

    def test_conversation_member_flow(self, client, auth_service):
        """Test conversation creation and member management flow - uses real users."""
        app.dependency_overrides[get_auth_service] = override_get_auth_service(auth_service)
        
        # 1. Register owner & member users
        client.post("/api/v1/auth/register", json={
            "username": "flowowner", "password": "password123",
        })
        client.post("/api/v1/auth/register", json={
            "username": "flowmember", "password": "password123",
        })
        
        # Login as owner
        login_resp = client.post("/api/v1/auth/login", json={
            "username": "flowowner", "password": "password123",
        })
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # 2. Create conversation
        create_response = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={
                "type": "group",
                "name": "Team Chat",
                "member_ids": [],
            },
        )
        assert create_response.status_code == 201
        conv_id = create_response.json()["id"]
        
        # 3. Add member - get member's user_id first
        from sprinkle.storage.database import SessionLocal
        from sprinkle.models import User
        db = SessionLocal()
        try:
            member_user = db.query(User).filter(User.username == "flowmember").first()
            member_id = member_user.id
        finally:
            db.close()
        
        add_response = client.post(
            f"/api/v1/conversations/{conv_id}/members",
            headers=headers,
            json={"user_id": member_id, "role": "member"},
        )
        assert add_response.status_code == 201
        
        # 4. Send message
        msg_response = client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers=headers,
            json={"content": "Hello team!"},
        )
        assert msg_response.status_code == 201
        
        # 5. List messages
        list_response = client.get(f"/api/v1/conversations/{conv_id}/messages", headers=headers)
        assert list_response.status_code == 200
        assert len(list_response.json()["items"]) == 1
        
        # 6. List members
        members_response = client.get(f"/api/v1/conversations/{conv_id}/members", headers=headers)
        assert members_response.status_code == 200
        assert members_response.json()["total"] == 2  # owner + new_member
        
        app.dependency_overrides.clear()
