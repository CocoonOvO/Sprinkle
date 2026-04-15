"""Tests for Phase 3: Plugin System."""

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sprinkle.plugins.base import Plugin, DropMessage
from sprinkle.plugins.events import PluginEventBus
from sprinkle.plugins.manager import (
    PluginManager,
    PluginLoadError,
    PluginDependencyError,
)
from sprinkle.plugins.builtin import HelloWorldPlugin, MessageLoggerPlugin
from sprinkle.kernel.message import Message


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_message() -> Message:
    """Create a sample message for testing."""
    return Message(
        conversation_id=uuid4(),
        sender_id=uuid4(),
        content="Hello, World!",
        content_type="text",
    )


@pytest.fixture
def bad_message() -> Message:
    """Create a message containing 'bad'."""
    return Message(
        conversation_id=uuid4(),
        sender_id=uuid4(),
        content="This is a bad message",
        content_type="text",
    )


@pytest.fixture
def event_bus() -> PluginEventBus:
    """Create a fresh event bus for testing."""
    bus = PluginEventBus(max_depth=5, timeout=1.0)
    yield bus
    bus.clear()


@pytest.fixture
def plugin_manager(event_bus: PluginEventBus) -> PluginManager:
    """Create a fresh plugin manager for testing."""
    manager = PluginManager(timeout=1.0)
    manager.set_event_bus(event_bus)
    return manager


# ============================================================================
# Plugin Base Tests
# ============================================================================

class TestDropMessage:
    """Tests for DropMessage exception."""
    
    def test_drop_message_can_be_raised(self):
        """Test that DropMessage can be raised."""
        with pytest.raises(DropMessage):
            raise DropMessage("Test drop")
    
    def test_drop_message_with_custom_message(self):
        """Test DropMessage with custom message."""
        msg = "Custom drop message"
        with pytest.raises(DropMessage, match=msg):
            raise DropMessage(msg)


class TestPlugin:
    """Tests for Plugin base class."""
    
    def test_plugin_has_required_attributes(self):
        """Test that Plugin has required class attributes."""
        assert hasattr(Plugin, 'name')
        assert hasattr(Plugin, 'version')
        assert hasattr(Plugin, 'dependencies')
        assert hasattr(Plugin, 'priority')
    
    def test_plugin_default_values(self):
        """Test Plugin default attribute values."""
        assert Plugin.name == "base-plugin"
        assert Plugin.version == "0.0.0"
        assert Plugin.dependencies == []
        assert Plugin.priority == 50
    
    def test_plugin_get_dependencies(self):
        """Test get_dependencies returns copy of dependencies."""
        deps = Plugin.get_dependencies()
        assert deps == []
        deps.append("new-dep")  # Should not modify class attribute
        assert Plugin.get_dependencies() == []
    
    def test_plugin_get_priority(self):
        """Test get_priority returns priority."""
        assert Plugin.get_priority() == 50
    
    def test_plugin_repr(self):
        """Test Plugin string representation."""
        class TestPlugin(Plugin):
            name = "test-plugin"
            version = "1.0.0"
        
        rep = repr(TestPlugin())
        assert "test-plugin" in rep
        assert "1.0.0" in rep


class TestPluginLifecycle:
    """Tests for Plugin lifecycle methods."""
    
    def test_plugin_initial_state(self):
        """Test plugin initial state is disabled."""
        class TestPlugin(Plugin):
            name = "test-lifecycle"
        
        plugin = TestPlugin()
        assert plugin.enabled is False
    
    def test_plugin_do_load(self):
        """Test _do_load marks plugin as enabled and calls on_load."""
        load_called = []
        
        class TestPlugin(Plugin):
            name = "test-load"
            
            def on_load(self):
                load_called.append(True)
        
        plugin = TestPlugin()
        plugin._do_load()
        
        assert plugin.enabled is True
        assert len(load_called) == 1
    
    def test_plugin_do_unload(self):
        """Test _do_unload marks plugin as disabled and calls on_unload."""
        unload_called = []
        
        class TestPlugin(Plugin):
            name = "test-unload"
            
            def __init__(self):
                super().__init__()
                self._enabled = True  # Start enabled
            
            def on_unload(self):
                unload_called.append(True)
        
        plugin = TestPlugin()
        plugin._do_unload()
        
        assert plugin.enabled is False
        assert len(unload_called) == 1
    
    def test_plugin_metadata(self):
        """Test plugin metadata storage."""
        class TestPlugin(Plugin):
            name = "test-metadata"
        
        plugin = TestPlugin()
        plugin.set_metadata("key1", "value1")
        plugin.set_metadata("key2", 42)
        
        assert plugin.get_metadata("key1") == "value1"
        assert plugin.get_metadata("key2") == 42
        assert plugin.get_metadata("nonexistent", "default") == "default"


