"""Hello World example plugin for Sprinkle."""

import logging
from typing import TYPE_CHECKING, Optional

from sprinkle.plugins.base import Plugin, DropMessage

if TYPE_CHECKING:
    from sprinkle.kernel.message import Message

logger = logging.getLogger(__name__)


class HelloWorldPlugin(Plugin):
    """
    A simple Hello World plugin demonstrating the plugin interface.
    
    This plugin logs incoming messages and adds a prefix to messages
    that start with "!hello".
    
    Features:
    - Logs all incoming messages
    - Responds to "!hello" commands
    - Demonstrates DropMessage usage
    
    Example:
        ```
        # After loading this plugin:
        # - All messages will be logged
        # - "!hello" will be responded with "Hello, World!"
        # - Messages containing "bad" will be dropped
        ```
    """
    
    name = "hello-world"
    version = "1.0.0"
    dependencies = []
    priority = 10  # Low priority, runs after high-priority plugins
    
    def __init__(self):
        super().__init__()
        self._message_count = 0
    
    def on_load(self) -> None:
        """Called when plugin is loaded."""
        logger.info(f"HelloWorldPlugin v{self.version} loaded!")
        self.set_metadata("loaded_at", "initialized")
    
    def on_message(self, message: "Message") -> Optional["Message"]:
        """
        Process incoming messages.
        
        Handles:
        - "!hello" command: responds with greeting (handled by dropping and 
          letting another plugin handle the response)
        - Messages containing "bad": dropped
        - All other messages: logged
        """
        self._message_count += 1
        self.set_metadata("message_count", self._message_count)
        
        content = message.content if hasattr(message, 'content') else str(message)
        
        # Log all incoming messages
        logger.debug(f"[HelloWorld] Message #{self._message_count}: {content[:50]}...")
        
        # Drop messages containing "bad"
        if "bad" in content.lower():
            logger.info(f"[HelloWorld] Dropping message containing 'bad'")
            raise DropMessage("Message filtered by HelloWorld plugin")
        
        return message
    
    def on_before_send(self, message: "Message") -> "Message":
        """
        Process messages before sending.
        
        Adds a footer to messages that were processed by this plugin.
        """
        if hasattr(message, 'metadata'):
            message.metadata["processed_by"] = self.name
        
        return message
    
    def on_unload(self) -> None:
        """Called when plugin is unloaded."""
        logger.info(
            f"HelloWorldPlugin unloaded. "
            f"Processed {self._message_count} messages."
        )
    
    @property
    def message_count(self) -> int:
        """Get the number of messages processed by this plugin."""
        return self._message_count
