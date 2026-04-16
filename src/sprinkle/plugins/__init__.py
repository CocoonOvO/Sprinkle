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
__version__ = "0.1.0"
