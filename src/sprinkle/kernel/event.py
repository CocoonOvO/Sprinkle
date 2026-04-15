"""Event Bus - Plugin communication and event dispatch system."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    TypeVar,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")
Handler = Callable[..., Awaitable[Any]]
SyncHandler = Callable[..., Any]


# ============================================================================
# Event Data
# ============================================================================

@dataclass
class EventData:
    """Event data structure.
    
    Attributes:
        name: Event name (e.g., 'message.received')
        data: Event payload
        sender: Sender identifier
        timestamp: Event timestamp
        depth: Current event chain depth
        source_event: Optional reference to source event for chain tracking
    """
    name: str
    data: Any
    sender: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    depth: int = 0
    source_event: Optional[str] = None


# ============================================================================
# Event Registry
# ============================================================================

class EventRegistry:
    """Registry for event handlers.
    
    Manages registration and lookup of event handlers.
    """
    
    def __init__(self) -> None:
        # Event name -> List of (handler, priority)
        self._handlers: Dict[str, List[tuple[Handler, int]]] = defaultdict(list)
        # Event name -> wildcard handlers (e.g., 'message.*')
        self._wildcard_handlers: Dict[str, List[tuple[Handler, int]]] = defaultdict(list)
        # Handler metadata (for debugging/admin)
        self._handler_metadata: Dict[int, Dict[str, Any]] = {}
    
    def register(
        self,
        event_name: str,
        handler: Handler,
        priority: int = 50,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register an event handler.
        
        Args:
            event_name: Event name (supports wildcards like 'message.*')
            handler: Async callable to handle the event
            priority: Handler priority (0-100, higher runs first)
            metadata: Optional metadata about the handler
        """
        # Handle wildcard events
        if "*" in event_name:
            self._wildcard_handlers[event_name].append((handler, priority))
            self._wildcard_handlers[event_name].sort(key=lambda x: -x[1])
        else:
            self._handlers[event_name].append((handler, priority))
            self._handlers[event_name].sort(key=lambda x: -x[1])
        
        # Store metadata
        self._handler_metadata[id(handler)] = metadata or {}
        
        logger.debug(f"Registered handler for event '{event_name}' with priority {priority}")
    
    def unregister(self, event_name: str, handler: Handler) -> bool:
        """Unregister an event handler.
        
        Args:
            event_name: Event name
            handler: Handler to remove
            
        Returns:
            True if handler was found and removed
        """
        removed = False
        
        # Check exact match
        if event_name in self._handlers:
            handlers_list = self._handlers[event_name]
            for i, (h, _) in enumerate(handlers_list):
                if h == handler:
                    handlers_list.pop(i)
                    removed = True
                    break
        
        # Check wildcard
        if event_name in self._wildcard_handlers:
            handlers_list = self._wildcard_handlers[event_name]
            for i, (h, _) in enumerate(handlers_list):
                if h == handler:
                    handlers_list.pop(i)
                    removed = True
                    break
        
        if removed:
            self._handler_metadata.pop(id(handler), None)
            logger.debug(f"Unregistered handler for event '{event_name}'")
        
        return removed
    
    def get_handlers(self, event_name: str) -> List[Handler]:
        """Get all handlers for an event (including wildcard matches).
        
        Args:
            event_name: Event name
            
        Returns:
            List of handlers sorted by priority (highest first)
        """
        handlers = []
        
        # Add exact match handlers
        if event_name in self._handlers:
            handlers.extend([h for h, _ in self._handlers[event_name]])
        
        # Add wildcard handlers
        for pattern, handler_list in self._wildcard_handlers.items():
            if self._match_wildcard(event_name, pattern):
                handlers.extend([h for h, _ in handler_list])
        
        return handlers
    
    @staticmethod
    def _match_wildcard(event_name: str, pattern: str) -> bool:
        """Check if event name matches a wildcard pattern.
        
        Args:
            event_name: Event name to check
            pattern: Wildcard pattern (e.g., 'message.*' or '*.error')
            
        Returns:
            True if matches
        """
        import re
        # Convert wildcard pattern to regex
        regex_pattern = pattern.replace(".", r"\.").replace("*", r".*")
        regex_pattern = f"^{regex_pattern}$"
        return bool(re.match(regex_pattern, event_name))
    
    def list_events(self) -> List[str]:
        """List all registered event names.
        
        Returns:
            List of event names
        """
        events = set(self._handlers.keys())
        # Add wildcard patterns (with * expanded)
        for pattern in self._wildcard_handlers.keys():
            events.add(pattern)
        return sorted(list(events))
    
    def get_handler_count(self, event_name: str) -> int:
        """Get number of handlers for an event.
        
        Args:
            event_name: Event name
            
        Returns:
            Number of registered handlers
        """
        count = len(self._handlers.get(event_name, []))
        for pattern in self._wildcard_handlers:
            if self._match_wildcard(event_name, pattern):
                count += len(self._wildcard_handlers[pattern])
        return count