# ============================================================================
# PluginEventBus Tests
# ============================================================================

class TestPluginEventBus:
    """Tests for PluginEventBus."""
    
    def test_event_bus_initialization(self):
        """Test event bus initialization."""
        bus = PluginEventBus(max_depth=10, timeout=5.0)
        assert bus.max_depth == 10
        assert bus.timeout == 5.0
    
    def test_event_bus_default_values(self):
        """Test event bus default values."""
        bus = PluginEventBus()
        assert bus.max_depth == 10
        assert bus.timeout == 5.0
    
    def test_on_register_handler(self, event_bus: PluginEventBus):
        """Test registering an event handler."""
        handler_called = []
        
        def handler():
            handler_called.append(True)
        
        event_bus.on("test.event", handler, "test-plugin", priority=50)
        
        handlers = event_bus.get_handlers("test.event")
        assert len(handlers) == 1
        assert handlers[0] == (50, "test-plugin")
    
    def test_off_unregister_handler(self, event_bus: PluginEventBus):
        """Test unregistering an event handler."""
        def handler():
            pass
        
        event_bus.on("test.event", handler, "test-plugin")
        result = event_bus.off("test.event", handler)
        
        assert result is True
        assert len(event_bus.get_handlers("test.event")) == 0
    
    def test_off_nonexistent_handler(self, event_bus: PluginEventBus):
        """Test off returns False for nonexistent handler."""
        def handler():
            pass
        
        result = event_bus.off("test.event", handler)
        assert result is False
    
    def test_off_all_plugin_handlers(self, event_bus: PluginEventBus):
        """Test off_all removes all handlers for a plugin."""
        def handler1():
            pass
        
        def handler2():
            pass
        
        event_bus.on("event1", handler1, "plugin-a")
        event_bus.on("event2", handler2, "plugin-a")
        
        count = event_bus.off_all("plugin-a")
        
        assert count == 2
        assert len(event_bus.list_events()) == 0
    
    def test_emit_calls_handlers(self, event_bus: PluginEventBus):
        """Test emit calls registered handlers."""
        results = []
        
        def handler1(data):
            results.append(("h1", data))
        
        def handler2(data):
            results.append(("h2", data))
        
        event_bus.on("test.event", handler1, "plugin1")
        event_bus.on("test.event", handler2, "plugin2")
        
        event_bus.emit("test.event", "test-data")
        
        assert ("h1", "test-data") in results
        assert ("h2", "test-data") in results
    
    def test_emit_respects_priority(self, event_bus: PluginEventBus):
        """Test emit calls handlers in priority order."""
        results = []
        
        def low_priority():
            results.append("low")
        
        def high_priority():
            results.append("high")
        
        event_bus.on("test.event", low_priority, "plugin-low", priority=10)
        event_bus.on("test.event", high_priority, "plugin-high", priority=100)
        
        event_bus.emit("test.event")
        
        assert results == ["high", "low"]  # High priority first
    
    def test_emit_error_isolation(self, event_bus: PluginEventBus):
        """Test emit isolates errors in handlers."""
        results = []
        
        def error_handler():
            raise RuntimeError("Handler error")
        
        def success_handler():
            results.append("success")
        
        event_bus.on("test.event", error_handler, "plugin-error")
        event_bus.on("test.event", success_handler, "plugin-ok")
        
        # Should not raise, just log error
        event_bus.emit("test.event")
        
        assert results == ["success"]
    
    def test_emit_loop_detection(self, event_bus: PluginEventBus):
        """Test emit detects and prevents loops."""
        depth_counter = []
        
        def recursive_handler(depth=0):
            depth_counter.append(depth)
            if depth < 5:  # Will exceed max_depth of 5
                event_bus.emit("test.event")
        
        event_bus.on("test.event", recursive_handler, "plugin-loop")
        
        # Should not raise RecursionError due to loop detection
        event_bus.emit("test.event")
        
        # Handler should only be called once due to loop detection
        assert len(depth_counter) == 1
    
    def test_emit_recursion_error(self, event_bus: PluginEventBus, caplog):
        """Test emit handles RecursionError gracefully when max_depth exceeded."""
        def deep_handler():
            event_bus.emit("test.event", depth=6)
        
        event_bus.on("test.event", deep_handler, "plugin-deep")
        
        # Should not raise, but should log error and return empty/partial results
        results = event_bus.emit("test.event")
        
        # Handler failed due to depth exceeded, so results may be empty or partial
        assert len(results) == 0 or results[0] is None
    
    def test_get_handlers_nonexistent_event(self, event_bus: PluginEventBus):
        """Test get_handlers returns empty list for nonexistent event."""
        handlers = event_bus.get_handlers("nonexistent")
        assert handlers == []
    
    def test_list_events(self, event_bus: PluginEventBus):
        """Test list_events returns all registered events."""
        def h1(): pass
        def h2(): pass
        
        event_bus.on("event1", h1, "plugin1")
        event_bus.on("event2", h2, "plugin2")
        
        events = event_bus.list_events()
        
        assert "event1" in events
        assert "event2" in events
    
    @pytest.mark.asyncio
    async def test_emit_async_calls_handlers(self, event_bus: PluginEventBus, sample_message):
        """Test emit_async calls async handlers."""
        async def async_handler(msg):
            return msg
        
        event_bus.on("async.event", async_handler, "async-plugin")
        
        results = await event_bus.emit_async("async.event", sample_message)
        
        assert len(results) == 1
        assert results[0] is sample_message
    
    @pytest.mark.asyncio
    async def test_emit_async_mixed_handlers(self, event_bus: PluginEventBus, sample_message):
        """Test emit_async handles mix of sync and async handlers."""
        def sync_handler(msg):
            return msg
        
        async def async_handler(msg):
            return msg
        
        event_bus.on("mixed.event", sync_handler, "sync-plugin")
        event_bus.on("mixed.event", async_handler, "async-plugin")
        
        results = await event_bus.emit_async("mixed.event", sample_message)
        
        assert len(results) == 2
        assert results[0] is sample_message
        assert results[1] is sample_message
    
    @pytest.mark.asyncio
    async def test_emit_async_error_isolation(self, event_bus: PluginEventBus, sample_message):
        """Test emit_async isolates errors in handlers."""
        async def error_handler(msg):
            raise RuntimeError("Async handler error")
        
        async def success_handler(msg):
            return msg
        
        event_bus.on("error.event", error_handler, "error-plugin")
        event_bus.on("error.event", success_handler, "success-plugin")
        
        results = await event_bus.emit_async("error.event", sample_message)
        
        assert len(results) == 2
        assert results[0] is None
        assert results[1] is sample_message
    
    def test_clear(self, event_bus: PluginEventBus):
        """Test clear removes all handlers."""
        def h1(): pass
        def h2(): pass
        
        event_bus.on("event1", h1, "plugin1")
        event_bus.on("event2", h2, "plugin2")
        
        event_bus.clear()
        
        assert len(event_bus.list_events()) == 0


