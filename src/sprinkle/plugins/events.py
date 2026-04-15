"""Plugin event bus for Sprinkle - integrates with Event Bus."""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class PluginEventBus:
    """
    Event bus specifically designed for plugin communication and lifecycle events.
    
    Features:
    - Event subscription with callback handlers
    - Priority-based event handler chain
    - Error isolation per handler
    - Loop detection via depth tracking
    - Sync and async event dispatch
    
    Attributes:
        max_depth: Maximum event chain depth to prevent infinite loops.
        timeout: Maximum time (seconds) for a single event handler.
    """
    
    def __init__(self, max_depth: int = 10, timeout: float = 5.0):
        """
        Initialize the plugin event bus.
        
        Args:
            max_depth: Maximum event chain depth (default: 10).
            timeout: Handler timeout in seconds (default: 5.0).
        """
        self._max_depth = max_depth
        self._timeout = timeout
        
        # Event registry: event_name -> list of (priority, handler, plugin_name)
        self._handlers: Dict[str, List[Tuple[int, Callable, str]]] = defaultdict(list)
        
        # Currently executing events for loop detection
        self._executing: Set[str] = set()
        
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()
    
    @property
    def max_depth(self) -> int:
        """Get maximum event chain depth."""
        return self._max_depth
    
    @property
    def timeout(self) -> float:
        """Get handler timeout in seconds."""
        return self._timeout
    
    def on(self, event_name: str, handler: Callable, plugin_name: str, priority: int = 50) -> None:
        """
        Subscribe a handler to an event.
        
        Args:
            event_name: Name of the event to subscribe to.
            handler: Callback function to handle the event.
            plugin_name: Name of the plugin registering the handler.
            priority: Handler priority (higher = runs first). Default 50.
        
        Example:
            ```python
            event_bus.on("message.received", my_handler, "my-plugin", priority=60)
            ```
        """
        self._handlers[event_name].append((priority, handler, plugin_name))
        # Sort by priority descending (higher priority first)
        self._handlers[event_name].sort(key=lambda x: -x[0])
        logger.debug(f"Handler registered: {event_name} from {plugin_name} (priority={priority})")
    
    def off(self, event_name: str, handler: Callable) -> bool:
        """
        Unsubscribe a handler from an event.
        
        Args:
            event_name: Name of the event.
            handler: The handler function to remove.
            
        Returns:
            True if handler was found and removed, False otherwise.
        """
        handlers = self._handlers.get(event_name, [])
        for i, (_, h, _) in enumerate(handlers):
            if h == handler:
                handlers.pop(i)
                logger.debug(f"Handler unregistered: {event_name}")
                return True
        return False
    
    def off_all(self, plugin_name: str) -> int:
        """
        Unsubscribe all handlers for a specific plugin.
        
        Args:
            plugin_name: Name of the plugin.
            
        Returns:
            Number of handlers removed.
        """
        count = 0
        for event_name in list(self._handlers.keys()):
            handlers = self._handlers[event_name]
            original_len = len(handlers)
            self._handlers[event_name] = [
                (p, h, n) for p, h, n in handlers if n != plugin_name
            ]
            count += original_len - len(self._handlers[event_name])
        if count > 0:
            logger.debug(f"All handlers for plugin {plugin_name} unregistered ({count} removed)")
        return count
    
    def emit(self, event_name: str, *args, depth: int = 0, **kwargs) -> List[Any]:
        """
        Emit a synchronous event to all handlers.
        
        Args:
            event_name: Name of the event to emit.
            *args: Positional arguments to pass to handlers.
            depth: Current event chain depth (for loop detection).
            **kwargs: Keyword arguments to pass to handlers.
            
        Returns:
            List of results from all handlers.
            
        Raises:
            RecursionError: If event chain depth exceeds max_depth.
        """
        if depth > self._max_depth:
            logger.warning(f"Event chain exceeded max depth ({self._max_depth}): {event_name}")
            raise RecursionError(f"Event chain exceeded max depth for {event_name}")
        
        # Loop detection: check if we're already processing this event
        if event_name in self._executing:
            logger.warning(f"Event loop detected: {event_name}")
            return []
        
        self._executing.add(event_name)
        
        try:
            results = []
            handlers = self._handlers.get(event_name, [])
            
            for priority, handler, plugin_name in handlers:
                try:
                    result = handler(*args, **kwargs)
                    results.append(result)
                except Exception as e:
                    # Error isolation: log and continue
                    logger.error(f"Error in event handler {plugin_name}.{event_name}: {e}")
                    results.append(None)
            
            return results
        finally:
            self._executing.discard(event_name)
    
    async def emit_async(self, event_name: str, *args, depth: int = 0, **kwargs) -> List[Any]:
        """
        Emit an asynchronous event to all handlers.
        
        Args:
            event_name: Name of the event to emit.
            *args: Positional arguments to pass to handlers.
            depth: Current event chain depth (for loop detection).
            **kwargs: Keyword arguments to pass to handlers.
            
        Returns:
            List of results from all handlers.
            
        Raises:
            RecursionError: If event chain depth exceeds max_depth.
        """
        if depth > self._max_depth:
            logger.warning(f"Event chain exceeded max depth ({self._max_depth}): {event_name}")
            raise RecursionError(f"Event chain exceeded max depth for {event_name}")
        
        if event_name in self._executing:
            logger.warning(f"Event loop detected: {event_name}")
            return []
        
        self._executing.add(event_name)
        
        try:
            results = []
            handlers = self._handlers.get(event_name, [])
            
            for priority, handler, plugin_name in handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        result = await asyncio.wait_for(
                            handler(*args, **kwargs),
                            timeout=self._timeout
                        )
                    else:
                        result = handler(*args, **kwargs)
                    results.append(result)
                except asyncio.TimeoutError:
                    logger.error(f"Handler timeout for {plugin_name}.{event_name}")
                    results.append(None)
                except Exception as e:
                    # Error isolation: log and continue
                    logger.error(f"Error in event handler {plugin_name}.{event_name}: {e}")
                    results.append(None)
            
            return results
        finally:
            self._executing.discard(event_name)
    
    def get_handlers(self, event_name: str) -> List[Tuple[int, str]]:
        """
        Get list of handlers for an event (for debugging/inspection).
        
        Args:
            event_name: Name of the event.
            
        Returns:
            List of (priority, plugin_name) tuples.
        """
        return [(p, n) for p, _, n in self._handlers.get(event_name, [])]
    
    def list_events(self) -> List[str]:
        """
        Get list of all registered events (with at least one handler).
        
        Returns:
            List of event names.
        """
        return [e for e in self._handlers if len(self._handlers[e]) > 0]
    
    def clear(self) -> None:
        """Clear all event handlers (for testing)."""
        self._handlers.clear()
        self._executing.clear()
