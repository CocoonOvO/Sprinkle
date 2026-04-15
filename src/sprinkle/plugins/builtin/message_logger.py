"""Message logger plugin for Sprinkle - logs all messages."""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sprinkle.plugins.base import Plugin

if TYPE_CHECKING:
    from sprinkle.kernel.message import Message

logger = logging.getLogger(__name__)


class MessageLoggerPlugin(Plugin):
    """
    A plugin that logs all messages for debugging and monitoring.
    
    This plugin provides comprehensive message logging including:
    - Message content and metadata
    - Message direction (incoming/outgoing)
    - Timestamp tracking
    - Statistics collection
    
    Note:
        This plugin has high priority to ensure it logs messages
        before other plugins can modify or drop them.
    
    Attributes:
        name: Plugin identifier.
        version: Plugin version.
        log_incoming: Whether to log incoming messages.
        log_outgoing: Whether to log outgoing messages.
    """
    
    name = "message-logger"
    version = "1.0.0"
    dependencies = []
    priority = 100  # High priority - runs first to capture original messages
    
    def __init__(
        self,
        log_incoming: bool = True,
        log_outgoing: bool = True,
        max_entries: int = 1000
    ):
        """
        Initialize the message logger plugin.
        
        Args:
            log_incoming: Log incoming messages (default True).
            log_outgoing: Log outgoing messages (default True).
            max_entries: Maximum number of message entries to keep in memory.
        """
        super().__init__()
        self._log_incoming = log_incoming
        self._log_outgoing = log_outgoing
        self._max_entries = max_entries
        
        self._incoming_count = 0
        self._outgoing_count = 0
        self._recent_messages: List[dict] = []
    
    def on_load(self) -> None:
        """Called when plugin is loaded."""
        logger.info(
            f"MessageLoggerPlugin v{self.version} initialized. "
            f"log_incoming={self._log_incoming}, log_outgoing={self._log_outgoing}"
        )
        self.set_metadata("started_at", datetime.now().isoformat())
    
    def on_message(self, message: "Message") -> Optional["Message"]:
        """
        Log incoming messages.
        
        Args:
            message: The incoming message.
            
        Returns:
            The unchanged message.
        """
        if not self._log_incoming:
            return message
        
        self._incoming_count += 1
        
        entry = {
            "direction": "incoming",
            "count": self._incoming_count,
            "timestamp": datetime.now().isoformat(),
            "content_preview": self._get_content_preview(message),
            "message_id": getattr(message, 'id', None),
            "conversation_id": getattr(message, 'conversation_id', None),
            "sender_id": getattr(message, 'sender_id', None),
        }
        
        self._add_entry(entry)
        
        logger.debug(
            f"[MessageLogger] IN #{self._incoming_count}: "
            f"conv={entry['conversation_id']}, "
            f"sender={entry['sender_id']}, "
            f"content={entry['content_preview']}"
        )
        
        return message
    
    def on_before_send(self, message: "Message") -> "Message":
        """
        Log outgoing messages.
        
        Args:
            message: The message being sent.
            
        Returns:
            The unchanged message.
        """
        if not self._log_outgoing:
            return message
        
        self._outgoing_count += 1
        
        entry = {
            "direction": "outgoing",
            "count": self._outgoing_count,
            "timestamp": datetime.now().isoformat(),
            "content_preview": self._get_content_preview(message),
            "message_id": getattr(message, 'id', None),
            "conversation_id": getattr(message, 'conversation_id', None),
        }
        
        self._add_entry(entry)
        
        logger.debug(
            f"[MessageLogger] OUT #{self._outgoing_count}: "
            f"conv={entry['conversation_id']}, "
            f"content={entry['content_preview']}"
        )
        
        return message
    
    def _get_content_preview(self, message: "Message", max_length: int = 100) -> str:
        """
        Get a preview of message content.
        
        Args:
            message: The message object.
            max_length: Maximum length of preview.
            
        Returns:
            Preview string.
        """
        content = getattr(message, 'content', None)
        if content is None:
            return "(empty)"
        
        content_str = str(content)
        if len(content_str) > max_length:
            return content_str[:max_length] + "..."
        return content_str
    
    def _add_entry(self, entry: dict) -> None:
        """
        Add a message entry to recent messages.
        
        Args:
            entry: Message entry dictionary.
        """
        self._recent_messages.append(entry)
        
        # Trim to max_entries
        if len(self._recent_messages) > self._max_entries:
            self._recent_messages.pop(0)
    
    def on_unload(self) -> None:
        """Called when plugin is unloaded."""
        logger.info(
            f"MessageLoggerPlugin unloaded. "
            f"Logged {self._incoming_count} incoming, "
            f"{self._outgoing_count} outgoing messages."
        )
        self.set_metadata("stopped_at", datetime.now().isoformat())
        self.set_metadata(
            "total_incoming", 
            self._incoming_count
        )
        self.set_metadata(
            "total_outgoing", 
            self._outgoing_count
        )
    
    def get_stats(self) -> dict:
        """
        Get message logging statistics.
        
        Returns:
            Dictionary with statistics.
        """
        return {
            "incoming_count": self._incoming_count,
            "outgoing_count": self._outgoing_count,
            "total_count": self._incoming_count + self._outgoing_count,
            "recent_entries": len(self._recent_messages),
            "started_at": self.get_metadata("started_at"),
        }
    
    def get_recent_messages(self, limit: int = 10) -> List[dict]:
        """
        Get recent message entries.
        
        Args:
            limit: Maximum number of entries to return.
            
        Returns:
            List of message entry dictionaries.
        """
        return self._recent_messages[-limit:]
    
    @property
    def incoming_count(self) -> int:
        """Get count of incoming messages."""
        return self._incoming_count
    
    @property
    def outgoing_count(self) -> int:
        """Get count of outgoing messages."""
        return self._outgoing_count
