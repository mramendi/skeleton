"""
Dynamic PluginManager that serves as a registry for all plugins.
Provides role-based access with duck-typing validation and proper shutdown handling.
"""
from typing import Dict, Any, Optional, List, Type
import asyncio
import logging
from datetime import datetime
from openai.types.chat import ChatCompletionChunk
from .protocols import (
    AuthPlugin, ModelPlugin, ThreadManagerPlugin, StorePlugin,
    FunctionPlugin, ToolPlugin, CorePlugin, PROTOCOL_REGISTRY
)
from .plugin_loader import PluginLoader
from .yaml_file_auth import YamlFileAuthPlugin
from .default_model_client import DefaultModelClient
from .default_thread_manager import DefaultThreadManager
from .sqlite_store_plugin import SQLiteStorePlugin
from .default_message_processor import DefaultMessageProcessor
from .default_context_manager import DefaultContextManager
from .yaml_system_prompt_manager import YamlSystemPromptManager
from generator_wrapper import GeneratorWrapper

logger = logging.getLogger("skeleton.plugin_manager")

class PluginManager:
    """
    Dynamic plugin registry that manages plugins by role.

    This replaces the hardcoded CorePluginManager approach with a flexible
    registry that can handle any role defined in PROTOCOL_REGISTRY.
    """

    def __init__(self):
        self.plugin_loader = PluginLoader()

        # hand the singleton to the loader
        self.plugin_loader.inject_manager(self)   # NEW

        # Registry of active plugins by role
        self._active_core_plugins: Dict[str, CorePlugin] = {}

        # Registry of all loaded plugins for shutdown
        self._all_loaded_core_plugins: List[CorePlugin] = []

        # Default plugin classes for each role
        self._default_plugins = {
            "auth": YamlFileAuthPlugin,
            "model": DefaultModelClient,
            "thread": DefaultThreadManager,
            "store": SQLiteStorePlugin,
            "context": DefaultContextManager,
            "system_prompt": YamlSystemPromptManager,
            "message_processor": DefaultMessageProcessor,
        }

        # Function and tool managers (keep these as they are)
        self.function = FunctionPluginManager(self.plugin_loader)
        self.tool = ToolPluginManager(self.plugin_loader)

    def initialize(self):
        """Initialize all plugins and validate protocol compliance"""
        logger.info("=" * 60)
        logger.info("PluginManager: Starting dynamic initialization")
        logger.info("=" * 60)

        try:
            # Load plugins from filesystem
            logger.info("Step 1: Loading plugins from filesystem...")
            self.plugin_loader.load_plugins()
            logger.info("✓ Plugin loading completed")

            # Initialize each role from PROTOCOL_REGISTRY
            logger.info("Step 2: Initializing plugin roles...")
            for role, protocol_class in PROTOCOL_REGISTRY.items():
                logger.info(f"Initializing role: {role} (protocol: {protocol_class.__name__})")
                self._initialize_role(role, protocol_class)
            logger.info("✓ Role initialization completed")

            # Validate protocol compliance using duck typing
            logger.info("Step 3: Validating protocol compliance...")
            self._validate_protocol_compliance()
            logger.info("✓ Protocol validation completed")

            logger.info("=" * 60)
            logger.info(f"✓ PluginManager: Successfully initialized {len(self._active_core_plugins)} active plugins")
            logger.info(f"Active roles: {list(self._active_core_plugins.keys())}")
            for role, plugin in self._active_core_plugins.items():
                logger.info(f"  - {role}: {plugin.__class__.__name__}")
            logger.info("=" * 60)

        except Exception as e:
            logger.error("=" * 60)
            logger.error("✗ PluginManager initialization FAILED")
            logger.error(f"Error: {e}", exc_info=True)
            logger.error(f"Active plugins before failure: {list(self._active_core_plugins.keys())}")
            logger.error("=" * 60)
            raise

    def _initialize_role(self, role: str, protocol_class: Type[CorePlugin]):
        """Initialize plugins for a specific role"""
        logger.debug(f"Initializing role '{role}'...")

        try:
            # Try to get highest priority plugin from loader
            plugin = self.plugin_loader.get_core_plugin(role)

            if plugin is None:
                # Fallback to default implementation
                default_class = self._default_plugins.get(role)
                if default_class:
                    logger.info(f"No loaded plugin for role '{role}', using default: {default_class.__name__}")
                    plugin = default_class()
                    logger.info(f"✓ Created default {role} plugin: {plugin.__class__.__name__}")
                else:
                    error_msg = f"No plugin available for required role '{role}'. Neither a loaded plugin nor a default implementation was found."
                    logger.error(f"✗ PLUGIN INITIALIZATION FAILED: {error_msg}")
                    raise RuntimeError(error_msg)
            else:
                logger.info(f"✓ Using loaded plugin for role '{role}': {plugin.__class__.__name__}")

            # Store the active plugin
            self._active_core_plugins[role] = plugin
            self._all_loaded_core_plugins.append(plugin)

            logger.debug(f"✓ Successfully initialized {role} plugin: {plugin.__class__.__name__}")

        except Exception as e:
            logger.error(f"✗ Failed to initialize role '{role}': {e}", exc_info=True)
            raise

    def _validate_protocol_compliance(self):
        """Validate that each active plugin implements its required protocol"""
        logger.info("Validating protocol compliance for all active plugins...")

        for role, plugin in self._active_core_plugins.items():
            logger.debug(f"Validating plugin '{plugin.__class__.__name__}' for role '{role}'")

            try:
                protocol_class = PROTOCOL_REGISTRY.get(role)
                if not protocol_class:
                    raise RuntimeError(f"Role '{role}' not found in PROTOCOL_REGISTRY")

                # Debug: Log what we're checking
                logger.debug(f"  Protocol: {protocol_class.__name__}")

                # Debug: Check if plugin has required methods
                import inspect
                protocol_methods = {name for name, _ in inspect.getmembers(protocol_class, inspect.isfunction) if not name.startswith('_')}
                plugin_methods = {name for name, _ in inspect.getmembers(plugin, inspect.ismethod) or inspect.getmembers(plugin, inspect.isfunction) if not name.startswith('_')}

                logger.debug(f"  Protocol methods required: {protocol_methods}")
                logger.debug(f"  Plugin methods available: {plugin_methods}")
                missing_methods = protocol_methods - plugin_methods
                if missing_methods:
                    logger.error(f"  ✗ Missing methods: {missing_methods}")

                # Duck typing check - verify plugin implements the protocol
                if not isinstance(plugin, protocol_class):
                    plugin_class_name = plugin.__class__.__name__
                    protocol_name = protocol_class.__name__

                    error_msg = (
                        f"Plugin '{plugin_class_name}' for role '{role}' does not implement "
                        f"required protocol '{protocol_name}'. "
                        f"Plugin must implement all methods defined in {protocol_name}. "
                        f"Missing methods: {missing_methods if missing_methods else 'none (check signatures)'}"
                    )

                    logger.error(f"✗ PROTOCOL COMPLIANCE FAILED: {error_msg}")
                    raise RuntimeError(error_msg)

                logger.info(f"  ✓ Plugin '{plugin.__class__.__name__}' correctly implements {protocol_class.__name__}")

            except Exception as e:
                logger.error(f"✗ Failed to validate plugin '{plugin.__class__.__name__}' for role '{role}': {e}", exc_info=True)
                raise

        logger.info("✓ All plugins passed protocol validation")

    def get_plugin(self, role: str) -> CorePlugin:
        """Get the active plugin for a specific role"""
        if role not in self._active_core_plugins:
            raise RuntimeError(f"No plugin initialized for role '{role}'. Available roles: {list(self._active_core_plugins.keys())}")

        return self._active_core_plugins[role]

    async def shutdown(self):
        """Graceful shutdown of all loaded plugins"""
        logger.debug("PluginManager: Starting shutdown of all plugins")

        shutdown_tasks = []

        # Create shutdown tasks for all core plugins
        for plugin in self._all_loaded_core_plugins:
            if hasattr(plugin, 'shutdown'):
                shutdown_tasks.append(self._safe_shutdown(plugin))

        # Add function plugin manager shutdown
        shutdown_tasks.append(self.function.shutdown())

        # Execute all shutdowns concurrently
        if shutdown_tasks:
            try:
                await asyncio.gather(*shutdown_tasks, return_exceptions=True)
            except Exception as e:
                logger.error(f"Error during plugin shutdown: {e}")

        # Clear registries
        self._active_core_plugins.clear()
        self._all_loaded_core_plugins.clear()

        logger.debug("PluginManager: Shutdown complete")

    async def _safe_shutdown(self, plugin: CorePlugin):
        """Safely shutdown a single plugin with error handling"""
        plugin_name = plugin.__class__.__name__
        logger.debug(f"Shutting down plugin: {plugin_name}")
        await plugin.shutdown()
        logger.debug(f"Successfully shutdown plugin: {plugin_name}")



