"""Message Service - business logic for message management."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sprinkle.kernel.permission import (
    Action,
    PermissionCheckResult,
    PermissionService,
)
from sprinkle.kernel.auth import UserCredentials
from sprinkle.plugins.events import PluginEventBus
from sprinkle.storage.layered import (
    LayeredStorageService,
    MessageRecord,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Events
# ============================================================================

EVENT_MESSAGE_SENT = "message.sent"
EVENT_MESSAGE_EDITED = "message.edited"
EVENT_MESSAGE_DELETED = "message.deleted"


# ============================================================================
# Exceptions
# ============================================================================

class MessageError(Exception):
    """Base exception for message errors."""
    pass


class MessageNotFoundError(MessageError):
    """Message not found."""
    pass


class ConversationNotFoundError(MessageError):
    """Conversation not found."""
    pass


class PermissionDeniedError(MessageError):
    """Permission denied."""
    pass


class InvalidOperationError(MessageError):
    """Invalid operation."""
    pass


# ============================================================================
# Message Service
# ============================================================================

class MessageService:
    """Message service for managing messages.
    
    This service implements the business logic for message operations
    including sending, editing, and deleting messages.
    
    Example:
        service = MessageService(
            storage=storage,
            permission=permission_service,
            event_bus=event_bus,
            ws_manager=ws_manager,
        )
        
        # Send a message
        message = await service.send_message(
            sender_id="user_123",
            conversation_id="conv_456",
            content="Hello, world!",
            content_type="text",
            mentions=["user_789"],
            reply_to="msg_111",
        )
        
        # Edit a message
        updated = await service.edit_message(
            message_id="msg_222",
            editor_id="user_123",
            new_content="Updated content",
        )
        
        # Delete a message
        await service.delete_message(
            message_id="msg_222",
            deleter_id="user_123",
        )
    """
    
    def __init__(
        self,
        storage: LayeredStorageService,
        permission_service: PermissionService,
        event_bus: Optional[PluginEventBus] = None,
        ws_manager=None,  # ConnectionManager from api/websocket.py
    ):
        """Initialize the message service.
        
        Args:
            storage: The layered storage service
            permission_service: The permission service
            event_bus: Optional event bus for publishing events
            ws_manager: Optional WebSocket connection manager for real-time delivery
        """
        self._storage = storage
        self._permission = permission_service
        self._event_bus = event_bus
        self._ws_manager = ws_manager
    
    # =========================================================================
    # Message Operations
    # =========================================================================
    
    async def send_message(
        self,
        sender_id: str,
        conversation_id: str,
        content: str,
        content_type: str = "text",
        mentions: Optional[List[str]] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MessageRecord:
        """Send a new message.
        
        Args:
            sender_id: The user sending the message
            conversation_id: The conversation ID
            content: The message content
            content_type: Content type (text/markdown/image/file)
            mentions: List of mentioned user IDs
            reply_to: ID of the message being replied to
            metadata: Optional metadata
        
        Returns:
            The created MessageRecord
        
        Raises:
            PermissionDeniedError: If sender is not a member
            InvalidOperationError: If reply_to message doesn't exist or is in different conversation
        """
        # Check permission (must be a member to send)
        result = await self._permission.check_permission(
            user_id=sender_id,
            conversation_id=conversation_id,
            action=Action.SEND_MESSAGE,
        )
        
        if not result.allowed:
            raise PermissionDeniedError(result.reason or "Cannot send message")
        
        # Verify conversation exists
        conversation = await self._storage.get_conversation(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(f"Conversation {conversation_id} not found")
        
        # Verify reply_to message if provided
        if reply_to:
            reply_message = await self._storage.get_message(reply_to)
            if reply_message is None:
                raise InvalidOperationError(f"Reply-to message {reply_to} not found")
            if reply_message.conversation_id != conversation_id:
                raise InvalidOperationError("Reply-to message is in a different conversation")
        
        # Create message
        now = datetime.now(timezone.utc)
        message_id = str(uuid4())
        
        message = MessageRecord(
            id=message_id,
            conversation_id=conversation_id,
            sender_id=sender_id,
            content=content,
            content_type=content_type,
            metadata=metadata or {},
            mentions=mentions or [],
            reply_to=reply_to,
            created_at=now,
        )
        
        # Save message (dual write)
        await self._storage.save_message(message)
        
        # Update conversation timestamp
        conversation.updated_at = now
        await self._storage.save_conversation(conversation)
        
        # Publish event
        await self._publish_event(
            EVENT_MESSAGE_SENT,
            message.to_dict(),
        )
        
        # Deliver to connected clients via WebSocket
        await self._deliver_to_subscribers(
            conversation_id=conversation_id,
            message=message,
            exclude_sender=True,
        )
        
        # Trigger push notification routing
        await self._trigger_push_notification(
            event_type="chat.message",
            conversation_id=conversation_id,
            sender_id=sender_id,
            content=content,
            mentions=mentions or [],
            reply_to=reply_to,
            metadata={"reply_to": reply_to, **(metadata or {})},
        )
        
        logger.info(f"Message {message_id} sent by {sender_id} to conversation {conversation_id}")
        
        return message
    
    async def send_stream_message(
        self,
        sender_id: str,
        conversation_id: str,
        chunks: List[str],
        content_type: str = "text",
        mentions: Optional[List[str]] = None,
        reply_to: Optional[str] = None,
    ) -> MessageRecord:
        """Send a stream message (merged from multiple chunks).
        
        This is used for AI responses that are generated incrementally.
        All chunks are merged into a single message.
        
        Args:
            sender_id: The user sending the message
            conversation_id: The conversation ID
            chunks: List of content chunks to merge
            content_type: Content type
            mentions: List of mentioned user IDs
            reply_to: ID of the message being replied to
        
        Returns:
            The created MessageRecord with merged content
        """
        # Merge all chunks
        content = ''.join(chunks)
        
        # Send as a regular message
        return await self.send_message(
            sender_id=sender_id,
            conversation_id=conversation_id,
            content=content,
            content_type=content_type,
            mentions=mentions,
            reply_to=reply_to,
        )
    
    async def edit_message(
        self,
        message_id: str,
        editor_id: str,
        new_content: str,
    ) -> MessageRecord:
        """Edit an existing message.
        
        Args:
            message_id: The message ID
            editor_id: The user editing the message
            new_content: The new content
        
        Returns:
            Updated MessageRecord
        
        Raises:
            MessageNotFoundError: If message not found
            PermissionDeniedError: If editor doesn't have permission
            InvalidOperationError: If message is deleted
        """
        # Get message
        message = await self._storage.get_message(message_id)
        
        if message is None:
            raise MessageNotFoundError(f"Message {message_id} not found")
        
        if message.is_deleted:
            raise InvalidOperationError("Cannot edit a deleted message")
        
        # Check permission
        result = await self._permission.check_permission(
            user_id=editor_id,
            conversation_id=message.conversation_id,
            action=Action.EDIT_OWN_MESSAGE,
        )
        
        if not result.allowed:
            # Check if they can edit any message (admin/owner)
            result = await self._permission.check_permission(
                user_id=editor_id,
                conversation_id=message.conversation_id,
                action=Action.EDIT_OWN_MESSAGE,  # Same action, but we check role
            )
            
            # Actually, let's use a proper check:
            # Admin/owner can edit any message
            role = await self._permission.get_user_role(editor_id, message.conversation_id)
            if role is None:
                raise PermissionDeniedError("Not a member of this conversation")
            
            # For now, we allow edit if:
            # 1. User is the sender AND not an agent member
            # 2. User is admin or owner
            can_edit = False
            
            if message.sender_id == editor_id:
                # Check if sender is an agent member (not admin)
                sender_role = await self._permission.get_user_role(message.sender_id, message.conversation_id)
                from sprinkle.kernel.permission import Role as RoleEnum
                if sender_role == RoleEnum.MEMBER and self._permission.is_user_agent(message.sender_id):
                    # Regular agent cannot edit their own messages
                    raise PermissionDeniedError("Agents cannot edit their own messages")
                can_edit = True
            else:
                # Admin or owner can edit any message
                if role in (RoleEnum.OWNER, RoleEnum.ADMIN):
                    can_edit = True
            
            if not can_edit:
                raise PermissionDeniedError("Cannot edit this message")
        
        # Update message
        now = datetime.now(timezone.utc)
        message.content = new_content
        message.edited_at = now
        
        # Get the message key and update in Redis
        from sprinkle.storage.layered import message_id_key
        import json
        await self._storage._redis.set(
            message_id_key(message_id),
            json.dumps(message.to_dict()),
        )
        
        # Publish event
        await self._publish_event(
            EVENT_MESSAGE_EDITED,
            {
                "message_id": message_id,
                "conversation_id": message.conversation_id,
                "editor_id": editor_id,
                "new_content": new_content,
                "edited_at": now.isoformat(),
            },
        )
        
        # Deliver update to subscribers
        await self._deliver_to_subscribers(
            conversation_id=message.conversation_id,
            message=message,
            event_type="message.edited",
            exclude_sender=False,
        )
        
        # Trigger push notification
        await self._trigger_push_notification(
            event_type="chat.message.edited",
            conversation_id=message.conversation_id,
            sender_id=editor_id,
            content=new_content,
            metadata={"message_id": message_id, "edited_at": now.isoformat()},
        )
        
        logger.info(f"Message {message_id} edited by {editor_id}")
        
        return message
    
    async def delete_message(
        self,
        message_id: str,
        deleter_id: str,
    ) -> None:
        """Delete a message (soft delete).
        
        Args:
            message_id: The message ID
            deleter_id: The user deleting the message
        
        Raises:
            MessageNotFoundError: If message not found
            PermissionDeniedError: If deleter doesn't have permission
            InvalidOperationError: If message is already deleted
        """
        # Get message
        message = await self._storage.get_message(message_id)
        
        if message is None:
            raise MessageNotFoundError(f"Message {message_id} not found")
        
        if message.is_deleted:
            raise InvalidOperationError("Message is already deleted")
        
        # Check permission
        can_delete = False
        
        from sprinkle.kernel.permission import Role as RoleEnum
        
        # Admin or owner can delete any message
        role = await self._permission.get_user_role(deleter_id, message.conversation_id)
        if role in (RoleEnum.OWNER, RoleEnum.ADMIN):
            can_delete = True
        elif message.sender_id == deleter_id:
            # Sender can delete their own message
            # Check if sender is an agent member (not admin)
            sender_role = await self._permission.get_user_role(message.sender_id, message.conversation_id)
            if sender_role == RoleEnum.MEMBER and self._permission.is_user_agent(message.sender_id):
                # Regular agent cannot delete their own messages
                raise PermissionDeniedError("Agents cannot delete their own messages")
            can_delete = True
        
        if not can_delete:
            raise PermissionDeniedError("Cannot delete this message")
        
        # Soft delete
        await self._storage.soft_delete_message(message_id)
        
        # Get updated message
        message = await self._storage.get_message(message_id)
        
        # Publish event
        await self._publish_event(
            EVENT_MESSAGE_DELETED,
            {
                "message_id": message_id,
                "conversation_id": message.conversation_id if message else None,
                "deleted_by": deleter_id,
                "deleted_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        
        # Deliver deletion notice to subscribers
        if message:
            await self._deliver_to_subscribers(
                conversation_id=message.conversation_id,
                message=message,
                event_type="message.deleted",
                exclude_sender=False,
            )
        
        # Trigger push notification
        await self._trigger_push_notification(
            event_type="chat.message.deleted",
            conversation_id=message.conversation_id if message else conversation_id,
            sender_id=deleter_id,
            content="[deleted message]",
            metadata={"message_id": message_id, "deleted_at": datetime.now(timezone.utc).isoformat()},
        )
        
        logger.info(f"Message {message_id} deleted by {deleter_id}")
    
    async def get_message(
        self,
        message_id: str,
        requester_id: str,
    ) -> Optional[MessageRecord]:
        """Get a message by ID.
        
        Args:
            message_id: The message ID
            requester_id: The user requesting the message
        
        Returns:
            MessageRecord if found and requester has access
        """
        message = await self._storage.get_message(message_id)
        
        if message is None:
            return None
        
        # Check if requester is a member
        result = await self._permission.check_permission(
            user_id=requester_id,
            conversation_id=message.conversation_id,
            action=Action.VIEW_CONVERSATION,
        )
        
        if not result.allowed:
            raise PermissionDeniedError("Access denied")
        
        return message
    
    async def get_conversation_messages(
        self,
        conversation_id: str,
        requester_id: str,
        limit: int = 50,
        before: Optional[datetime] = None,
    ) -> List[MessageRecord]:
        """Get messages for a conversation.
        
        Args:
            conversation_id: The conversation ID
            requester_id: The user requesting messages
            limit: Maximum number of messages
            before: Only return messages before this time
        
        Returns:
            List of MessageRecords, sorted by created_at descending
        
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
        
        return await self._storage.get_conversation_messages(
            conversation_id=conversation_id,
            limit=limit,
            before=before,
        )
    
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
    
    async def _deliver_to_subscribers(
        self,
        conversation_id: str,
        message: MessageRecord,
        event_type: str = "message",
        exclude_sender: bool = True,
    ) -> None:
        """Deliver a message to all subscribers of a conversation.
        
        Args:
            conversation_id: The conversation ID
            message: The message to deliver
            event_type: The event type for the delivery
            exclude_sender: Whether to exclude the sender
        """
        if self._ws_manager is None:
            return
        
        try:
            # Get all sessions subscribed to this conversation
            # This is a simplified implementation
            from sprinkle.api.websocket import ConnectionManager
            
            # Get all websocket connections
            ws_connections = ConnectionManager._ws_connections
            
            for session_id, websocket in ws_connections.items():
                # Check if this session is subscribed to the conversation
                # (In real impl, would check session subscriptions)
                
                # Skip sender if specified
                if exclude_sender:
                    # Would need to look up user_id from session
                    # For now, skip based on some identifier
                    pass
                
                # Send message
                await ConnectionManager.send_to_websocket(session_id, {
                    "type": event_type,
                    "data": message.to_dict(),
                })
        except Exception as e:
            logger.error(f"Failed to deliver message to subscribers: {e}")
    
    async def _trigger_push_notification(
        self,
        event_type: str,
        conversation_id: str,
        sender_id: str,
        content: str,
        mentions: Optional[List[str]] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Trigger push notification routing for an event.
        
        This method creates a PushEventData and routes it to subscribed agents.
        It manages its own database session internally.
        
        Args:
            event_type: The type of push event (e.g., "chat.message")
            conversation_id: The conversation ID
            sender_id: Who triggered the event
            content: The message content
            mentions: List of mentioned user IDs
            reply_to: ID of message being replied to
            metadata: Additional event metadata
        """
        try:
            # Import here to avoid circular imports
            from sprinkle.push.events import PushEvent, PushEventData
            from sprinkle.storage.database import get_async_session
            
            # Map event_type string to PushEvent enum
            event_map = {
                "chat.message": PushEvent.CHAT_MESSAGE,
                "chat.message.edited": PushEvent.CHAT_MESSAGE_EDITED,
                "chat.message.deleted": PushEvent.CHAT_MESSAGE_DELETED,
                "chat.message.reply": PushEvent.CHAT_MESSAGE_REPLY,
                "mention": PushEvent.MENTION,
                "group.member.joined": PushEvent.GROUP_MEMBER_JOINED,
                "group.member.left": PushEvent.GROUP_MEMBER_LEFT,
                "group.member.kicked": PushEvent.GROUP_MEMBER_KICKED,
                "group.created": PushEvent.GROUP_CREATED,
                "group.disbanded": PushEvent.GROUP_DISBANDED,
                "group.info.updated": PushEvent.GROUP_INFO_UPDATED,
                "system.notification": PushEvent.SYSTEM_NOTIFICATION,
            }
            
            push_event = event_map.get(event_type, PushEvent.CHAT_MESSAGE)
            
            # Create PushEventData
            event_data = PushEventData(
                event=push_event,
                conversation_id=conversation_id,
                sender_id=sender_id,
                target_ids=mentions or [],
                content=content,
                metadata=metadata or {},
                template_name=event_type,
            )
            
            # Route and deliver using push system
            async with get_async_session() as db:
                from sprinkle.push.subscription import SubscriptionService
                from sprinkle.push.templates import PushTemplateEngine
                from sprinkle.push.router import PushRouter
                
                subscription_service = SubscriptionService(db)
                template_engine = PushTemplateEngine(db)
                router = PushRouter(subscription_service, template_engine)
                
                # Route event to subscribed agents
                results = await router.route_event_with_render(event_data)
                
                if results:
                    logger.info(f"Push notification routed to {len(results)} agents for event {event_type}")
                    
                    # Deliver to each agent
                    # For now, delivery is handled by the agent's registered callback
                    # This will be expanded in future phases
                    for agent_id, rendered_content in results:
                        await self._deliver_push_to_agent(agent_id, event_data, rendered_content)
                        
        except ImportError as e:
            logger.warning(f"Push system not available: {e}")
        except Exception as e:
            logger.error(f"Failed to trigger push notification: {e}")
    
    async def _deliver_push_to_agent(
        self,
        agent_id: str,
        event_data: Any,
        rendered_content: str,
    ) -> None:
        """Deliver a push notification to a specific agent.
        
        This method can be extended to support different delivery mechanisms
        (WebSocket, HTTP webhook, email, etc.) based on agent configuration.
        
        Args:
            agent_id: The target agent ID
            event_data: The push event data
            rendered_content: The rendered notification content
        """
        if self._ws_manager is None:
            return
        
        try:
            # Import here to avoid circular imports
            from sprinkle.api.websocket import ConnectionManager
            
            # Find sessions for this agent and send push
            # This is a simplified implementation
            ws_connections = ConnectionManager._ws_connections
            
            for session_id, websocket in ws_connections.items():
                # Would need to look up if this session belongs to the agent
                # For now, just broadcast to all sessions subscribed to the conversation
                pass
                
        except Exception as e:
            logger.error(f"Failed to deliver push to agent {agent_id}: {e}")


# ============================================================================
# Factory
# ============================================================================

def create_message_service(
    storage: LayeredStorageService,
    permission_service: PermissionService,
    event_bus: Optional[PluginEventBus] = None,
    ws_manager=None,
) -> MessageService:
    """Create a message service.
    
    Args:
        storage: The layered storage service
        permission_service: The permission service
        event_bus: Optional event bus
        ws_manager: Optional WebSocket connection manager
    
    Returns:
        Configured MessageService
    """
    return MessageService(
        storage=storage,
        permission_service=permission_service,
        event_bus=event_bus,
        ws_manager=ws_manager,
    )


__all__ = [
    "MessageService",
    "create_message_service",
    "MessageError",
    "MessageNotFoundError",
    "ConversationNotFoundError",
    "PermissionDeniedError",
    "InvalidOperationError",
    "EVENT_MESSAGE_SENT",
    "EVENT_MESSAGE_EDITED",
    "EVENT_MESSAGE_DELETED",
]