# ============================================================================
# PluginManager Tests
# ============================================================================

class TestPluginManager:
    """Tests for PluginManager."""
    
    def test_manager_initialization(self):
        """Test plugin manager initialization."""
        manager = PluginManager(plugin_dir="/tmp/plugins", timeout=5.0)
        
        assert manager.plugin_dir == Path("/tmp/plugins")
        assert manager.plugins == {}
    
    def test_register_plugin_class(self, plugin_manager: PluginManager):
        """Test registering a plugin class."""
        class TestPlugin(Plugin):
            name = "test-register"
        
        plugin_manager.register_plugin_class(TestPlugin)
        
        assert "test-register" in plugin_manager._plugin_classes
    
    def test_register_plugin_instance(self, plugin_manager: PluginManager):
        """Test registering a plugin instance."""
        class TestPlugin(Plugin):
            name = "test-instance"
        
        plugin = TestPlugin()
        plugin_manager.register_plugin_instance(plugin)
        
        assert "test-instance" in plugin_manager._plugins
    
    def test_register_invalid_plugin_class(self, plugin_manager: PluginManager):
        """Test registering non-Plugin class raises error."""
        with pytest.raises(TypeError):
            plugin_manager.register_plugin_class(object)
    
    def test_get_plugin(self, plugin_manager: PluginManager):
        """Test getting a loaded plugin."""
        class TestPlugin(Plugin):
            name = "test-get"
        
        plugin = TestPlugin()
        plugin_manager.register_plugin_instance(plugin)
        
        result = plugin_manager.get_plugin("test-get")
        
        assert result is plugin
    
    def test_get_nonexistent_plugin(self, plugin_manager: PluginManager):
        """Test getting nonexistent plugin returns None."""
        result = plugin_manager.get_plugin("nonexistent")
        assert result is None
    
    def test_is_loaded(self, plugin_manager: PluginManager):
        """Test is_loaded returns correct status."""
        class TestPlugin(Plugin):
            name = "test-loaded"
        
        plugin = TestPlugin()
        plugin_manager.register_plugin_instance(plugin)
        
        assert plugin_manager.is_loaded("test-loaded") is True
        assert plugin_manager.is_loaded("nonexistent") is False
    
    def test_resolve_dependencies_simple(self, plugin_manager: PluginManager):
        """Test simple dependency resolution."""
        class PluginA(Plugin):
            name = "plugin-a"
            dependencies = []
        
        class PluginB(Plugin):
            name = "plugin-b"
            dependencies = ["plugin-a"]
        
        classes = {"plugin-a": PluginA, "plugin-b": PluginB}
        
        result = plugin_manager._resolve_dependencies(classes)
        
        assert result == [PluginA, PluginB]
    
    def test_resolve_dependencies_complex(self, plugin_manager: PluginManager):
        """Test complex dependency resolution."""
        class PluginA(Plugin):
            name = "a"
            dependencies = []
        
        class PluginB(Plugin):
            name = "b"
            dependencies = ["a"]
        
        class PluginC(Plugin):
            name = "c"
            dependencies = ["a", "b"]
        
        classes = {"a": PluginA, "b": PluginB, "c": PluginC}
        
        result = plugin_manager._resolve_dependencies(classes)
        
        assert result == [PluginA, PluginB, PluginC]
    
    def test_resolve_dependencies_circular(self, plugin_manager: PluginManager):
        """Test circular dependency detection."""
        class PluginA(Plugin):
            name = "a"
            dependencies = ["b"]
        
        class PluginB(Plugin):
            name = "b"
            dependencies = ["a"]
        
        classes = {"a": PluginA, "b": PluginB}
        
        with pytest.raises(PluginDependencyError, match="Circular dependency"):
            plugin_manager._resolve_dependencies(classes)
    
    @pytest.mark.asyncio
    async def test_load_plugin(self, plugin_manager: PluginManager):
        """Test loading a plugin."""
        class TestPlugin(Plugin):
            name = "test-load"
            
            def on_load(self):
                self._loaded = True
        
        plugin_manager.register_plugin_class(TestPlugin)
        
        loaded = await plugin_manager.load_plugin("test-load")
        
        assert loaded is not None
        assert plugin_manager.is_loaded("test-load")
    
    @pytest.mark.asyncio
    async def test_load_nonexistent_plugin(self, plugin_manager: PluginManager):
        """Test loading nonexistent plugin raises error."""
        with pytest.raises(PluginLoadError, match="Plugin class not found"):
            await plugin_manager.load_plugin("nonexistent")
    
    @pytest.mark.asyncio
    async def test_load_plugin_with_dependencies(self, plugin_manager: PluginManager):
        """Test loading plugin with satisfied dependencies."""
        class PluginA(Plugin):
            name = "dep-a"
        
        class PluginB(Plugin):
            name = "dep-b"
            dependencies = ["dep-a"]
        
        plugin_manager.register_plugin_class(PluginA)
        plugin_manager.register_plugin_class(PluginB)
        
        await plugin_manager.load_plugin("dep-a")
        loaded = await plugin_manager.load_plugin("dep-b")
        
        assert loaded.name == "dep-b"
    
    @pytest.mark.asyncio
    async def test_load_plugin_missing_dependency(self, plugin_manager: PluginManager):
        """Test loading plugin with missing dependency raises error."""
        class PluginB(Plugin):
            name = "missing-dep"
            dependencies = ["nonexistent"]
        
        plugin_manager.register_plugin_class(PluginB)
        
        with pytest.raises(PluginDependencyError, match="depends on"):
            await plugin_manager.load_plugin("missing-dep")
    
    @pytest.mark.asyncio
    async def test_unload_plugin(self, plugin_manager: PluginManager):
        """Test unloading a plugin."""
        class TestPlugin(Plugin):
            name = "test-unload"
        
        plugin_manager.register_plugin_class(TestPlugin)
        await plugin_manager.load_plugin("test-unload")
        
        result = await plugin_manager.unload_plugin("test-unload")
        
        assert result is True
        assert not plugin_manager.is_loaded("test-unload")
    
    @pytest.mark.asyncio
    async def test_unload_plugin_with_dependents(self, plugin_manager: PluginManager):
        """Test unloading plugin that others depend on raises error."""
        class PluginA(Plugin):
            name = "used-by"
        
        class PluginB(Plugin):
            name = "dependent"
            dependencies = ["used-by"]
        
        plugin_manager.register_plugin_class(PluginA)
        plugin_manager.register_plugin_class(PluginB)
        
        await plugin_manager.load_plugin("used-by")
        await plugin_manager.load_plugin("dependent")
        
        with pytest.raises(PluginDependencyError, match="required by"):
            await plugin_manager.unload_plugin("used-by")
    
    @pytest.mark.asyncio
    async def test_load_all(self, plugin_manager: PluginManager):
        """Test loading all plugins in dependency order."""
        class PluginA(Plugin):
            name = "all-a"
        
        class PluginB(Plugin):
            name = "all-b"
            dependencies = ["all-a"]
        
        class PluginC(Plugin):
            name = "all-c"
        
        plugin_manager.register_plugin_class(PluginC)
        plugin_manager.register_plugin_class(PluginB)
        plugin_manager.register_plugin_class(PluginA)
        
        loaded = await plugin_manager.load_all()
        
        assert len(loaded) == 3
        names = [p.name for p in loaded]
        # B should come after A, C can be anywhere
        assert names.index("all-a") < names.index("all-b")
    
    @pytest.mark.asyncio
    async def test_unload_all(self, plugin_manager: PluginManager):
        """Test unloading all plugins."""
        class PluginA(Plugin):
            name = "unload-a"
        
        class PluginB(Plugin):
            name = "unload-b"
            dependencies = ["unload-a"]
        
        plugin_manager.register_plugin_class(PluginA)
        plugin_manager.register_plugin_class(PluginB)
        
        await plugin_manager.load_all()
        await plugin_manager.unload_all()
        
        assert len(plugin_manager.plugins) == 0
    
    def test_get_plugin_info(self, plugin_manager: PluginManager):
        """Test getting plugin information."""
        class TestPlugin(Plugin):
            name = "info-plugin"
            version = "2.0.0"
            priority = 75
        
        plugin_manager.register_plugin_class(TestPlugin)
        
        info = plugin_manager.get_plugin_info("info-plugin")
        
        assert info is not None
        assert info["name"] == "info-plugin"
        assert info["version"] == "2.0.0"
        assert info["priority"] == 75
        assert info["loaded"] is False
    
    @pytest.mark.asyncio
    async def test_get_plugin_info_loaded(self, plugin_manager: PluginManager):
        """Test getting info for loaded plugin."""
        class TestPlugin(Plugin):
            name = "loaded-info"
        
        plugin_manager.register_plugin_class(TestPlugin)
        await plugin_manager.load_plugin("loaded-info")
        
        info = plugin_manager.get_plugin_info("loaded-info")
        
        assert info is not None
        assert info["loaded"] is True
        assert info["enabled"] is True
    
    def test_list_plugins(self, plugin_manager: PluginManager):
        """Test listing all plugins."""
        class PluginA(Plugin):
            name = "list-a"
        
        class PluginB(Plugin):
            name = "list-b"
        
        plugin_manager.register_plugin_class(PluginA)
        plugin_manager.register_plugin_class(PluginB)
        
        plugins = plugin_manager.list_plugins()
        
        assert len(plugins) == 2
        names = [p["name"] for p in plugins]
        assert "list-a" in names
        assert "list-b" in names