class FunctionPluginManager:
    """Manages function plugins that modify request context"""

    def __init__(self, plugin_loader: PluginLoader):
        self.plugin_loader = plugin_loader

    async def shutdown(self):
        """Shutdown all function plugins"""
        function_plugins = self.plugin_loader.get_function_plugins()
        logger.debug(f"FunctionPluginManager: Starting shutdown of {len(function_plugins)} function plugins")

        shutdown_tasks = []
        for plugin in function_plugins:
            if hasattr(plugin, 'shutdown'):
                shutdown_tasks.append(self._safe_shutdown(plugin))

        if shutdown_tasks:
            try:
                await asyncio.gather(*shutdown_tasks, return_exceptions=True)
            except Exception as e:
                logger.error(f"Error during function plugin shutdown: {e}")

        logger.debug("FunctionPluginManager: Shutdown complete")

    async def _safe_shutdown(self, plugin: FunctionPlugin):
        """Safely shutdown a single function plugin with error handling"""
        plugin_name = plugin.get_name()
        logger.debug(f"Shutting down function plugin: {plugin_name}")
        try:
            await plugin.shutdown()
            logger.debug(f"Successfully shutdown function plugin: {plugin_name}")
        except Exception as e:
            logger.error(f"Error shutting down function plugin {plugin_name}: {e}", exc_info=True)

    async def pre_call(
        self,
        user_id: str,
        thread_id: str,
        turn_correlation_id: str,
        new_message: Dict[str, Any],
        model: List[str],  # Single-element list for mutable string
        system_prompt: List[str],  # Single-element list for mutable string
        tools: List[Dict[str, Any]],
    ) -> Any:
        """
        Execute pre_call hooks for all function plugins in priority order.

        This method is a "delegating generator." It consumes the wrapped function
        (which is a coroutine or generator) and re-yields its progress, then returns
        its final value.

        This ensures that `pre_call()` *always* returns an async generator,
        creating a uniform contract for the message processor.

        It is also a Raise to Return generator, to bypass Python's ban
        on return values for async generators.
        """
        # Get all function plugins (already sorted by priority: highest first)
        function_plugins = self.plugin_loader.get_function_plugins()

        logger.debug(f"Executing pre_call hooks for {len(function_plugins)} function plugins")

        for func_plugin in function_plugins:
            try:
                logger.debug(f"Calling pre_call for function plugin: {func_plugin.get_name()}")

                # Call the plugin's pre_call method with fresh copies of immutable params
                # and original mutable params (so they can be mutated in-place)
                gen_or_coro = func_plugin.pre_call(
                    user_id=str(user_id),  # Immutable - create fresh copy each call
                    thread_id=str(thread_id),  # Immutable - create fresh copy each call
                    turn_correlation_id=str(turn_correlation_id),  # Immutable - create fresh copy each call
                    new_message=new_message,  # Mutable - pass original for in-place mutation
                    model=model,  # Mutable - pass list for in-place mutation of model[0]
                    system_prompt=system_prompt,  # Mutable - pass list for in-place mutation of system_prompt[0]
                    tools=tools,  # Mutable - pass original for in-place mutation
                )

                # Handle R2R generators - execute yields and get return value
                if gen_or_coro is not None:
                    wrapped = GeneratorWrapper(gen_or_coro)

                    # Delegate: Yield its yields... (exceptions get passed upstream)
                    async for item in wrapped.yields():
                        # Yield update messages to the caller, prepended with function name
                        yield f"{func_plugin.get_name()}: {str(item)}"

                    # Delegate: Get its return (and log it but don't use it)
                    return_value = await wrapped.returns()

                    # For pre_call, we ignore the return value as per the protocol
                    logger.debug(f"Function plugin {func_plugin.get_name()} returned: {return_value}")

                logger.debug(f"Completed pre_call for function plugin: {func_plugin.get_name()}")

            except Exception as e:
                logger.error(f"Error in pre_call for function plugin {func_plugin.get_name()}: {e}", exc_info=True)
                # Yield error to caller and continue with other plugins
                yield f"Error in function {func_plugin.get_name()}: {str(e)}"


        # Raise to return - no return value for pre_call
        raise StopAsyncIteration(None)

    async def filter_stream(
        self,
        user_id: str,
        thread_id: str,
        turn_correlation_id: str,
        chunk: ChatCompletionChunk,
    ) -> Any:
        """
        Execute filter_stream hooks for all function plugins in reverse priority order.

        This method is a "delegating generator." It consumes the wrapped function
        (which is a coroutine or generator) and re-yields its progress, then returns
        its final value.

        Lower priority plugins run first (higher priority last), so we iterate
        through the list in reverse. If any function returns None, we stop
        iterating and return None immediately.

        Returns:
            ChatCompletionChunk or None
        """
        # Get all function plugins (already sorted by priority: highest first)
        # We need to iterate in reverse for filter_stream (lowest priority first)
        function_plugins = list(reversed(self.plugin_loader.get_function_plugins()))

        logger.debug(f"Executing filter_stream hooks for {len(function_plugins)} function plugins")

        current_chunk = chunk

        for func_plugin in function_plugins:
            try:
                logger.debug(f"Calling filter_stream for function plugin: {func_plugin.get_name()}")

                # Call the plugin's filter_stream method
                gen_or_coro = func_plugin.filter_stream(
                    user_id=str(user_id),  # Immutable - create fresh copy each call
                    thread_id=str(thread_id),  # Immutable - create fresh copy each call
                    turn_correlation_id=str(turn_correlation_id),  # Immutable - create fresh copy each call
                    chunk=current_chunk,  # Pass the current chunk
                )

                # Handle R2R generators - execute yields and get return value
                if gen_or_coro is not None:
                    wrapped = GeneratorWrapper(gen_or_coro)

                    # Delegate: Yield its yields... (exceptions get passed upstream)
                    async for item in wrapped.yields():
                        # Yield update messages to the caller, prepended with function name
                        yield f"{func_plugin.get_name()}: {str(item)}"

                    # Delegate: Get its return
                    return_value = await wrapped.returns()

                    # Check if the function returned None (drop the chunk)
                    if return_value is None:
                        logger.debug(f"Function plugin {func_plugin.get_name()} returned None - dropping chunk")
                        # Set to None and break to exit loop cleanly
                        current_chunk = None
                        break
                    else:
                        # Use the returned chunk for the next function
                        current_chunk = return_value
                        logger.debug(f"Function plugin {func_plugin.get_name()} returned modified chunk")

                logger.debug(f"Completed filter_stream for function plugin: {func_plugin.get_name()}")

            except Exception as e:
                logger.error(f"Error in filter_stream for function plugin {func_plugin.get_name()}: {e}", exc_info=True)
                # Yield error to caller and continue with other plugins
                yield f"Error in function {func_plugin.get_name()}: {str(e)}"

        # Return the final chunk after all filters have been applied
        # (or None if any filter returned None)
        raise StopAsyncIteration(current_chunk)

    async def post_call(
        self,
        user_id: str,
        thread_id: str,
        turn_correlation_id: str,
        response_metadata: Dict[str, Any],
        assistant_message: Dict[str, Any],
    ) -> Any:
        """
        Execute post_call hooks for all function plugins in reverse priority order.

        This method is a "delegating generator." It consumes the wrapped function
        (which is a coroutine or generator) and re-yields its progress, then returns
        its final value.

        Lower priority plugins run first (higher priority last), so we iterate
        through the list in reverse. Returned values are ignored as per the protocol.

        response_metadata is copied for each call since it's a dict but not mutable.
        assistant_message is mutable and can be modified by plugins.
        """
        # Get all function plugins (already sorted by priority: highest first)
        # We need to iterate in reverse for post_call (lowest priority first)
        function_plugins = list(reversed(self.plugin_loader.get_function_plugins()))

        logger.debug(f"Executing post_call hooks for {len(function_plugins)} function plugins")

        for func_plugin in function_plugins:
            try:
                logger.debug(f"Calling post_call for function plugin: {func_plugin.get_name()}")

                # Call the plugin's post_call method with fresh copies of immutable params
                # and mutable params (so they can be mutated in-place)
                gen_or_coro = func_plugin.post_call(
                    user_id=str(user_id),  # Immutable - create fresh copy each call
                    thread_id=str(thread_id),  # Immutable - create fresh copy each call
                    turn_correlation_id=str(turn_correlation_id),  # Immutable - create fresh copy each call
                    response_metadata=response_metadata.copy(),  # Dict - create fresh copy each call
                    assistant_message=assistant_message,  # Mutable - pass original for in-place mutation
                )

                # Handle R2R generators - execute yields and get return value
                if gen_or_coro is not None:
                    wrapped = GeneratorWrapper(gen_or_coro)

                    # Delegate: Yield its yields... (exceptions get passed upstream)
                    async for item in wrapped.yields():
                        # Yield update messages to the caller, prepended with function name
                        yield f"{func_plugin.get_name()}: {str(item)}"

                    # Delegate: Get its return (and log it but don't use it)
                    return_value = await wrapped.returns()

                    # For post_call, we ignore the return value as per the protocol
                    logger.debug(f"Function plugin {func_plugin.get_name()} returned: {return_value}")

                logger.debug(f"Completed post_call for function plugin: {func_plugin.get_name()}")

            except Exception as e:
                logger.error(f"Error in post_call for function plugin {func_plugin.get_name()}: {e}", exc_info=True)
                # Yield error to caller and continue with other plugins
                yield f"Error in function {func_plugin.get_name()}: {str(e)}"

        # Raise to return - no return value for post_call
        raise StopAsyncIteration(None)


