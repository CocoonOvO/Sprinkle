"""Conversation Service - business logic for conversation management."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sprinkle.kernel.permission import (
    Action,
    MemberInfo,
    PermissionCheckResult,
    PermissionService,
    Role,
)
from sprinkle.kernel.auth import UserCredentials
from sprinkle.plugins.events import PluginEventBus
from sprinkle.storage.layered import (
    ConversationRecord,
    MemberRecord,
    LayeredStorageService,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Events
# ============================================================================

EVENT_CONVERSATION_CREATED = "conversation.created"
EVENT_CONVERSATION_UPDATED = "conversation.updated"
EVENT_CONVERSATION_DELETED = "conversation.deleted"
EVENT_MEMBER_JOINED = "member.joined"
EVENT_MEMBER_LEFT = "member.left"
EVENT_MEMBER_ROLE_CHANGED = "member.role_changed"


# ============================================================================
# Exceptions
# ============================================================================

class ConversationError(Exception):
    """Base exception for conversation errors."""
    pass


class ConversationNotFoundError(ConversationError):
    """Conversation not found."""
    pass


class MemberNotFoundError(ConversationError):
    """Member not found."""
    pass


class PermissionDeniedError(ConversationError):
    """Permission denied."""
    pass


class InvalidOperationError(ConversationError):
    """Invalid operation."""
    pass


# ============================================================================
# Conversation Service
# ============================================================================

class ConversationService:
    """Conversation service for managing conversations and members.
    
    This service implements the business logic for conversation operations
    including creation, member management, and role changes.
    
    Example:
        service = ConversationService(
            storage=storage,
            permission=permission_service,
            event_bus=event_bus,
        )
        
        # Create a group conversation
        conv = await service.create_conversation(
            creator_id="user_123",
            type="group",
            name="My Group",
            member_ids=["user_456", "user_789"],
        )
        
        # Invite a member
        member = await service.invite_member(
            conversation_id=conv.id,
            inviter_id="user_123",
            user_id="user_999",
        )
    """
    
    def __init__(
        self,
        storage: LayeredStorageService,
        permission_service: PermissionService,
        event_bus: Optional[PluginEventBus] = None,
    ):
        """Initialize the conversation service.
        
        Args:
            storage: The layered storage service
            permission_service: The permission service
            event_bus: Optional event bus for publishing events
        """
        self._storage = storage
        self._permission = permission_service
        self._event_bus = event_bus
    
    # =========================================================================
    # Conversation CRUD
    # =========================================================================
    
    async def create_conversation(
        self,
        creator_id: str,
        type: str,  # "direct" | "group"
        name: Optional[str] = None,
        member_ids: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConversationRecord:
        """Create a new conversation.
        
        Args:
            creator_id: The user ID creating the conversation
            type: Conversation type ("direct" or "group")
            name: Conversation name (required for group type)
            member_ids: Initial member user IDs (creator is automatically added)
            metadata: Optional metadata
        
        Returns:
            The created ConversationRecord
        
        Raises:
            InvalidOperationError: If group conversation has no name
        """
        # Validate
        if type == "group" and not name:
            raise InvalidOperationError("Group conversations require a name")
        
        # Create conversation
        now = datetime.now(timezone.utc)
        conversation_id = str(uuid4())
        
        conversation = ConversationRecord(
            id=conversation_id,
            type=type,
            name=name or f"Direct with {creator_id}",
            owner_id=creator_id,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        
        # Save conversation
        await self._storage.save_conversation(conversation)
        
        # Add creator as owner
        creator_member = MemberRecord(
            conversation_id=conversation_id,
            user_id=creator_id,
            role="owner",
            joined_at=now,
            is_active=True,
        )
        await self._storage.add_member(creator_member)
        
        # Update permission cache
        self._permission.set_member_info(MemberInfo(
            conversation_id=conversation_id,
            user_id=creator_id,
            role=Role.OWNER,
            is_agent=False,  # Will be updated if creator is an agent
            is_active=True,
        ))
        
        # Add other members
        if member_ids:
            for member_id in member_ids:
                if member_id != creator_id:
                    member = MemberRecord(
                        conversation_id=conversation_id,
                        user_id=member_id,
                        role="member",
                        joined_at=now,
                        is_active=True,
                    )
                    await self._storage.add_member(member)
                    
                    # Update permission cache
                    is_agent = self._permission.is_user_agent(member_id)
                    self._permission.set_member_info(MemberInfo(
                        conversation_id=conversation_id,
                        user_id=member_id,
                        role=Role.MEMBER,
                        is_agent=is_agent,
                        is_active=True,
                    ))
        
        # Publish event
        await self._publish_event(
            EVENT_CONVERSATION_CREATED,
            conversation.to_dict(),
        )
        
        logger.info(f"Created conversation {conversation_id} by user {creator_id}")
        
        return conversation
    
    async def get_conversation(
        self,
        conversation_id: str,
        requester_id: str,
    ) -> Optional[ConversationRecord]:
        """Get a conversation by ID.
        
        Args:
            conversation_id: The conversation ID
            requester_id: The user requesting the conversation
        
        Returns:
            ConversationRecord if found and requester is a member
        
        Raises:
            PermissionDeniedError: If requester is not a member
        """
        # Check permission
        result = await self._permission.check_permission(
            user_id=requester_id,
            conversation_id=conversation_id,
            action=Action.VIEW_CONVERSATION,
        )
        
        if not result.allowed:
            raise PermissionDeniedError(result.reason or "Access denied")
        
        conversation = await self._storage.get_conversation(conversation_id)
        
        if conversation is None:
            raise ConversationNotFoundError(f"Conversation {conversation_id} not found")
        
        return conversation
    
    async def update_conversation(
        self,
        conversation_id: str,
        updater_id: str,
        name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConversationRecord:
        """Update a conversation's details.
        
        Args:
            conversation_id: The conversation ID
            updater_id: The user making the update
            name: New conversation name (optional)
            metadata: Updated metadata (optional, merged)
        
        Returns:
            Updated ConversationRecord
        
        Raises:
            PermissionDeniedError: If user doesn't have edit permission
        """
        # Check permission
        result = await self._permission.check_permission(
            user_id=updater_id,
            conversation_id=conversation_id,
            action=Action.EDIT_CONVERSATION,
        )
        
        if not result.allowed:
            raise PermissionDeniedError(result.reason or "Permission denied")
        
        conversation = await self._storage.get_conversation(conversation_id)
        
        if conversation is None:
            raise ConversationNotFoundError(f"Conversation {conversation_id} not found")
        
        # Update fields
        if name is not None:
            conversation.name = name
        
        if metadata is not None:
            conversation.metadata.update(metadata)
        
        conversation.updated_at = datetime.now(timezone.utc)
        
        # Save
        await self._storage.save_conversation(conversation)
        
        # Publish event
        await self._publish_event(
            EVENT_CONVERSATION_UPDATED,
            {
                "conversation_id": conversation_id,
                "update_type": "details",
                "updated_by": updater_id,
                "name": name,
                "metadata": metadata,
            },
        )
        
        logger.info(f"Updated conversation {conversation_id} by user {updater_id}")
        
        return conversation
    
    async def delete_conversation(
        self,
        conversation_id: str,
        deleter_id: str,
    ) -> None:
        """Delete a conversation.
        
        Only the owner can delete a conversation.
        
        Args:
            conversation_id: The conversation ID
            deleter_id: The user deleting the conversation
        
        Raises:
            PermissionDeniedError: If user is not the owner
        """
        # Check permission
        result = await self._permission.check_permission(
            user_id=deleter_id,
            conversation_id=conversation_id,
            action=Action.DELETE_CONVERSATION,
        )
        
        if not result.allowed:
            raise PermissionDeniedError(result.reason or "Permission denied")
        
        # Get all members
        members = await self._storage.get_conversation_members(conversation_id)
        
        # Remove all members
        for member in members:
            await self._storage.remove_member(conversation_id, member.user_id)
            self._permission.remove_member(conversation_id, member.user_id)
        
        # Delete conversation (in real impl, would delete from DB)
        # For now, just log
        logger.info(f"Deleted conversation {conversation_id} by user {deleter_id}")
        
        # Publish event
        await self._publish_event(
            EVENT_CONVERSATION_DELETED,
            {
                "conversation_id": conversation_id,
                "deleted_by": deleter_id,
            },
        )
    
    async def list_conversations(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ConversationRecord]:
        """List conversations a user is a member of.
        
        Args:
            user_id: The user ID
            limit: Maximum number of results
            offset: Number of results to skip
        
        Returns:
            List of ConversationRecords
        """
        conversations = await self._storage.get_user_conversations(user_id)
        
        # Sort by updated_at descending
        conversations.sort(key=lambda c: c.updated_at, reverse=True)
        
        # Apply pagination
        return conversations[offset:offset + limit]
    
    # =========================================================================
    # Member Management
    # =========================================================================
    
    async def invite_member(
        self,
        conversation_id: str,
        inviter_id: str,
        user_id: str,
        nickname: Optional[str] = None,
    ) -> MemberRecord:
        """Invite a member to a conversation.
        
        Args:
            conversation_id: The conversation ID
            inviter_id: The user inviting the member
            user_id: The user being invited
            nickname: Optional nickname in the conversation
        
        Returns:
            The created MemberRecord
        
        Raises:
            PermissionDeniedError: If inviter doesn't have permission
            InvalidOperationError: If user is already a member
        """
        # Check permission
        result = await self._permission.check_permission(
            user_id=inviter_id,
            conversation_id=conversation_id,
            action=Action.ADD_MEMBER,
        )
        
        if not result.allowed:
            raise PermissionDeniedError(result.reason or "Permission denied")
        
        # Check if already a member
        existing = await self._storage.get_member(conversation_id, user_id)
        if existing and existing.is_active:
            raise InvalidOperationError(f"User {user_id} is already a member")
        
        # Create member
        now = datetime.now(timezone.utc)
        member = MemberRecord(
            conversation_id=conversation_id,
            user_id=user_id,
            role="member",
            nickname=nickname,
            joined_at=now,
            is_active=True,
        )
        
        await self._storage.add_member(member)
        
        # Update permission cache
        is_agent = self._permission.is_user_agent(user_id)
        self._permission.set_member_info(MemberInfo(
            conversation_id=conversation_id,
            user_id=user_id,
            role=Role.MEMBER,
            is_agent=is_agent,
            is_active=True,
        ))
        
        # Update conversation timestamp
        conversation = await self._storage.get_conversation(conversation_id)
        if conversation:
            conversation.updated_at = now
            await self._storage.save_conversation(conversation)
        
        # Publish event
        await self._publish_event(
            EVENT_MEMBER_JOINED,
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "inviter_id": inviter_id,
                "member": member.to_dict(),
            },
        )
        
        logger.info(f"User {user_id} joined conversation {conversation_id} (invited by {inviter_id})")
        
        return member
    
    async def remove_member(
        self,
        conversation_id: str,
        remover_id: str,
        user_id: str,
    ) -> None:
        """Remove a member from a conversation.
        
        Args:
            conversation_id: The conversation ID
            remover_id: The user removing the member
            user_id: The user being removed
        
        Raises:
            PermissionDeniedError: If remover doesn't have permission
            InvalidOperationError: If trying to remove owner or self
        """
        # Get member being removed
        member = await self._storage.get_member(conversation_id, user_id)
        if member is None:
            raise MemberNotFoundError(f"Member {user_id} not found")
        
        # Cannot remove owner
        if member.role == "owner":
            raise InvalidOperationError("Cannot remove the owner from a conversation")
        
        # Cannot remove self (use leave instead)
        if remover_id == user_id:
            raise InvalidOperationError("Cannot remove yourself. Use leave instead.")
        
        # Check permission
        result = await self._permission.check_permission(
            user_id=remover_id,
            conversation_id=conversation_id,
            action=Action.REMOVE_MEMBER,
        )
        
        if not result.allowed:
            raise PermissionDeniedError(result.reason or "Permission denied")
        
        # Remove member
        await self._storage.remove_member(conversation_id, user_id)
        self._permission.remove_member(conversation_id, user_id)
        
        # Update conversation timestamp
        conversation = await self._storage.get_conversation(conversation_id)
        if conversation:
            conversation.updated_at = datetime.now(timezone.utc)
            await self._storage.save_conversation(conversation)
        
        # Publish event
        await self._publish_event(
            EVENT_MEMBER_LEFT,
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "removed_by": remover_id,
            },
        )
        
        logger.info(f"User {user_id} removed from conversation {conversation_id} by {remover_id}")
    
    async def leave_conversation(
        self,
        conversation_id: str,
        user_id: str,
    ) -> None:
        """Leave a conversation (self-removal).
        
        Args:
            conversation_id: The conversation ID
            user_id: The user leaving
        
        Raises:
            PermissionDeniedError: If user is the owner
            InvalidOperationError: If user is not a member
        """
        member = await self._storage.get_member(conversation_id, user_id)
        if member is None:
            raise MemberNotFoundError(f"Member {user_id} not found")
        
        if member.role == "owner":
            raise InvalidOperationError("Owner cannot leave. Transfer ownership or delete the conversation.")
        
        await self.remove_member(conversation_id, user_id, user_id)
    
    async def update_member_role(
        self,
        conversation_id: str,
        updater_id: str,
        user_id: str,
        new_role: str,  # "admin" | "member"
    ) -> MemberRecord:
        """Update a member's role.
        
        Args:
            conversation_id: The conversation ID
            updater_id: The user making the update
            user_id: The user whose role is being changed
            new_role: The new role
        
        Raises:
            PermissionDeniedError: If updater doesn't have permission
            InvalidOperationError: If invalid role change
        """
        # Validate role
        if new_role not in ("admin", "member"):
            raise InvalidOperationError("Invalid role. Must be 'admin' or 'member'.")
        
        # Get member
        member = await self._storage.get_member(conversation_id, user_id)
        if member is None:
            raise MemberNotFoundError(f"Member {user_id} not found")
        
        # Cannot change owner's role
        if member.role == "owner":
            raise InvalidOperationError("Cannot change owner's role")
        
        # Cannot change own role
        if updater_id == user_id:
            raise InvalidOperationError("Cannot change your own role")
        
        # Check permission (only owner can set admin)
        result = await self._permission.check_permission(
            user_id=updater_id,
            conversation_id=conversation_id,
            action=Action.SET_ADMIN,
        )
        
        if not result.allowed:
            raise PermissionDeniedError(result.reason or "Permission denied")
        
        # Update role
        updated_member = await self._storage.update_member_role(
            conversation_id,
            user_id,
            new_role,
        )
        
        # Update permission cache
        is_agent = self._permission.is_user_agent(user_id)
        role_enum = Role.ADMIN if new_role == "admin" else Role.MEMBER
        self._permission.set_member_info(MemberInfo(
            conversation_id=conversation_id,
            user_id=user_id,
            role=role_enum,
            is_agent=is_agent,
            is_active=True,
        ))
        
        # Update conversation timestamp
        conversation = await self._storage.get_conversation(conversation_id)
        if conversation:
            conversation.updated_at = datetime.now(timezone.utc)
            await self._storage.save_conversation(conversation)
        
        # Publish event
        await self._publish_event(
            EVENT_MEMBER_ROLE_CHANGED,
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "old_role": member.role,
                "new_role": new_role,
                "changed_by": updater_id,
            },
        )
        
        logger.info(f"User {user_id} role changed to {new_role} in conversation {conversation_id} by {updater_id}")
        
        return updated_member
    
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
            MemberRecord if found
        """
        return await self._storage.get_member(conversation_id, user_id)
    
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
        return await self._storage.get_conversation_members(conversation_id)
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    async def _publish_event(
        self,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        """Publish an event to the event bus.
        
        Args:
            event_type: The event type
            data: Event data
        """
        if self._event_bus:
            try:
                await self._event_bus.emit_async(event_type, data, sender=self)
            except Exception as e:
                logger.error(f"Failed to publish event {event_type}: {e}")


# ============================================================================
# Factory
# ============================================================================

def create_conversation_service(
    storage: LayeredStorageService,
    permission_service: PermissionService,
    event_bus: Optional[PluginEventBus] = None,
) -> ConversationService:
    """Create a conversation service.
    
    Args:
        storage: The layered storage service
        permission_service: The permission service
        event_bus: Optional event bus
    
    Returns:
        Configured ConversationService
    """
    return ConversationService(
        storage=storage,
        permission_service=permission_service,
        event_bus=event_bus,
    )


__all__ = [
    "ConversationService",
    "create_conversation_service",
    "ConversationError",
    "ConversationNotFoundError",
    "MemberNotFoundError",
    "PermissionDeniedError",
    "InvalidOperationError",
    "EVENT_CONVERSATION_CREATED",
    "EVENT_CONVERSATION_UPDATED",
    "EVENT_CONVERSATION_DELETED",
    "EVENT_MEMBER_JOINED",
    "EVENT_MEMBER_LEFT",
    "EVENT_MEMBER_ROLE_CHANGED",
]