# ============================================================================
# Builtin Plugin Tests
# ============================================================================

class TestHelloWorldPlugin:
    """Tests for HelloWorldPlugin."""
    
    def test_hello_world_default_values(self):
        """Test HelloWorldPlugin default values."""
        assert HelloWorldPlugin.name == "hello-world"
        assert HelloWorldPlugin.version == "1.0.0"
        assert HelloWorldPlugin.dependencies == []
        assert HelloWorldPlugin.priority == 10
    
    def test_hello_world_on_load(self):
        """Test HelloWorldPlugin on_load."""
        plugin = HelloWorldPlugin()
        plugin.on_load()
        
        assert plugin.get_metadata("loaded_at") == "initialized"
    
    def test_hello_world_on_message_normal(self, sample_message):
        """Test HelloWorldPlugin handles normal messages."""
        plugin = HelloWorldPlugin()
        plugin.on_load()
        
        result = plugin.on_message(sample_message)
        
        assert result is sample_message
        assert plugin.message_count == 1
    
    def test_hello_world_on_message_drops_bad(self, bad_message):
        """Test HelloWorldPlugin drops messages with 'bad'."""
        plugin = HelloWorldPlugin()
        plugin.on_load()
        
        with pytest.raises(DropMessage):
            plugin.on_message(bad_message)
    
    def test_hello_world_on_before_send(self, sample_message):
        """Test HelloWorldPlugin on_before_send."""
        plugin = HelloWorldPlugin()
        plugin.on_load()
        
        result = plugin.on_before_send(sample_message)
        
        assert result is sample_message
        assert result.metadata.get("processed_by") == "hello-world"
    
    def test_hello_world_on_unload(self):
        """Test HelloWorldPlugin on_unload."""
        plugin = HelloWorldPlugin()
        plugin.on_load()
        plugin.on_message(Message(
            conversation_id=uuid4(),
            sender_id=uuid4(),
            content="test"
        ))
        
        plugin.on_unload()
        
        assert plugin.message_count == 1  # Preserved after unload