class ToolPluginManager:
    """Manages tool plugins for OpenAI function calling"""

    def __init__(self, plugin_loader: PluginLoader):
        self.plugin_loader = plugin_loader

    def get_tool(self, tool_name: str) -> Optional[ToolPlugin]:
        """Finds and returns a tool plugin by its name"""
        logger.debug(f"Looking for tool '{tool_name}' among {len(self.plugin_loader.get_tool_plugins())} available tools")

        available_tools = []
        for tool_plugin in self.plugin_loader.get_tool_plugins():
            try:
                schema = tool_plugin.get_schema()
                # Handle both schema formats: direct name or nested under 'function'
                if 'name' in schema:
                    tool_name_from_schema = schema.get('name')
                elif 'function' in schema and 'name' in schema['function']:
                    tool_name_from_schema = schema['function'].get('name')
                else:
                    tool_name_from_schema = None

                available_tools.append(tool_name_from_schema)

                if tool_name_from_schema == tool_name:
                    logger.debug(f"Found matching tool plugin object: {tool_plugin.__class__.__name__}")
                    return tool_plugin  # Return the plugin instance
            except Exception as e:
                logger.error(f"Error checking tool plugin: {e}", exc_info=True)
                raise

        logger.error(f"Tool '{tool_name}' not found. Available tools: {available_tools}")
        raise ValueError(f"Tool '{tool_name}' not found. Available tools: {available_tools}")

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get all available tool schemas"""
        return self.plugin_loader.get_tool_schemas()


# Global plugin manager instance
plugin_manager = PluginManager()
