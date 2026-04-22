"""Permission System - Role-based access control for Sprinkle."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

from sqlalchemy import select

from sprinkle.kernel.auth import UserCredentials

logger = logging.getLogger(__name__)


# ============================================================================
# Enums
# ============================================================================

class Role(str, Enum):
    """User roles in a conversation."""
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class Action(str, Enum):
    """Actions that can be performed in a conversation."""
    # Message actions
    SEND_MESSAGE = "send_message"
    EDIT_OWN_MESSAGE = "edit_own_message"
    DELETE_OWN_MESSAGE = "delete_own_message"
    DELETE_ANY_MESSAGE = "delete_any_message"
    
    # Conversation actions
    VIEW_CONVERSATION = "view_conversation"
    EDIT_CONVERSATION = "edit_conversation"
    ADD_MEMBER = "add_member"
    REMOVE_MEMBER = "remove_member"
    SET_ADMIN = "set_admin"
    DELETE_CONVERSATION = "delete_conversation"
    TRANSFER_OWNERSHIP = "transfer_ownership"


# ============================================================================
# Permission Matrix
# ============================================================================

# Default permissions for each role
# Owner has all permissions
# Admin has all permissions except set_admin, delete_conversation, transfer_ownership
# Member has basic permissions based on user type (human vs agent)
# Agent (non-admin) has limited permissions

OWNER_PERMISSIONS: Set[Action] = {
    Action.SEND_MESSAGE,
    Action.EDIT_OWN_MESSAGE,
    Action.DELETE_OWN_MESSAGE,
    Action.DELETE_ANY_MESSAGE,
    Action.VIEW_CONVERSATION,
    Action.EDIT_CONVERSATION,
    Action.ADD_MEMBER,
    Action.REMOVE_MEMBER,
    Action.SET_ADMIN,
    Action.DELETE_CONVERSATION,
    Action.TRANSFER_OWNERSHIP,
}

ADMIN_PERMISSIONS: Set[Action] = {
    Action.SEND_MESSAGE,
    Action.EDIT_OWN_MESSAGE,
    Action.DELETE_OWN_MESSAGE,
    Action.DELETE_ANY_MESSAGE,
    Action.VIEW_CONVERSATION,
    Action.EDIT_CONVERSATION,
    Action.ADD_MEMBER,
    Action.REMOVE_MEMBER,
    # Note: SET_ADMIN, DELETE_CONVERSATION, TRANSFER_OWNERSHIP are NOT included
}

# Human member permissions (non-admin)
HUMAN_MEMBER_PERMISSIONS: Set[Action] = {
    Action.SEND_MESSAGE,
    Action.EDIT_OWN_MESSAGE,
    Action.DELETE_OWN_MESSAGE,
    Action.VIEW_CONVERSATION,
}

# Agent member permissions (non-admin) - limited to sending messages only
AGENT_MEMBER_PERMISSIONS: Set[Action] = {
    Action.SEND_MESSAGE,
    Action.VIEW_CONVERSATION,
}


# ============================================================================
# Permission Matrix Lookup
# ============================================================================

def get_permissions_for_role(
    role: Role,
    is_agent: bool = False,
) -> Set[Action]:
    """Get the set of permissions for a given role.
    
    Args:
        role: The user's role in the conversation
        is_agent: Whether the user is an agent (affects member permissions)
    
    Returns:
        Set of allowed actions
    """
    if role == Role.OWNER:
        return OWNER_PERMISSIONS
    
    if role == Role.ADMIN:
        return ADMIN_PERMISSIONS
    
    # Member role
    if is_agent:
        return AGENT_MEMBER_PERMISSIONS
    else:
        return HUMAN_MEMBER_PERMISSIONS


# ============================================================================
# Permission Check Result
# ============================================================================

@dataclass
class PermissionCheckResult:
    """Result of a permission check."""
    allowed: bool
    role: Optional[Role] = None
    reason: Optional[str] = None


# ============================================================================
# Member Info Cache
# ============================================================================

@dataclass
class MemberInfo:
    """Cached member information."""
    conversation_id: str
    user_id: str
    role: Role
    is_agent: bool
    is_active: bool = True


# ============================================================================
# Permission Service
# ============================================================================

class PermissionService:
    """Permission service for conversation-level access control.
    
    This service manages role-based permissions within conversations.
    It provides methods to check if a user has permission to perform
    specific actions based on their role.
    
    Example:
        permission_service = PermissionService()
        
        # Check if user can delete a message
        result = await permission_service.check_permission(
            user_id="user_123",
            conversation_id="conv_456",
            action=Action.DELETE_ANY_MESSAGE,
        )
        
        if result.allowed:
            print("Permission granted")
        else:
            print(f"Permission denied: {result.reason}")
    """
    
    def __init__(self):
        """Initialize the permission service."""
        # In-memory cache for member info
        # In production, this would be backed by the database
        self._member_cache: Dict[tuple[str, str], MemberInfo] = {}
        
        # In-memory cache for user agent status
        # Maps user_id -> is_agent
        self._agent_cache: Dict[str, bool] = {}
    
    # ------------------------------------------------------------------------
    # Cache Management
    # ------------------------------------------------------------------------
    
    def set_member_role(
        self,
        conversation_id: str,
        user_id: str,
        role: Role,
        is_agent: bool = False,
        is_active: bool = True,
    ) -> None:
        """Set a member's role in a conversation (for cache updates)."""
        key = (conversation_id, user_id)
        self._member_cache[key] = MemberInfo(
            conversation_id=conversation_id,
            user_id=user_id,
            role=role,
            is_agent=is_agent,
            is_active=is_active,
        )
    
    def remove_member(
        self,
        conversation_id: str,
        user_id: str,
    ) -> None:
        """Remove a member from the cache."""
        key = (conversation_id, user_id)
        self._member_cache.pop(key, None)
    
    def set_user_is_agent(self, user_id: str, is_agent: bool) -> None:
        """Set whether a user is an agent."""
        self._agent_cache[user_id] = is_agent
    
    def is_user_agent(self, user_id: str) -> bool:
        """Check if a user is an agent (from cache or database)."""
        # Check cache first
        if user_id in self._agent_cache:
            return self._agent_cache.get(user_id, False)
        
        # Fetch from database
        from sprinkle.storage.database import SessionLocal
        from sprinkle.models import User
        
        db = SessionLocal()
        try:
            user = db.execute(
                select(User).where(User.id == user_id)
            ).scalar_one_or_none()
            
            if not user:
                return False
            
            is_agent = user.user_type.value == "agent"
            self._agent_cache[user_id] = is_agent
            return is_agent
        finally:
            db.close()
    
    def get_member_info(
        self,
        conversation_id: str,
        user_id: str,
    ) -> Optional[MemberInfo]:
        """Get member info from cache or database.
        
        If not in cache, fetches from database and caches the result.
        """
        key = (conversation_id, user_id)
        
        # Check cache first
        if key in self._member_cache:
            return self._member_cache.get(key)
        
        # Fetch from database
        member_info = self._fetch_member_from_db(conversation_id, user_id)
        
        if member_info:
            self._member_cache[key] = member_info
        
        return member_info
    
    def _fetch_member_from_db(
        self,
        conversation_id: str,
        user_id: str,
    ) -> Optional[MemberInfo]:
        """Fetch member info from database."""
        from sprinkle.storage.database import SessionLocal
        from sprinkle.models import ConversationMember, Conversation, User
        
        db = SessionLocal()
        try:
            # Get member record
            member = db.execute(
                select(ConversationMember).where(
                    ConversationMember.conversation_id == conversation_id,
                    ConversationMember.user_id == user_id,
                )
            ).scalar_one_or_none()
            
            if not member:
                return None
            
            # Get user to check if agent
            user = db.execute(
                select(User).where(User.id == user_id)
            ).scalar_one_or_none()
            
            is_agent = user.user_type.value == "agent" if user else False
            
            return MemberInfo(
                conversation_id=conversation_id,
                user_id=user_id,
                role=Role(member.role.value),
                is_agent=is_agent,
                is_active=member.is_active,
            )
        finally:
            db.close()
    
    def set_member_info(self, info: MemberInfo) -> None:
        """Set cached member info."""
        key = (info.conversation_id, info.user_id)
        self._member_cache[key] = info
    
    def clear_cache(self) -> None:
        """Clear all caches (for testing)."""
        self._member_cache.clear()
        self._agent_cache.clear()
    
    # ------------------------------------------------------------------------
    # Permission Checking
    # ------------------------------------------------------------------------
    
    async def check_permission(
        self,
        user_id: str,
        conversation_id: str,
        action: Action,
    ) -> PermissionCheckResult:
        """Check if a user has permission to perform an action.
        
        Args:
            user_id: The user attempting the action
            conversation_id: The conversation context
            action: The action being attempted
        
        Returns:
            PermissionCheckResult with allowed status and reason
        """
        member_info = self.get_member_info(conversation_id, user_id)
        
        if member_info is None:
            return PermissionCheckResult(
                allowed=False,
                reason="User is not a member of this conversation",
            )
        
        if not member_info.is_active:
            return PermissionCheckResult(
                allowed=False,
                reason="User is not an active member",
            )
        
        # Get permissions for this role and user type
        permissions = get_permissions_for_role(
            role=member_info.role,
            is_agent=member_info.is_agent,
        )
        
        if action in permissions:
            return PermissionCheckResult(
                allowed=True,
                role=member_info.role,
            )
        
        # Generate appropriate denial reason
        reason = self._get_denial_reason(member_info.role, action, member_info.is_agent)
        
        return PermissionCheckResult(
            allowed=False,
            role=member_info.role,
            reason=reason,
        )
    
    def _get_denial_reason(
        self,
        role: Role,
        action: Action,
        is_agent: bool,
    ) -> str:
        """Generate a human-readable denial reason."""
        if action == Action.SET_ADMIN:
            return "Only the owner can set administrators"
        
        if action == Action.DELETE_CONVERSATION:
            return "Only the owner can delete the conversation"
        
        if action == Action.TRANSFER_OWNERSHIP:
            return "Only the owner can transfer ownership"
        
        if action == Action.REMOVE_MEMBER:
            if role == Role.MEMBER:
                return "Only admins and owners can remove members"
            return "Cannot remove the owner from the conversation"
        
        if action in (Action.EDIT_OWN_MESSAGE, Action.DELETE_OWN_MESSAGE):
            if is_agent and role == Role.MEMBER:
                return "Agents cannot edit or delete their own messages"
            return "You can only edit/delete your own messages"
        
        if action == Action.DELETE_ANY_MESSAGE:
            return "Only admins and owners can delete other users' messages"
        
        if action == Action.EDIT_CONVERSATION:
            return "Only admins and owners can edit conversation details"
        
        if action == Action.ADD_MEMBER:
            return "Only admins and owners can add members"
        
        return f"Action {action.value} is not permitted for role {role.value}"
    
    async def get_user_role(
        self,
        user_id: str,
        conversation_id: str,
    ) -> Optional[Role]:
        """Get a user's role in a conversation.
        
        Args:
            user_id: The user ID
            conversation_id: The conversation ID
        
        Returns:
            Role if user is a member, None otherwise
        """
        member_info = self.get_member_info(conversation_id, user_id)
        
        if member_info is None:
            return None
        
        return member_info.role
    
    async def is_agent_admin(
        self,
        user_id: str,
        conversation_id: str,
    ) -> bool:
        """Check if an agent user has admin privileges.
        
        An agent is an admin if:
        1. Their role in the conversation is ADMIN
        2. They are marked as an agent in the system
        
        Args:
            user_id: The agent user ID
            conversation_id: The conversation ID
        
        Returns:
            True if the agent has admin privileges
        """
        member_info = self.get_member_info(conversation_id, user_id)
        
        if member_info is None:
            return False
        
        # Must be an agent and have admin role
        return member_info.is_agent and member_info.role == Role.ADMIN
    
    async def get_member_permissions(
        self,
        user_id: str,
        conversation_id: str,
    ) -> List[Action]:
        """Get all permissions a user has in a conversation.
        
        Args:
            user_id: The user ID
            conversation_id: The conversation ID
        
        Returns:
            List of allowed actions
        """
        member_info = self.get_member_info(conversation_id, user_id)
        
        if member_info is None:
            return []
        
        permissions = get_permissions_for_role(
            role=member_info.role,
            is_agent=member_info.is_agent,
        )
        
        return list(permissions)
    
    # ------------------------------------------------------------------------
    # Bulk Permission Checking
    # ------------------------------------------------------------------------
    
    async def filter_users_by_permission(
        self,
        user_ids: List[str],
        conversation_id: str,
        action: Action,
    ) -> List[str]:
        """Filter a list of users to those who have permission.
        
        Args:
            user_ids: List of user IDs to check
            conversation_id: The conversation context
            action: The action to check
        
        Returns:
            List of user IDs that have permission
        """
        allowed = []
        
        for user_id in user_ids:
            result = await self.check_permission(
                user_id=user_id,
                conversation_id=conversation_id,
                action=action,
            )
            if result.allowed:
                allowed.append(user_id)
        
        return allowed