class TestMessageLoggerPlugin:
    """Tests for MessageLoggerPlugin."""
    
    def test_message_logger_default_values(self):
        """Test MessageLoggerPlugin default values."""
        assert MessageLoggerPlugin.name == "message-logger"
        assert MessageLoggerPlugin.version == "1.0.0"
        assert MessageLoggerPlugin.priority == 100  # High priority
    
    def test_message_logger_custom_init(self):
        """Test MessageLoggerPlugin with custom parameters."""
        plugin = MessageLoggerPlugin(
            log_incoming=False,
            log_outgoing=True,
            max_entries=500
        )
        
        assert plugin._log_incoming is False
        assert plugin._log_outgoing is True
        assert plugin._max_entries == 500
    
    def test_message_logger_on_load(self):
        """Test MessageLoggerPlugin on_load."""
        plugin = MessageLoggerPlugin()
        plugin.on_load()
        
        assert plugin.get_metadata("started_at") is not None
    
    def test_message_logger_on_message_incoming(self, sample_message):
        """Test MessageLoggerPlugin logs incoming messages."""
        plugin = MessageLoggerPlugin(log_incoming=True)
        plugin.on_load()
        
        result = plugin.on_message(sample_message)
        
        assert result is sample_message
        assert plugin.incoming_count == 1
        assert plugin.outgoing_count == 0
    
    def test_message_logger_on_message_disabled(self, sample_message):
        """Test MessageLoggerPlugin doesn't log when disabled."""
        plugin = MessageLoggerPlugin(log_incoming=False)
        plugin.on_load()
        
        plugin.on_message(sample_message)
        
        assert plugin.incoming_count == 0
    
    def test_message_logger_on_before_send(self, sample_message):
        """Test MessageLoggerPlugin logs outgoing messages."""
        plugin = MessageLoggerPlugin(log_outgoing=True)
        plugin.on_load()
        
        result = plugin.on_before_send(sample_message)
        
        assert result is sample_message
        assert plugin.outgoing_count == 1
        assert plugin.incoming_count == 0
    
    def test_message_logger_get_stats(self):
        """Test MessageLoggerPlugin get_stats."""
        plugin = MessageLoggerPlugin()
        plugin.on_load()
        
        stats = plugin.get_stats()
        
        assert "incoming_count" in stats
        assert "outgoing_count" in stats
        assert "total_count" in stats
    
    def test_message_logger_max_entries(self):
        """Test MessageLoggerPlugin respects max_entries."""
        plugin = MessageLoggerPlugin(max_entries=5)
        plugin.on_load()
        
        # Add more messages than max_entries
        for i in range(10):
            msg = Message(
                conversation_id=uuid4(),
                sender_id=uuid4(),
                content=f"Message {i}"
            )
            plugin.on_message(msg)
        
        recent = plugin.get_recent_messages()
        assert len(recent) == 5