# ============================================================================
# Event Bus
# ============================================================================

class EventBus:
    """Central event bus for plugin communication.
    
    Supports synchronous and asynchronous event dispatch with:
    - Event registry for handler management
    - Loop detection (max_depth: 10)
    - Timeout protection (5s per event handler)
    
    Example:
        event_bus = EventBus()
        
        async def handle_message(event):
            print(f"Received: {event.data}")
        
        event_bus.on("message.received", handle_message)
        await event_bus.emit("message.received", data="Hello")
        
        # With loop detection
        await event_bus.emit_async("message.received", data="Hello", sender="plugin_a")
    """
    
    def __init__(
        self,
        max_depth: int = 10,
        handler_timeout: float = 5.0,
    ):
        """Initialize EventBus.
        
        Args:
            max_depth: Maximum event chain depth (default: 10)
            handler_timeout: Handler execution timeout in seconds (default: 5.0)
        """
        self._registry = EventRegistry()
        self._max_depth = max_depth
        self._handler_timeout = handler_timeout
        
        # Track event chains for loop detection
        self._event_chains: Dict[str, Set[str]] = defaultdict(set)
        
        # Error handlers
        self._error_handlers: List[Callable] = []
        
        # Event statistics
        self._stats: Dict[str, int] = defaultdict(int)
    
    @property
    def registry(self) -> EventRegistry:
        """Get the event registry."""
        return self._registry
    
    # ------------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------------
    
    def on(
        self,
        event_name: str,
        handler: Handler,
        priority: int = 50,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register an event handler (alias for registry.register).
        
        Args:
            event_name: Event name (supports wildcards like 'message.*')
            handler: Async callable to handle the event
            priority: Handler priority (0-100, higher runs first)
            metadata: Optional metadata about the handler
        """
        self._registry.register(event_name, handler, priority, metadata)
    
    def off(self, event_name: str, handler: Handler) -> bool:
        """Unregister an event handler.
        
        Args:
            event_name: Event name
            handler: Handler to remove
            
        Returns:
            True if handler was found and removed
        """
        return self._registry.unregister(event_name, handler)
    
    def once(
        self,
        event_name: str,
        handler: Handler,
        priority: int = 50,
    ) -> None:
        """Register a one-time event handler (auto-unregisters after first call).
        
        Args:
            event_name: Event name
            handler: Async callable to handle the event
            priority: Handler priority
        """
        async def wrapper(event: EventData) -> Any:
            result = await handler(event)
            self.off(event_name, wrapper)
            return result
        
        self.on(event_name, wrapper, priority)
    
    # ------------------------------------------------------------------------
    # Sync Dispatch
    # ------------------------------------------------------------------------
    
    async def emit(
        self,
        event_name: str,
        data: Any = None,
        sender: Optional[str] = None,
    ) -> List[Any]:
        """Emit an event synchronously (handlers run sequentially).
        
        Args:
            event_name: Event name
            data: Event payload
            sender: Sender identifier (for loop detection)
            
        Returns:
            List of handler results
        """
        event = EventData(
            name=event_name,
            data=data,
            sender=sender,
            depth=0,
        )
        
        return await self._dispatch(event)
    
    async def _dispatch(self, event: EventData) -> List[Any]:
        """Dispatch an event to all registered handlers.
        
        Args:
            event: Event data
            
        Returns:
            List of handler results
        """
        # Loop detection
        if self._max_depth > 0:
            chain_key = f"{event.sender}:{event.name}"
            if chain_key in self._event_chains:
                # Already processing this event chain
                logger.warning(
                    f"Event loop detected for '{event.name}' from {event.sender} "
                    f"(depth: {event.depth})"
                )
                return []
            
            self._event_chains[chain_key].add(event.name)
        
        results = []
        handlers = self._registry.get_handlers(event.name)
        
        if not handlers:
            logger.debug(f"No handlers for event '{event.name}'")
            return results
        
        logger.debug(
            f"Dispatching event '{event.name}' to {len(handlers)} handlers "
            f"(depth: {event.depth})"
        )
        
        for handler in handlers:
            # Check depth limit
            if event.depth >= self._max_depth:
                logger.warning(
                    f"Event chain max depth ({self._max_depth}) exceeded for "
                    f"'{event.name}', skipping remaining handlers"
                )
                break
            
            try:
                # Execute with timeout
                result = await asyncio.wait_for(
                    handler(event),
                    timeout=self._handler_timeout,
                )
                results.append(result)
                
                # Check if result is a new event
                if isinstance(result, EventData):
                    result.depth = event.depth + 1
                    result.source_event = event.name
                    sub_results = await self._dispatch(result)
                    results.extend(sub_results)
                
            except asyncio.TimeoutError:
                logger.error(
                    f"Handler timeout ({self._handler_timeout}s) for event "
                    f"'{event.name}'"
                )
                results.append(None)
            except Exception as e:
                logger.error(
                    f"Handler error for event '{event.name}': {e}",
                    exc_info=True
                )
                results.append(None)
                await self._handle_error(event, e)
        
        # Cleanup chain tracking
        if self._max_depth > 0:
            chain_key = f"{event.sender}:{event.name}"
            self._event_chains[chain_key].discard(event.name)
            if not self._event_chains[chain_key]:
                del self._event_chains[chain_key]
        
        # Update statistics
        self._stats[event.name] += 1
        
        return results
    
    # ------------------------------------------------------------------------
    # Async Dispatch
    # ------------------------------------------------------------------------
    
    async def emit_async(
        self,
        event_name: str,
        data: Any = None,
        sender: Optional[str] = None,
    ) -> List[Any]:
        """Emit an event asynchronously (handlers run in parallel).
        
        Args:
            event_name: Event name
            data: Event payload
            sender: Sender identifier (for loop detection)
            
        Returns:
            List of handler results
        """
        event = EventData(
            name=event_name,
            data=data,
            sender=sender,
            depth=0,
        )
        
        return await self._dispatch_async(event)
    
    async def _dispatch_async(self, event: EventData) -> List[Any]:
        """Dispatch an event asynchronously to all registered handlers.
        
        Args:
            event: Event data
            
        Returns:
            List of handler results
        """
        handlers = self._registry.get_handlers(event.name)
        
        if not handlers:
            logger.debug(f"No handlers for event '{event.name}'")
            return []
        
        logger.debug(
            f"Async dispatching event '{event.name}' to {len(handlers)} handlers "
            f"(depth: {event.depth})"
        )
        
        # Create tasks for all handlers
        tasks = []
        for handler in handlers:
            if event.depth >= self._max_depth:
                logger.warning(
                    f"Event chain max depth ({self._max_depth}) exceeded for "
                    f"'{event.name}', skipping remaining handlers"
                )
                break
            
            task = asyncio.create_task(self._execute_handler(handler, event))
            tasks.append(task)
        
        # Wait for all handlers with timeout
        if tasks:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self._handler_timeout * len(handlers),
            )
        else:
            results = []
        
        # Update statistics
        self._stats[event.name] += 1
        
        return results
    
    async def _execute_handler(
        self,
        handler: Handler,
        event: EventData,
    ) -> Any:
        """Execute a single handler with timeout and error handling.
        
        Args:
            handler: Handler to execute
            event: Event data
            
        Returns:
            Handler result or None on error
        """
        try:
            result = await asyncio.wait_for(
                handler(event),
                timeout=self._handler_timeout,
            )
            
            # Process nested events
            if isinstance(result, EventData):
                result.depth = event.depth + 1
                result.source_event = event.name
                return await self._dispatch(result)
            
            return result
            
        except asyncio.TimeoutError:
            logger.error(
                f"Handler timeout ({self._handler_timeout}s) for event "
                f"'{event.name}'"
            )
            return None
        except Exception as e:
            logger.error(
                f"Handler error for event '{event.name}': {e}",
                exc_info=True
            )
            await self._handle_error(event, e)
            return None
    
    # ------------------------------------------------------------------------
    # Error Handling
    # ------------------------------------------------------------------------
    
    def on_error(self, handler: SyncHandler) -> None:
        """Register an error handler.
        
        Args:
            handler: Sync callable that handles errors
        """
        self._error_handlers.append(handler)
    
    async def _handle_error(self, event: EventData, error: Exception) -> None:
        """Handle an error from event processing.
        
        Args:
            event: Event that caused the error
            error: The exception that was raised
        """
        for handler in self._error_handlers:
            try:
                handler(event, error)
            except Exception as e:
                logger.error(f"Error handler failed: {e}")
    
    # ------------------------------------------------------------------------
    # Statistics and Debugging
    # ------------------------------------------------------------------------
    
    def get_stats(self) -> Dict[str, int]:
        """Get event statistics.
        
        Returns:
            Dictionary of event_name -> dispatch count
        """
        return dict(self._stats)
    
    def clear_stats(self) -> None:
        """Clear event statistics."""
        self._stats.clear()
    
    def list_events(self) -> List[str]:
        """List all registered events.
        
        Returns:
            List of event names
        """
        return self._registry.list_events()


# ============================================================================
# Global Event Bus Instance
# ============================================================================

_global_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get the global EventBus instance.
    
    Returns:
        Global EventBus (creates one if not exists)
    """
    global _global_event_bus
    if _global_event_bus is None:
        _global_event_bus = EventBus()
    return _global_event_bus


def set_event_bus(bus: EventBus) -> None:
    """Set the global EventBus instance.
    
    Args:
        bus: EventBus to use as global instance
    """
    global _global_event_bus
    _global_event_bus = bus
