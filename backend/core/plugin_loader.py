"""
PluginLoader discovers and loads plugins from the plugins/ directory.
This is a low-level component that PluginManager uses - other code should
use PluginManager instances rather than calling PluginLoader directly.
"""
import os
import importlib.util
import inspect
import logging
from typing import Dict, List, Any, Optional, Callable
from pathlib import Path
from .protocols import CorePlugin, FunctionPlugin, ToolPlugin

try:
    from llmio.function_parser import model_from_function
    LLMIO_AVAILABLE = True
except ImportError:
    LLMIO_AVAILABLE = False
    logger = logging.getLogger("skeleton.plugin_loader")
    logger.warning("llmio.function_parser not available - function-based tools will not work")

logger = logging.getLogger("skeleton.plugin_loader")


class FunctionToolWrapper:
    """
    Wrapper that converts a plain Python function into a ToolPlugin.

    Uses llmio.function_parser.model_from_function() to:
    1. Auto-generate Pydantic model from function signature
    2. Create OpenAI function schema from the Pydantic model
    3. Validate arguments before calling the function

    Example function:
        def get_weather(location: str, unit: str = "celsius") -> dict:
            '''Get weather for a location.

            Args:
                location: City name
                unit: Temperature unit
            '''
            return {"temp": 20, "unit": unit}
    """

    def __init__(self, func: Callable):
        self.func = func
        self.func_name = func.__name__

        if not LLMIO_AVAILABLE:
            raise ImportError("llmio.function_parser is required for function-based tools")

        # Generate Pydantic model from function signature
        self.pydantic_model = model_from_function(func)

        # Generate OpenAI schema from Pydantic model
        # The model has a model_json_schema() method that returns JSON schema
        self.schema = self._generate_openai_schema()

    def _generate_openai_schema(self) -> Dict[str, Any]:
        """Generate OpenAI function schema from Pydantic model"""
        # Get JSON schema from Pydantic model
        json_schema = self.pydantic_model.model_json_schema()

        # Convert to OpenAI function schema format
        openai_schema = {
            "type": "function",
            "function": {
                "name": self.func_name,
                "description": self.func.__doc__ or f"Call {self.func_name}",
                "parameters": {
                    "type": "object",
                    "properties": json_schema.get("properties", {}),
                    "required": json_schema.get("required", [])
                }
            }
        }

        return openai_schema

    def get_schema(self) -> Dict[str, Any]:
        """Return OpenAI function schema (implements ToolPlugin protocol)"""
        return self.schema

    async def execute(self, arguments: Dict[str, Any]) -> Any:
        """
        Execute tool with arguments (implements ToolPlugin protocol).

        Validates arguments with Pydantic model before calling the function.
        """
        # Validate arguments with Pydantic model
        validated_args = self.pydantic_model(**arguments)

        # Call the function with validated arguments
        # Convert Pydantic model to dict for **kwargs unpacking
        result = self.func(**validated_args.model_dump())

        return result


