"""Plugin Manager for Sprinkle - handles plugin lifecycle and hot-swapping."""

import asyncio
import importlib
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Type

from sprinkle.plugins.base import Plugin, DropMessage

logger = logging.getLogger(__name__)


class PluginLoadError(Exception):
    """Exception raised when a plugin fails to load."""
    pass


class PluginDependencyError(Exception):
    """Exception raised when plugin dependencies cannot be satisfied."""
    pass


class PluginManager:
    """
    Manager for loading, unloading, and managing plugins.
    
    Features:
    - Plugin registration and discovery
    - Dependency resolution and topological sort
    - Lifecycle management (load/unload)
    - Quasi hot-swapping via importlib
    - Error isolation per plugin
    
    Attributes:
        plugin_dir: Directory to scan for plugins.
        timeout: Maximum time for plugin operations.
    """
    
    def __init__(self, plugin_dir: Optional[str] = None, timeout: float = 5.0):
        """
        Initialize the plugin manager.
        
        Args:
            plugin_dir: Directory path to scan for plugins. Defaults to ./plugins.
            timeout: Maximum time in seconds for plugin operations.
        """
        self._plugin_dir = Path(plugin_dir) if plugin_dir else Path("./plugins")
        self._timeout = timeout
        
        # Registry: plugin_name -> plugin_instance
        self._plugins: Dict[str, Plugin] = {}
        
        # Registry: plugin_name -> Plugin class
        self._plugin_classes: Dict[str, Type[Plugin]] = {}
        
        # Loaded module cache for hot-swapping
        self._loaded_modules: Dict[str, Any] = {}
        
        # Event bus reference
        self._event_bus = None
        
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()
    
    @property
    def plugin_dir(self) -> Path:
        """Get plugin directory path."""
        return self._plugin_dir
    
    @property
    def plugins(self) -> Dict[str, Plugin]:
        """Get all loaded plugins."""
        return self._plugins.copy()
    
    def set_event_bus(self, event_bus) -> None:
        """
        Set the event bus for plugin communication.
        
        Args:
            event_bus: PluginEventBus instance.
        """
        self._event_bus = event_bus
    
    def register_plugin_class(self, plugin_class: Type[Plugin]) -> None:
        """
        Register a plugin class directly (without loading from file).
        
        Args:
            plugin_class: Plugin class to register.
        """
        if not issubclass(plugin_class, Plugin):
            raise TypeError(f"{plugin_class.__name__} must inherit from Plugin")
        
        name = plugin_class.name
        self._plugin_classes[name] = plugin_class
        logger.debug(f"Plugin class registered: {name}")
    
    def register_plugin_instance(self, plugin: Plugin) -> None:
        """
        Register a plugin instance directly.
        
        Args:
            plugin: Plugin instance to register.
        """
        if not isinstance(plugin, Plugin):
            raise TypeError(f"{type(plugin).__name__} must be a Plugin instance")
        
        name = plugin.name
        self._plugins[name] = plugin
        logger.debug(f"Plugin instance registered: {name}")
    
    def get_plugin(self, name: str) -> Optional[Plugin]:
        """
        Get a loaded plugin by name.
        
        Args:
            name: Plugin name.
            
        Returns:
            Plugin instance or None if not loaded.
        """
        return self._plugins.get(name)
    
    def is_loaded(self, name: str) -> bool:
        """
        Check if a plugin is loaded.
        
        Args:
            name: Plugin name.
            
        Returns:
            True if plugin is loaded.
        """
        return name in self._plugins
    
    def _resolve_dependencies(
        self, 
        plugin_classes: Dict[str, Type[Plugin]]
    ) -> List[Type[Plugin]]:
        """
        Resolve plugin dependencies using topological sort.
        
        Args:
            plugin_classes: Dictionary of plugin_name -> Plugin class.
            
        Returns:
            List of plugin classes in load order.
            
        Raises:
            PluginDependencyError: If circular dependencies detected.
        """
        # Build dependency graph and calculate in-degrees
        # in_degree[name] = number of dependencies that are in plugin_classes
        graph: Dict[str, Set[str]] = {}
        in_degree: Dict[str, int] = {}
        
        # First pass: initialize all plugins with 0 in-degree
        for name in plugin_classes:
            in_degree[name] = 0
        
        # Second pass: calculate actual in-degrees based on internal dependencies
        for name, cls in plugin_classes.items():
            graph[name] = set(cls.dependencies)
            for dep in cls.dependencies:
                if dep in in_degree:
                    in_degree[name] += 1
        
        # Kahn's algorithm for topological sort
        # Start with nodes that have no internal dependencies
        queue = [n for n in in_degree if in_degree[n] == 0]
        result = []
        
        while queue:
            node = queue.pop(0)
            result.append(node)
            
            # For each plugin that depends on this node
            for neighbor, deps in graph.items():
                if node in deps and neighbor in in_degree:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        queue.append(neighbor)
        
        # Check for circular dependencies
        if len(result) != len(plugin_classes):
            circular = set(plugin_classes.keys()) - set(result)
            raise PluginDependencyError(f"Circular dependency detected involving: {circular}")
        
        # Return classes in resolved order
        return [plugin_classes[n] for n in result if n in plugin_classes]
    
    async def _load_plugin_unlocked(
        self, 
        name: str, 
        plugin_class: Optional[Type[Plugin]] = None
    ) -> Plugin:
        """
        Internal method to load a plugin without acquiring lock.
        
        Args:
            name: Plugin name.
            plugin_class: Optional plugin class (if None, uses registered class).
            
        Returns:
            Loaded plugin instance.
            
        Raises:
            PluginLoadError: If plugin fails to load.
        """
        if name in self._plugins:
            logger.warning(f"Plugin already loaded: {name}")
            return self._plugins[name]
        
        if plugin_class is None:
            plugin_class = self._plugin_classes.get(name)
        
        if plugin_class is None:
            raise PluginLoadError(f"Plugin class not found: {name}")
        
        # Check dependencies
        for dep in plugin_class.dependencies:
            if dep not in self._plugins:
                raise PluginDependencyError(
                    f"Plugin {name} depends on {dep} which is not loaded"
                )
        
        try:
            # Instantiate and load
            plugin = plugin_class()
            plugin._do_load()
            
            self._plugins[name] = plugin
            
            # Register event handlers
            if self._event_bus is not None:
                self._register_plugin_events(plugin)
            
            logger.info(f"Plugin loaded: {name} (v{plugin.version})")
            return plugin
            
        except Exception as e:
            raise PluginLoadError(f"Failed to load plugin {name}: {e}")
    
    async def load_plugin(
        self, 
        name: str, 
        plugin_class: Optional[Type[Plugin]] = None
    ) -> Plugin:
        """
        Load a single plugin.
        
        Args:
            name: Plugin name.
            plugin_class: Optional plugin class (if None, uses registered class).
            
        Returns:
            Loaded plugin instance.
            
        Raises:
            PluginLoadError: If plugin fails to load.
        """
        async with self._lock:
            return await self._load_plugin_unlocked(name, plugin_class)
    
    def _register_plugin_events(self, plugin: Plugin) -> None:
        """
        Register plugin event handlers with the event bus.
        
        Args:
            plugin: Plugin instance.
        """
        if self._event_bus is None:
            return
        
        # This would be extended to auto-discover event handlers
        # For now, plugins manually register in on_load
        pass
    
    async def _unload_plugin_unlocked(self, name: str) -> bool:
        """
        Internal method to unload a plugin without acquiring lock.
        
        Args:
            name: Plugin name.
            
        Returns:
            True if plugin was unloaded, False if not found.
        """
        plugin = self._plugins.get(name)
        if plugin is None:
            logger.warning(f"Plugin not found for unload: {name}")
            return False
        
        # Check if other plugins depend on this
        for p_name, p in self._plugins.items():
            if name in p.dependencies:
                raise PluginDependencyError(
                    f"Cannot unload {name}: required by {p_name}"
                )
        
        try:
            # Unregister event handlers
            if self._event_bus is not None:
                self._event_bus.off_all(name)
            
            plugin._do_unload()
            del self._plugins[name]
            
            logger.info(f"Plugin unloaded: {name}")
            return True
            
        except Exception as e:
            raise PluginLoadError(f"Failed to unload plugin {name}: {e}")
    
    async def unload_plugin(self, name: str) -> bool:
        """
        Unload a plugin.
        
        Args:
            name: Plugin name.
            
        Returns:
            True if plugin was unloaded, False if not found.
        """
        async with self._lock:
            return await self._unload_plugin_unlocked(name)
    
    async def reload_plugin(self, name: str) -> Plugin:
        """
        Hot-reload a plugin (quasi hot-swapping).
        
        This reloads the module and creates a new instance.
        
        Args:
            name: Plugin name.
            
        Returns:
            Reloaded plugin instance.
        """
        async with self._lock:
            # Get old plugin to find its module
            old_plugin = self._plugins.get(name)
            if old_plugin is None:
                raise PluginLoadError(f"Plugin not loaded: {name}")
            
            # Unload first (using unlocked version since we hold the lock)
            await self._unload_plugin_unlocked(name)
            
            # Try to reload from module if it was loaded from a file
            module_name = None
            for mod_name, mod in list(sys.modules.items()):
                if hasattr(mod, name):
                    module_name = mod_name
                    break
            
            if module_name:
                try:
                    # Hot-swap the module
                    module = importlib.reload(sys.modules[module_name])
                    self._loaded_modules[module_name] = module
                    
                    # Find the plugin class in the reloaded module
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if isinstance(attr, type) and issubclass(attr, Plugin) and attr != Plugin:
                            plugin_class = attr
                            break
                    
                    # Load the new instance
                    return await self.load_plugin(name, plugin_class)
                    
                except Exception as e:
                    raise PluginLoadError(f"Failed to reload plugin {name}: {e}")
            else:
                # Fall back to just reloading the registered class
                plugin_class = self._plugin_classes.get(name)
                return await self.load_plugin(name, plugin_class)
    
    async def load_all(self) -> List[Plugin]:
        """
        Load all registered plugin classes in dependency order.
        
        Returns:
            List of loaded plugin instances.
        """
        async with self._lock:
            if not self._plugin_classes:
                return []
            
            # Resolve dependencies
            sorted_classes = self._resolve_dependencies(self._plugin_classes)
            
            loaded = []
            for cls in sorted_classes:
                try:
                    plugin = await self._load_plugin_unlocked(cls.name, cls)
                    loaded.append(plugin)
                except (PluginLoadError, PluginDependencyError) as e:
                    logger.error(f"Failed to load {cls.name}: {e}")
                    # Continue loading other plugins
            
            return loaded
    
    async def unload_all(self) -> None:
        """Unload all plugins in reverse dependency order."""
        async with self._lock:
            # Get plugins in reverse dependency order
            names = list(self._plugins.keys())
            
            for name in reversed(names):
                try:
                    await self._unload_plugin_unlocked(name)
                except Exception as e:
                    logger.error(f"Failed to unload {name}: {e}")
    
    async def discover_plugins(self, directory: Optional[Path] = None) -> int:
        """
        Discover and auto-load plugins from a directory.
        
        Looks for Python files defining Plugin subclasses.
        
        Args:
            directory: Directory to scan. Defaults to plugin_dir.
            
        Returns:
            Number of plugins discovered.
        """
        directory = directory or self._plugin_dir
        
        if not directory.exists():
            logger.warning(f"Plugin directory does not exist: {directory}")
            return 0
        
        discovered = 0
        
        for path in directory.glob("*.py"):
            if path.stem.startswith("_"):
                continue
            
            try:
                module_name = f"sprinkle_plugins.{path.stem}"
                
                # Import the module
                spec = importlib.util.spec_from_file_location(module_name, path)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                    self._loaded_modules[module_name] = module
                    
                    # Find Plugin subclasses
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if isinstance(attr, type) and issubclass(attr, Plugin) and attr != Plugin:
                            self.register_plugin_class(attr)
                            discovered += 1
                            
            except Exception as e:
                logger.error(f"Failed to discover plugins in {path}: {e}")
        
        logger.info(f"Discovered {discovered} plugins in {directory}")
        return discovered
    
    def get_plugin_info(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a plugin.
        
        Args:
            name: Plugin name.
            
        Returns:
            Dictionary with plugin info or None if not found.
        """
        plugin = self._plugins.get(name)
        if plugin is None:
            plugin_class = self._plugin_classes.get(name)
            if plugin_class:
                return {
                    "name": plugin_class.name,
                    "version": plugin_class.version,
                    "dependencies": plugin_class.dependencies,
                    "priority": plugin_class.priority,
                    "loaded": False,
                }
            return None
        
        return {
            "name": plugin.name,
            "version": plugin.version,
            "dependencies": plugin.dependencies,
            "priority": plugin.priority,
            "enabled": plugin.enabled,
            "loaded": True,
        }
    
    def list_plugins(self) -> List[Dict[str, Any]]:
        """
        Get information about all registered plugins.
        
        Returns:
            List of plugin info dictionaries.
        """
        result = []
        
        # Add loaded plugins
        for name in self._plugins:
            info = self.get_plugin_info(name)
            if info:
                result.append(info)
        
        # Add registered but not loaded plugins
        for name in self._plugin_classes:
            if name not in self._plugins:
                info = self.get_plugin_info(name)
                if info:
                    result.append(info)
        
        return result
