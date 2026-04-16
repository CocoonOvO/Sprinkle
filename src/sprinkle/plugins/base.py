"""Plugin base interface for Sprinkle."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type

if TYPE_CHECKING:
    from sprinkle.kernel.message import Message


class DropMessage(Exception):
    """
    Exception to signal that a message should be dropped and not continue processing.
    
    When a plugin raises this exception in on_message(), the message will be
    silently dropped and not passed to subsequent plugins or delivered.
    """
    pass


class Plugin(ABC):
    """
    Base class for all Sprinkle plugins.
    
    Plugins can intercept and process messages through lifecycle hooks.
    They are executed in order of priority (higher priority runs first).
    
    Attributes:
        name: Unique identifier for the plugin.
        version: Plugin version string.
        dependencies: List of plugin names that must be loaded before this plugin.
        priority: Execution priority (0-100, higher runs first). Default is 50.
    
    Example:
        ```python
        class MyPlugin(Plugin):
            name = "my-plugin"
            version = "1.0.0"
            priority = 60
            
            def on_load(self):
                print("Plugin loaded!")
            
            def on_message(self, message: "Message") -> Optional["Message"]:
                if "bad word" in message.content:
                    raise DropMessage("Message filtered")
                return message
        ```
    """
    
    name: str = "base-plugin"
    version: str = "0.0.0"
    dependencies: List[str] = []
    priority: int = 50
    
    def __init__(self):
        self._enabled = False
        self._metadata: Dict[str, Any] = {}
    
    @property
    def enabled(self) -> bool:
        """Check if plugin is enabled."""
        return self._enabled
    
    @property
    def metadata(self) -> Dict[str, Any]:
        """Get plugin metadata."""
        return self._metadata
    
    def set_metadata(self, key: str, value: Any) -> None:
        """Set a metadata value."""
        self._metadata[key] = value
    
    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get a metadata value."""
        return self._metadata.get(key, default)
    
    def on_load(self) -> None:
        """
        Called when the plugin is loaded.
        
        Override this method to perform initialization tasks like
        opening connections, loading configuration, etc.
        """
        pass
    
    def on_message(self, message: "Message") -> Optional["Message"]:
        """
        Intercept and process incoming messages.
        
        Args:
            message: The incoming message to process.
            
        Returns:
            - The (possibly modified) message to continue processing
            - None to keep the message unchanged
            
        Raises:
            DropMessage: To silently drop the message and stop processing.
        
        Note:
            - This method is called for every message before it's processed
            - Raising DropMessage will silently drop the message
            - This method should not raise other exceptions to avoid
              crashing the main process
        """
        return message
    
    def on_before_send(self, message: "Message") -> "Message":
        """
        Process a message before it's sent.
        
        Args:
            message: The message that will be sent.
            
        Returns:
            The (possibly modified) message to send.
            
        Note:
            - This is called just before a message is sent to recipients
            - Use this to add decorations, filters, etc.
        """
        return message
    
    def on_unload(self) -> None:
        """
        Called when the plugin is unloaded.
        
        Override this method to clean up resources like
        closing connections, saving state, etc.
        """
        pass
    
    def _do_load(self) -> None:
        """Internal method to mark plugin as loaded and call on_load."""
        self._enabled = True
        self.on_load()
    
    def _do_unload(self) -> None:
        """Internal method to mark plugin as unloaded and call on_unload."""
        self._enabled = False
        self.on_unload()
    
    @classmethod
    def get_dependencies(cls) -> List[str]:
        """Get list of plugin names this plugin depends on."""
        return cls.dependencies.copy()
    
    @classmethod
    def get_priority(cls) -> int:
        """Get plugin priority."""
        return cls.priority
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name!r}, version={self.version!r})>"