# ============================================================================
# Integration Tests
# ============================================================================

class TestPluginSystemIntegration:
    """Integration tests for the plugin system."""
    
    @pytest.mark.asyncio
    async def test_full_plugin_lifecycle(
        self, 
        plugin_manager: PluginManager,
        event_bus: PluginEventBus,
        sample_message: Message
    ):
        """Test complete plugin lifecycle."""
        # Register plugins
        plugin_manager.register_plugin_class(HelloWorldPlugin)
        plugin_manager.register_plugin_class(MessageLoggerPlugin)
        
        # Load all
        loaded = await plugin_manager.load_all()
        
        assert len(loaded) == 2
        
        # Process message through logger (high priority)
        logger = plugin_manager.get_plugin("message-logger")
        result = logger.on_message(sample_message)
        assert result is sample_message
        
        # Process message through hello world (low priority)
        hello = plugin_manager.get_plugin("hello-world")
        result = hello.on_message(result)
        assert result is sample_message
        
        # Unload all
        await plugin_manager.unload_all()
        
        assert len(plugin_manager.plugins) == 0
    
    @pytest.mark.asyncio
    async def test_event_bus_integration(
        self,
        plugin_manager: PluginManager,
        event_bus: PluginEventBus,
        sample_message: Message
    ):
        """Test event bus integration with plugins."""
        # Register handler
        handler_called = []
        
        def test_handler(msg):
            handler_called.append(msg)
            return msg
        
        event_bus.on("test.event", test_handler, "test-plugin")
        
        # Emit event
        results = event_bus.emit("test.event", sample_message)
        
        assert len(results) == 1
        assert results[0] is sample_message


