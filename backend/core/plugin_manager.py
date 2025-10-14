"""
PluginManager provides clean access to active plugin instances.
Uses unified managers for core, tools, and functions.
"""
from typing import Dict, Any, Optional, List, Type, TypeVar, Generic
import asyncio
import logging
from .protocols import AuthPlugin, ModelPlugin, ThreadManagerPlugin, FunctionPlugin, ToolPlugin, CorePlugin
from .plugin_loader import PluginLoader
from .default_auth import DefaultAuthHandler
from .default_model_client import DefaultModelClient
from .default_thread_manager import DefaultThreadManager

logger = logging.getLogger("skeleton.plugin_manager")
T = TypeVar('T', bound=CorePlugin)

class CorePluginManager(Generic[T]):
    """Unified manager for all core plugin types"""
    
    def __init__(self, plugin_loader: PluginLoader, plugin_type: str, default_class: Type[T]):
        self.plugin_loader = plugin_loader
        self.plugin_type = plugin_type
        self.default_class = default_class
        self._active_plugin: Optional[T] = None
    
    def initialize(self):
        """Initialize with the highest priority plugin or fallback to default"""
        self._active_plugin = self.plugin_loader.get_core_plugin(self.plugin_type)
        if self._active_plugin is None:
            # Fallback to default implementation
            self._active_plugin = self.default_class()
    
    def get_plugin(self) -> T:
        """Get the active plugin instance (either overridden or default)"""
        if self._active_plugin is None:
            raise RuntimeError(f"CorePluginManager for {self.plugin_type} not initialized")
        return self._active_plugin

class FunctionPluginManager:
    """Manages function plugins that modify request context"""
    
    def __init__(self, plugin_loader: PluginLoader):
        self.plugin_loader = plugin_loader
    
    async def execute_functions(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute all function plugins in priority order"""
        final_context = context.copy()
        
        for function_plugin in self.plugin_loader.get_function_plugins():
            try:
                result = await function_plugin.execute(final_context)
                if result:
                    final_context.update(result)
            except Exception as e:
                logger.error(f"Error executing function plugin {function_plugin.get_name()}: {e}", exc_info=True)
        
        return final_context

class ToolPluginManager:
    """Manages tool plugins for OpenAI function calling"""
    
    def __init__(self, plugin_loader: PluginLoader):
        self.plugin_loader = plugin_loader
    
    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Execute a specific tool by name"""
        for tool_plugin in self.plugin_loader.get_tool_plugins():
            try:
                schema = tool_plugin.get_schema()
                if schema.get('name') == tool_name:
                    return await tool_plugin.execute(arguments)
            except Exception as e:
                logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
        
        raise ValueError(f"Tool {tool_name} not found")
    
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get all available tool schemas"""
        return self.plugin_loader.get_tool_schemas()

class PluginManager:
    """
    Main plugin manager that provides access to plugin instances.
    Other code can directly call plugin methods without proxy delegation.
    """
    
    def __init__(self):
        self.plugin_loader = PluginLoader()
        
        # Core plugin managers - other code can access these directly
        self.auth = CorePluginManager[AuthPlugin](
            self.plugin_loader, 'auth', DefaultAuthHandler
        )
        self.model = CorePluginManager[ModelPlugin](
            self.plugin_loader, 'model', DefaultModelClient
        )
        self.thread = CorePluginManager[ThreadManagerPlugin](
            self.plugin_loader, 'thread', DefaultThreadManager
        )
        
        # Function and tool managers
        self.function = FunctionPluginManager(self.plugin_loader)
        self.tool = ToolPluginManager(self.plugin_loader)
    
    def initialize(self):
        """Initialize all plugin managers"""
        self.plugin_loader.load_plugins()
        self.auth.initialize()
        self.model.initialize()
        self.thread.initialize()

# Global plugin manager instance
plugin_manager = PluginManager()