class PluginLoader:
    """
    PluginLoader discovers and loads plugins from the plugins/ directory.
    
    This is a low-level plugin discovery system. For actual plugin usage,
    use the PluginManager class which provides clean interfaces to active plugins.
    
    PLUGIN DISCOVERY PROCESS:
    ------------------------
    1. Scan plugins/ subdirectories (core/, functions/, tools/)
    2. Import Python modules and find classes implementing protocols
    3. Sort plugins by priority (highest first)
    4. Return lists of plugin instances for PluginManager to use

    DIRECTORY STRUCTURE:
    -------------------
    plugins/
    ├── core/           # Override core functionality (auth, models, thread, etc.)
    │   ├── my_auth.py     # Must contain class implementing AuthPlugin
    │   ├── my_models.py   # Must contain class implementing ModelPlugin
    │   └── my_threads.py  # Must contain class implementing ThreadManagerPlugin
    ├── functions/      # Add function plugins (modify request context)
    │   └── logging.py     # Must contain class implementing FunctionPlugin
    └── tools/          # Add tool plugins (OpenAI function calling)
        ├── weather.py     # Can be a class implementing ToolPlugin (legacy)
        └── calculator.py  # Or a plain Python function with type hints (recommended)

    TOOL PLUGIN STYLES:
    ------------------
    Tools support two implementation styles:

    1. Class-based (legacy): Classes implementing ToolPlugin protocol
    2. Function-based (recommended): Plain Python functions with type hints

    Function-based tools are auto-converted using llmio.function_parser.
    """
    
    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = Path(plugins_dir)
        self.core_plugins: Dict[str, List[Any]] = {}
        self.function_plugins: List[FunctionPlugin] = []
        self.tool_plugins: List[ToolPlugin] = []

    def load_plugins(self):
        """Load all plugins from plugins directory"""
        logger.info(f"PluginLoader: Loading plugins from {self.plugins_dir}")
        
        if not self.plugins_dir.exists():
            logger.warning(f"PluginLoader: Plugins directory {self.plugins_dir} does not exist")
            return

        logger.info("PluginLoader: Directory exists, starting plugin loading...")

        # Load core plugins
        logger.info("Loading core plugins...")
        self._load_core_plugins()
        logger.info(f"Loaded {len(self.core_plugins)} core plugin types: {list(self.core_plugins.keys())}")

        # Load function plugins
        logger.info("Loading function plugins...")
        self._load_function_plugins()
        logger.info(f"Loaded {len(self.function_plugins)} function plugins")

        # Load tool plugins
        logger.info("Loading tool plugins...")
        self._load_tool_plugins()
        logger.info(f"Loaded {len(self.tool_plugins)} tool plugins")
        
        logger.info("PluginLoader: All plugin loading completed")

    def _load_core_plugins(self):
        """
        Load core plugins that can override default implementations.
        
        This method discovers and loads core plugins from the plugins/core/ directory.
        Core plugins are special plugins that can replace the default implementations
        of core system components (auth, model, thread, store).
        
        The loading process works as follows:
        1. Scan plugins/core/ directory for .py files (excluding __init__.py)
        2. For each file, dynamically import it as a Python module
        3. Inspect the module for classes that implement the CorePlugin interface
        4. Instantiate each plugin class and group them by their declared role
        5. Sort plugins within each role by priority (highest first)
        
        Plugin classes are identified by having both get_role() and get_priority() methods.
        The get_role() method declares which system component the plugin wants to replace.
        The get_priority() method determines which plugin wins when multiple plugins target the same role.
        """
        core_dir = self.plugins_dir / "core"
        if not core_dir.exists():
            logger.debug("Core plugins directory not found - no core plugins to load")
            return

        logger.info(f"Loading core plugins from: {core_dir}")

        # Step 1: Scan for Python files in the core directory
        for plugin_file in core_dir.glob("*.py"):
            # Skip special files like __init__.py
            if plugin_file.name.startswith("__"):
                logger.debug(f"Skipping special file: {plugin_file.name}")
                continue

            logger.debug(f"Processing core plugin file: {plugin_file}")

            # Step 2: Dynamically import the Python file as a module
            # Create a unique module name to avoid conflicts
            module_name = f"plugin_{plugin_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            module = importlib.util.module_from_spec(spec)
            
            try:
                # Execute the module to load its classes into memory
                spec.loader.exec_module(module)
                logger.debug(f"Successfully imported module: {module_name}")
            except Exception as e:
                logger.error(f"Failed to import module {module_name}: {e}", exc_info=True)
                continue

            # Step 3: Inspect the module for plugin classes
            for name, obj in inspect.getmembers(module, inspect.isclass):
                # Skip private classes and classes imported from other modules
                if name.startswith("_") or obj.__module__ != module.__name__:
                    continue

                logger.debug(f"Found class in module: {name}")

                # Step 4: Check if class implements CorePlugin interface
                # Use isinstance() for proper duck typing against the CorePlugin protocol
                try:
                    # Try to instantiate and check if it implements CorePlugin
                    plugin_instance = obj()
                    if isinstance(plugin_instance, CorePlugin):
                        # Get the role this plugin wants to handle
                        role = plugin_instance.get_role()
                        
                        logger.debug(f"Plugin '{name}' declares role: '{role}'")

                        # Step 5: Group plugins by their role
                        # Initialize the list for this role if it doesn't exist
                        if role not in self.core_plugins:
                            self.core_plugins[role] = []

                        # Add the plugin instance to the appropriate role group
                        self.core_plugins[role].append(plugin_instance)
                        logger.info(f"Loaded core plugin '{name}' for role '{role}' (priority: {plugin_instance.get_priority()})")

                except Exception as e:
                    logger.error(f"Failed to instantiate or validate plugin class '{name}': {e}", exc_info=True)
                else:
                    logger.debug(f"Class '{name}' does not implement CorePlugin protocol - skipping")

        # Step 6: Sort plugins within each role by priority
        # Higher priority plugins come first (will be selected as the active plugin)
        for role in self.core_plugins:
            plugins_for_role = self.core_plugins[role]
            plugins_for_role.sort(key=lambda p: p.get_priority(), reverse=True)
            
            # Log the final priority order for debugging
            plugin_names = [f"{p.__class__.__name__}({p.get_priority()})" for p in plugins_for_role]
            logger.debug(f"Core plugins for role '{role}' (sorted by priority): {plugin_names}")
            
            # Check for priority ties (non-fatal error)
            if len(plugins_for_role) > 1:
                top_priority = plugins_for_role[0].get_priority()
                top_priority_plugins = [p for p in plugins_for_role if p.get_priority() == top_priority]
                if len(top_priority_plugins) > 1:
                    tied_plugin_names = [p.__class__.__name__ for p in top_priority_plugins]
                    logger.error(
                        f"PRIORITY TIE for role '{role}': {len(top_priority_plugins)} plugins share "
                        f"the highest priority ({top_priority}): {tied_plugin_names}. "
                        f"The first one in the list will be used, but this may cause unpredictable behavior."
                    )
            
            # The first plugin in the list will be the active one
            active_plugin = plugins_for_role[0]
            logger.info(f"Selected active plugin for role '{role}': {active_plugin.__class__.__name__}")

    def _load_function_plugins(self):
        """
        Load function plugins that modify request context.
        """
        functions_dir = self.plugins_dir / "functions"
        if not functions_dir.exists():
            return

        for plugin_file in functions_dir.glob("*.py"):
            if plugin_file.name.startswith("__"):
                continue

            module_name = f"function_{plugin_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find function plugin classes
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if hasattr(obj, 'execute') and hasattr(obj, 'get_name'):
                    plugin_instance = obj()
                    self.function_plugins.append(plugin_instance)

        # Sort by priority
        self.function_plugins.sort(key=lambda p: p.get_priority(), reverse=True)

    def _load_tool_plugins(self):
        """
        Load tool plugins for OpenAI function calling.

        Supports two plugin styles:
        1. Class-based (legacy): Classes implementing ToolPlugin protocol
        2. Function-based (recommended): Plain Python functions with type hints

        Function-based tools are auto-converted using llmio.function_parser.
        """
        tools_dir = self.plugins_dir / "tools"
        if not tools_dir.exists():
            return

        for plugin_file in tools_dir.glob("*.py"):
            if plugin_file.name.startswith("__"):
                continue

            module_name = f"tool_{plugin_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find tool plugin classes (legacy class-based approach)
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if hasattr(obj, 'execute') and hasattr(obj, 'get_schema'):
                    plugin_instance = obj()
                    self.tool_plugins.append(plugin_instance)
                    logger.info(f"Loaded class-based tool plugin: {name}")

            # Find plain Python functions (function-based approach)
            for name, obj in inspect.getmembers(module, inspect.isfunction):
                # Skip private functions and imports from other modules
                if name.startswith("_") or obj.__module__ != module.__name__:
                    continue

                # Check if function has type hints (required for auto-generation)
                signature = inspect.signature(obj)
                has_type_hints = any(
                    param.annotation != inspect.Parameter.empty
                    for param in signature.parameters.values()
                )

                if has_type_hints and LLMIO_AVAILABLE:
                    try:
                        # Wrap function with FunctionToolWrapper
                        wrapped_tool = FunctionToolWrapper(obj)
                        self.tool_plugins.append(wrapped_tool)
                        logger.info(f"Loaded function-based tool plugin: {name}")
                    except Exception as e:
                        logger.error(f"Error wrapping function {name} as tool: {e}", exc_info=True)
                elif has_type_hints:
                    logger.warning(f"Function {name} has type hints but llmio.function_parser is not available")

    def get_core_plugin(self, plugin_type: str) -> Optional[Any]:
        """
        Get the highest priority core plugin of given type.
        
        Returns None if no plugins of this type are loaded.
        """
        if plugin_type in self.core_plugins and self.core_plugins[plugin_type]:
            return self.core_plugins[plugin_type][0]  # Highest priority (first in sorted list)
        return None

    def get_function_plugins(self) -> List[FunctionPlugin]:
        """Get all function plugins sorted by priority"""
        return self.function_plugins

    def get_tool_plugins(self) -> List[ToolPlugin]:
        """Get all tool plugins"""
        return self.tool_plugins

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get all tool schemas for OpenAI function calling"""
        schemas = []
        for tool in self.tool_plugins:
            try:
                schema = tool.get_schema()
                schemas.append(schema)
            except Exception as e:
                logger.error(f"Error getting schema from tool plugin: {e}", exc_info=True)
        return schemas