# ============================================================================
# Coverage Report Helper
# ============================================================================

def test_all_exports():
    """Test that all expected exports are available."""
    from sprinkle.plugins import Plugin, DropMessage, PluginManager, PluginEventBus
    from sprinkle.plugins.builtin import HelloWorldPlugin, MessageLoggerPlugin
    
    assert Plugin is not None
    assert DropMessage is not None
    assert PluginManager is not None
    assert PluginEventBus is not None
    assert HelloWorldPlugin is not None
    assert MessageLoggerPlugin is not None


# ============================================================================
# Additional Manager Tests for Coverage
# ============================================================================

class TestPluginManagerCoverage:
    """Additional tests to improve manager coverage."""
    
    @pytest.mark.asyncio
    async def test_load_plugin_already_loaded(self, plugin_manager: PluginManager):
        """Test loading already loaded plugin returns existing instance."""
        class TestPlugin(Plugin):
            name = "already-loaded"
        
        plugin_manager.register_plugin_class(TestPlugin)
        first = await plugin_manager.load_plugin("already-loaded")
        second = await plugin_manager.load_plugin("already-loaded")
        
        assert first is second
    
    @pytest.mark.asyncio
    async def test_reload_plugin(self, plugin_manager: PluginManager):
        """Test hot-reloading a plugin."""
        class TestPlugin(Plugin):
            name = "reload-test"
            version = "1.0.0"
        
        plugin_manager.register_plugin_class(TestPlugin)
        await plugin_manager.load_plugin("reload-test")
        
        # Note: actual module reload requires the plugin to be loaded from a module
        # This tests the error path
        with pytest.raises(PluginLoadError, match="not loaded"):
            await plugin_manager.reload_plugin("nonexistent")
    
    @pytest.mark.asyncio
    async def test_discover_plugins_nonexistent_dir(self, plugin_manager: PluginManager):
        """Test discover_plugins with nonexistent directory."""
        import tempfile
        import os
        
        nonexistent_dir = Path(tempfile.gettempdir()) / "nonexistent_sprinkle_test_12345"
        count = await plugin_manager.discover_plugins(nonexistent_dir)
        
        assert count == 0
    
    @pytest.mark.asyncio
    async def test_discover_plugins_with_py_files(self, plugin_manager: PluginManager):
        """Test discover_plugins with actual Python files."""
        import tempfile
        import os
        
        # Create a temp directory with a plugin file
        temp_dir = Path(tempfile.mkdtemp(prefix="sprinkle_test_"))
        plugin_file = temp_dir / "test_discover_plugin.py"
        plugin_file.write_text('''
from sprinkle.plugins.base import Plugin

class DiscoveredPlugin(Plugin):
    name = "discovered"
    version = "1.0.0"
''')
        
        try:
            count = await plugin_manager.discover_plugins(temp_dir)
            assert count >= 1
            
            # Check if the plugin was registered
            assert "discovered" in plugin_manager._plugin_classes
        finally:
            # Cleanup
            import shutil
            shutil.rmtree(temp_dir)
    
    @pytest.mark.asyncio
    async def test_unload_plugin_not_found(self, plugin_manager: PluginManager):
        """Test unloading nonexistent plugin returns False."""
        result = await plugin_manager.unload_plugin("nonexistent")
        assert result is False
    
    @pytest.mark.asyncio
    async def test_unload_plugin_error(self, plugin_manager: PluginManager):
        """Test unloading plugin that fails during unload."""
        class FailingPlugin(Plugin):
            name = "failing-unload"
            
            def on_unload(self):
                raise RuntimeError("Unload failed")
        
        plugin_manager.register_plugin_class(FailingPlugin)
        await plugin_manager.load_plugin("failing-unload")
        
        with pytest.raises(PluginLoadError, match="Failed to unload"):
            await plugin_manager.unload_plugin("failing-unload")
    
    @pytest.mark.asyncio
    async def test_load_plugin_error(self, plugin_manager: PluginManager):
        """Test loading plugin that fails during on_load."""
        class ErrorPlugin(Plugin):
            name = "error-load"
            
            def on_load(self):
                raise RuntimeError("Load failed")
        
        plugin_manager.register_plugin_class(ErrorPlugin)
        
        with pytest.raises(PluginLoadError, match="Failed to load"):
            await plugin_manager.load_plugin("error-load")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
