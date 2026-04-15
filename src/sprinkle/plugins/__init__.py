<<<<<<< HEAD
"""Plugins module - plugin system."""

__version__ = "0.1.0"
=======
"""Sprinkle Plugin System."""

from sprinkle.plugins.base import Plugin, DropMessage
from sprinkle.plugins.manager import PluginManager
from sprinkle.plugins.events import PluginEventBus

__all__ = [
    "Plugin",
    "DropMessage",
    "PluginManager",
    "PluginEventBus",
]
>>>>>>> feature/phase3-plugins
