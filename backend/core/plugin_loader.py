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

    1. Class-based (legacy):
       class WeatherTool:
           def get_schema(self) -> dict: ...
           async def execute(self, arguments: dict) -> Any: ...

    2. Function-based (recommended):
       def get_weather(location: str, unit: str = "celsius") -> dict:
           '''Get weather for a location.

           Args:
               location: City name
               unit: Temperature unit
           '''
           return {"temp": 20, "unit": unit}

    Function-based tools are auto-converted to OpenAI function schemas using
    llmio.function_parser, which generates Pydantic models from type hints and
    validates arguments automatically.
    """
    
    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = Path(plugins_dir)
        self.core_plugins: Dict[str, List[Any]] = {}
        self.function_plugins: List[FunctionPlugin] = []
        self.tool_plugins: List[ToolPlugin] = []

    def load_plugins(self):
        """Load all plugins from plugins directory"""
        if not self.plugins_dir.exists():
            return

        # Load core plugins
        self._load_core_plugins()

        # Load function plugins
        self._load_function_plugins()

        # Load tool plugins
        self._load_tool_plugins()

    def _load_core_plugins(self):
        """
        Load core plugins that can override default implementations.
        
        Finds classes implementing CorePlugin protocols and groups them by type.
        """
        core_dir = self.plugins_dir / "core"
        if not core_dir.exists():
            return

        for plugin_file in core_dir.glob("*.py"):
            if plugin_file.name.startswith("__"):
                continue

            module_name = f"plugin_{plugin_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find plugin classes in module
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if hasattr(obj, 'get_priority') and not name.startswith("_"):
                    plugin_instance = obj()
                    plugin_type = name.lower().replace('plugin', '')

                    if plugin_type not in self.core_plugins:
                        self.core_plugins[plugin_type] = []

                    self.core_plugins[plugin_type].append(plugin_instance)

        # Sort plugins by priority - highest priority wins
        for plugin_type in self.core_plugins:
            self.core_plugins[plugin_type].sort(key=lambda p: p.get_priority(), reverse=True)

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