# ============================================================================
# Permission Decorator
# ============================================================================

def require_permission(action: Action):
    """Decorator to require a specific permission for an API endpoint.
    
    Usage:
        @router.post("/conversations/{conv_id}/members")
        @require_permission(Action.ADD_MEMBER)
        async def add_member(...):
            ...
    """
    def decorator(func):
        func._required_permission = action
        return func
    return decorator


# ============================================================================
# Global Instance
# ============================================================================

_permission_service: Optional[PermissionService] = None


def get_permission_service() -> PermissionService:
    """Get the global permission service instance."""
    global _permission_service
    if _permission_service is None:
        _permission_service = PermissionService()
    return _permission_service


def set_permission_service(service: PermissionService) -> None:
    """Set the global permission service instance (for testing)."""
    global _permission_service
    _permission_service = service


__all__ = [
    "Role",
    "Action",
    "PermissionService",
    "PermissionCheckResult",
    "MemberInfo",
    "get_permission_for_role",
    "get_permissions_for_role",
    "OWNER_PERMISSIONS",
    "ADMIN_PERMISSIONS",
    "HUMAN_MEMBER_PERMISSIONS",
    "AGENT_MEMBER_PERMISSIONS",
    "require_permission",
    "get_permission_service",
    "set_permission_service",
]
